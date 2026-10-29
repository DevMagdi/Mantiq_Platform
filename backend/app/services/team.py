"""The sales office: who sits in it, and adding or moving a seat from the team screen (spec §team round).

Why this exists next to `services/users.py` instead of replacing it: screen 15 is the *admin* surface — it
can create a `system_admin`, wire a portal account and edit role permission lists, and it is gated on
`users.manage`, which a sales manager deliberately does not hold (ADR-016 §8). A sales manager still has to
be able to add a rep or a CS agent, so this module offers that one thing through `leads.assign` — the
permission that already means "I am accountable for who owns a lead" — and nothing else:

* the role list is capped to the four office seats (`OFFICE_ROLES`); a `system_admin` or a portal role is
  refused by the service, so this surface cannot mint an admin or a client login;
* users are created through `services.users.create_user` — one implementation of uniqueness, password
  policy, E.164 mobile and the audit row, so a member added from the team screen is indistinguishable from
  one added from screen 15;
* scopes are never touched here: an office member's data scope is the union the role implies, and this
  screen has no business narrowing it.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.core.scope import Scope
from app.domain.enums import LeadStatus
from app.models.auth import Role, User
from app.models.leads import Lead
from app.services import users as users_svc

#: the seats the sales office may add or move. `system_admin` and the two portal roles are absent on purpose.
OFFICE_ROLES: frozenset[str] = frozenset({"sales_rep", "sales_manager", "customer_service", "account_manager"})

_TERMINAL = [LeadStatus.WON, LeadStatus.LOST]


#: the order the "add a member" dialog offers the seats in. `sales_rep` first because it is the seat this
#: screen exists to add, and because a client that defaults to `roles[0]` must not default to
#: `account_manager` — which is exactly what an alphabetical list did (R3: a manager added a rep who landed
#: as an account manager, could not receive leads, and the e2e test caught the mismatch).
ROLE_ORDER: tuple[str, ...] = ("sales_rep", "sales_manager", "customer_service", "account_manager")


async def roster(session: AsyncSession, scope: Scope) -> dict[str, Any]:
    """The office seats plus the roles this screen may assign, in the order the dialog should offer them."""
    members = await list_members(session, scope)
    names = (await session.execute(select(Role.code, Role.name).where(func.lower(Role.code).in_(sorted(OFFICE_ROLES))))).all()
    rank = {code: index for index, code in enumerate(ROLE_ORDER)}
    ordered = sorted(names, key=lambda row: (rank.get(str(row[0]).lower(), len(ROLE_ORDER)), str(row[0])))
    assignable = [{"code": str(code), "name": str(name)} for code, name in ordered]
    return {"members": members, "assignable_roles": assignable}


async def list_members(session: AsyncSession, scope: Scope) -> list[dict[str, Any]]:
    """The roster of the office, with the load each seat is carrying right now."""
    rows = (
        await session.execute(
            select(User, Role.code, Role.name)
            .join(Role, Role.id == User.role_id)
            .where(func.lower(Role.code).in_(sorted(OFFICE_ROLES)), User.deleted_at.is_(None))
            .order_by(Role.code, User.full_name)
        )
    ).all()
    counts = (
        await session.execute(
            select(Lead.assigned_to, func.count(Lead.id).filter(and_(Lead.status.notin_(_TERMINAL), Lead.deleted_at.is_(None))))
            .where(Lead.assigned_to.is_not(None))
            .group_by(Lead.assigned_to)
        )
    ).all()
    loads: dict[UUID, int] = {owner: int(value or 0) for owner, value in counts if owner is not None}
    owners = await _lead_owning_roles(session)
    out: list[dict[str, Any]] = []
    for user, role_code, role_name in rows:
        out.append(
            {
                "id": str(user.id),
                "email": user.email,
                "full_name": user.full_name,
                "mobile": user.mobile,
                "role": role_code,
                "role_name": role_name,
                "is_active": user.is_active,
                "open_leads": int(loads.get(user.id, 0) or 0),
                # the redistribute picker filters on this: a customer-service seat is in the office but a
                # lead handed to it disappears from every sales board (see `leads._require_rep`)
                "accepts_leads": str(role_code).lower() in owners,
                "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
            }
        )
    return out


async def _lead_owning_roles(session: AsyncSession) -> set[str]:
    """Role codes whose permissions include `leads.view_own` — the seats that can hold a lead."""
    from app.models.auth import Permission, role_permissions

    rows = (
        await session.execute(
            select(Role.code)
            .select_from(Role)
            .join(role_permissions, role_permissions.c.role_id == Role.id)
            .join(Permission, Permission.id == role_permissions.c.permission_id)
            .where(Permission.code == "leads.view_own")
        )
    ).scalars()
    return {str(code).lower() for code in rows}


async def create_member(session: AsyncSession, actor: User, payload: dict[str, Any], *, ip: str | None = None) -> tuple[User, str | None]:
    return await users_svc.create_user(session, actor, payload, ip=ip, allowed_roles=OFFICE_ROLES)


async def patch_member(session: AsyncSession, actor: User, user_id: UUID, payload: dict[str, Any], *, ip: str | None = None) -> User:
    """Move a seat: role, active flag, name, mobile or a fresh temporary password."""
    member = await session.get(User, user_id)
    role_code = None if member is None else (await session.execute(select(Role.code).where(Role.id == member.role_id))).scalar_one_or_none()
    if member is None or member.deleted_at is not None or role_code is None or str(role_code).lower() not in OFFICE_ROLES:
        # anything that is not an office seat is answered as "not here" rather than "forbidden": the roster
        # this screen offers does not contain it, and confirming an id it cannot see would be a leak
        raise NotFound("Team member not found")
    return await users_svc.update_user(session, actor, user_id, payload, ip=ip, allowed_roles=OFFICE_ROLES)
