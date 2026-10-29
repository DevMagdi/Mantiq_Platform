"""Portal accounts: the client owner's own login, issued by a manager (spec §6, ADR-022).

A client-company owner is not a staff seat and not a "user the admin happens to create": the account only
means something when it is pinned to one client, so it is created from the client file, by someone who can
manage that client, and it always carries a `ClientPortalUser` link.

Three rules, each because of a support call we do not want:

* **the password is returned once.** A temporary password is shown in the answer of the create/reset call
  and never again — there is no read endpoint for it, and it is deliberately absent from the audit row.
* **a reset logs the owner out.** `refresh_token_version` moves with a password change, so an old session
  in another browser stops working at the next refresh instead of at the next week.
* **deactivating is not deleting.** The account row (and its history) stays; `is_active=false` refuses the
  login with a 403 the operator can read.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.scope import Scope
from app.core.security import generate_temp_password, hash_password_async
from app.models.auth import ClientPortalUser, Role, User
from app.services import audit
from app.services import users as users_svc
from app.services.clients import get as get_client

PORTAL_ROLES = ("client_owner", "client_contact")
_USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{2,62}$")


def _clean_username(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip().lower()
    if not _USERNAME.match(candidate):
        raise ValidationFailed(
            "Some fields need attention",
            details=[
                {
                    "field": "username",
                    "message": "٣ أحرف على الأقل، حروف إنجليزية صغيرة وأرقام و . _ -",
                    "type": "invalid",
                }
            ],
        )
    return candidate


async def _accounts(session: AsyncSession, client_id: UUID) -> list[User]:
    rows = (
        (
            await session.execute(
                select(User)
                .join(ClientPortalUser, ClientPortalUser.user_id == User.id)
                .where(ClientPortalUser.client_id == client_id, User.deleted_at.is_(None))
                .order_by(User.created_at)
            )
        )
        .unique()
        .scalars()
        .all()
    )
    return list(rows)


def _brief(user: User, link: ClientPortalUser | None) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "email": user.email,
        "username": user.username,
        "full_name": user.full_name,
        "role": user.role.code,
        "is_active": user.is_active,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "portal_role": link.portal_role.value if link else None,
    }


async def list_accounts(session: AsyncSession, scope: Scope, client_id: UUID) -> list[dict[str, Any]]:
    await get_client(session, client_id, scope)  # scope first: a foreign client is a 404, not an empty list
    users = await _accounts(session, client_id)
    links = {
        str(row.user_id): row
        for row in (await session.execute(select(ClientPortalUser).where(ClientPortalUser.client_id == client_id))).scalars()
    }
    return [_brief(user, links.get(str(user.id))) for user in users]


async def create_account(
    session: AsyncSession,
    actor: User,
    scope: Scope,
    client_id: UUID,
    payload: dict[str, Any],
    *,
    ip: str | None = None,
) -> dict[str, Any]:
    """Create (or re-link) one portal login for a client. Returns the temporary password exactly once."""
    client = await get_client(session, client_id, scope)
    role_code = str(payload.get("role_code") or "client_owner")
    if role_code not in PORTAL_ROLES:
        raise ValidationFailed(
            "Some fields need attention",
            details=[{"field": "role_code", "message": " / ".join(PORTAL_ROLES), "type": "invalid"}],
        )

    username = _clean_username(payload.get("username"))
    email = str(payload.get("email") or "").strip().lower()
    if not email:
        if not username:
            raise ValidationFailed(
                "Some fields need attention",
                details=[{"field": "email", "message": "بريد إلكتروني أو اسم مستخدم مطلوب", "type": "missing"}],
            )
        # a username-only account still needs a unique key: the mailbox is a local, deliverable-less handle
        email = f"{username}@portal.brokerhub.local"

    if username and (await session.execute(select(User.id).where(func.lower(User.username) == username))).scalar_one_or_none():
        raise Conflict("This username is already taken", details={"username": username})

    password = str(payload.get("password") or "")
    generated: str | None = None
    if not password:
        generated = generate_temp_password()
        password = generated
    elif len(password) < 10:
        raise ValidationFailed("Password must be at least 10 characters")

    user, _ = await users_svc.create_user(
        session,
        actor,
        {
            "email": email,
            "password": password,
            "full_name": str(payload.get("full_name") or client.name)[:160],
            "role_code": role_code,
            "portal_client_id": str(client.id),
            "username": username,
            "is_active": True,
        },
        ip=ip,
        allowed_roles=frozenset(PORTAL_ROLES),
    )
    return {
        **_brief(user, user.portal_link),
        "temporary_password": generated,
        "client_id": str(client.id),
        "client_name": client.name,
    }


async def _require_account(session: AsyncSession, client_id: UUID, user_id: UUID) -> tuple[User, ClientPortalUser]:
    link = (
        await session.execute(select(ClientPortalUser).where(ClientPortalUser.user_id == user_id, ClientPortalUser.client_id == client_id))
    ).scalar_one_or_none()
    user = await session.get(User, user_id)
    if link is None or user is None or user.deleted_at is not None:
        raise NotFound("Portal account not found")  # also the answer for another client's account
    return user, link


async def reset_password(
    session: AsyncSession,
    actor: User,
    scope: Scope,
    client_id: UUID,
    user_id: UUID,
    payload: dict[str, Any],
    *,
    ip: str | None = None,
) -> dict[str, Any]:
    await get_client(session, client_id, scope)
    user, _link = await _require_account(session, client_id, user_id)
    password = str(payload.get("password") or "")
    generated: str | None = None
    if not password:
        generated = generate_temp_password()
        password = generated
    elif len(password) < 10:
        raise ValidationFailed("Password must be at least 10 characters")
    user.password_hash = await hash_password_async(password)
    user.refresh_token_version += 1  # an old session elsewhere dies at its next refresh
    await session.flush()
    await audit.write(
        session,
        actor=actor,
        action="portal.password_reset",
        entity_type="user",
        entity_id=user.id,
        after={"client_id": str(client_id), "generated": generated is not None},
        ip=ip,
    )
    return {"id": str(user.id), "username": user.username, "temporary_password": generated}


async def set_active(
    session: AsyncSession,
    actor: User,
    scope: Scope,
    client_id: UUID,
    user_id: UUID,
    *,
    is_active: bool,
    ip: str | None = None,
) -> dict[str, Any]:
    await get_client(session, client_id, scope)
    user, link = await _require_account(session, client_id, user_id)
    user.is_active = is_active
    if not is_active:
        user.refresh_token_version += 1
    await session.flush()
    await audit.write(
        session,
        actor=actor,
        action="portal.account_active",
        entity_type="user",
        entity_id=user.id,
        after={"is_active": is_active, "client_id": str(client_id)},
        ip=ip,
    )
    return _brief(user, link)


async def update_username(
    session: AsyncSession, actor: User, scope: Scope, client_id: UUID, user_id: UUID, username: str | None, *, ip: str | None = None
) -> dict[str, Any]:
    await get_client(session, client_id, scope)
    user, link = await _require_account(session, client_id, user_id)
    cleaned = _clean_username(username)
    taken = (await session.execute(select(User.id).where(func.lower(User.username) == cleaned, User.id != user.id))).scalar_one_or_none()
    if cleaned and taken:
        raise Conflict("This username is already taken", details={"username": cleaned})
    user.username = cleaned
    await session.flush()
    await audit.write(
        session,
        actor=actor,
        action="portal.username_changed",
        entity_type="user",
        entity_id=user.id,
        after={"username": cleaned, "client_id": str(client_id)},
        ip=ip,
    )
    return _brief(user, link)


_ = Role
