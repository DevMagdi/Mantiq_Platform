"""Login / refresh rotation / lockout (spec §6)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import Forbidden, TooManyRequests, Unauthorized
from app.core.security import (
    decode_token,
    hash_password_async,
    make_access_token,
    make_refresh_token,
    needs_rehash,
    totp_matches,
    verify_password_async,
)
from app.domain import permissions as P
from app.models.auth import Role, User
from app.services import audit

_ATTEMPTS: dict[str, tuple[int, float]] = {}  # email -> (count, unlocked_at) — Redis-backed in prod


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: str
    access_ttl_seconds: int


def _rate_key(email: str, ip: str | None) -> str:
    return f"{email.lower()}|{ip or 'local'}"


def _check_lockout(key: str) -> None:
    count, unlocked_at = _ATTEMPTS.get(key, (0, 0.0))
    if count >= get_settings().LOGIN_MAX_ATTEMPTS:
        if time.time() < unlocked_at:
            raise TooManyRequests(
                "Too many failed attempts — temporarily locked",
                details={"retry_after_seconds": int(unlocked_at - time.time())},
            )
        _ATTEMPTS.pop(key, None)


def _register_failure(key: str) -> None:
    settings = get_settings()
    count, _ = _ATTEMPTS.get(key, (0, 0.0))
    _ATTEMPTS[key] = (count + 1, time.time() + settings.LOGIN_LOCKOUT_SECONDS)


def clear_failures(key: str) -> None:
    _ATTEMPTS.pop(key, None)


def reset_rate_limits() -> None:
    """Test/admin hook: drop every lockout counter (the store is process-local in dev)."""
    _ATTEMPTS.clear()


async def login(session: AsyncSession, identifier: str, password: str, *, ip: str | None = None) -> tuple[User, Tokens]:
    settings = get_settings()
    key = _rate_key(identifier, ip)
    _check_lockout(key)

    # One identifier, two columns: staff sign in with the e-mail the manager typed, portal owners may
    # sign in with the username printed on their onboarding note. Both land on the same user row.
    handle = identifier.strip().lower()
    user = (
        await session.execute(
            select(User).where(or_(func.lower(User.email) == handle, func.lower(func.coalesce(User.username, "")) == handle))
        )
    ).scalar_one_or_none()
    if user is None:
        _register_failure(key)
        raise Unauthorized("Email or password is incorrect")
    # Release the pooled connection *before* the expensive hash. SQLAlchemy keeps a connection checked out
    # from the first statement until the transaction ends, and Argon2 costs ~110 ms of CPU per verify plus
    # whatever queue a burst puts in front of it — so a login burst used to sit on the whole pool while it
    # was only *waiting*. R8 measured the result: 25 concurrent users + a 10 logins/s arrival rate drew
    # `QueuePool limit of size 10 overflow 20 reached, connection timed out, timeout 30.00` and two
    # `POST /auth/login` answers of 500 after 19–35 s. `expire_on_commit=False` (core/db.py) keeps `user`
    # usable after the commit, and the audit row below re-acquires a connection when it writes.
    await session.commit()
    # the hash check runs in a worker thread: 110 ms of Argon2 on the event loop is 110 ms in which no
    # other request in this process can even be read (see `security.hash_password_async`)
    if not await verify_password_async(password, user.password_hash):
        _register_failure(key)
        raise Unauthorized("Email or password is incorrect")
    if not user.is_active or user.deleted_at is not None:
        raise Forbidden("Account is disabled")
    clear_failures(key)

    if needs_rehash(user.password_hash):
        user.password_hash = await hash_password_async(password)

    from datetime import UTC, datetime

    user.last_login_at = datetime.now(UTC)
    tokens = issue_tokens(user)
    await audit.write(
        session,
        actor=user,
        action="auth.login",
        entity_type="user",
        entity_id=user.id,
        after={"email": user.email},
        ip=ip,
    )
    _ = settings
    return user, tokens


def issue_tokens(user: User) -> Tokens:
    perms = tuple(sorted(user.permission_codes))
    if user.is_portal_only:
        perms = (*perms, P.PORTAL_ACCESS)
    return Tokens(
        access_token=make_access_token(user.id, perms),
        refresh_token=make_refresh_token(user.id, user.refresh_token_version),
        access_ttl_seconds=get_settings().ACCESS_TOKEN_TTL_MIN * 60,
    )


async def refresh(session: AsyncSession, refresh_token: str) -> tuple[User, Tokens]:
    try:
        claims = decode_token(refresh_token, "refresh")
    except Exception as exc:
        raise Unauthorized("Refresh token is invalid or expired") from exc
    user = await session.get(User, UUID(str(claims["sub"])))
    if user is None or not user.is_active or user.deleted_at is not None:
        raise Unauthorized("Account is inactive")
    if int(claims.get("ver", -1)) != user.refresh_token_version:
        # Reuse of a rotated token: kill the family (spec §6 "تدوير وإبطال").
        user.refresh_token_version += 1
        await session.flush()
        raise Unauthorized("Refresh token was already used — please sign in again")
    user.refresh_token_version += 1  # rotation invalidates every previously issued refresh token
    await session.flush()
    return user, issue_tokens(user)


async def logout(session: AsyncSession, user: User | None) -> None:
    if user is not None:
        user.refresh_token_version += 1
        await session.flush()


def verify_mfa_sync(user: User, code: str) -> bool:
    if not user.mfa_enabled or not user.totp_secret_encrypted:
        return True
    return totp_matches(user.totp_secret_encrypted, code)


def public_profile(user: User) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role.code,
        "role_name": user.role.name,
        "permissions": sorted(user.permission_codes),
        "is_active": user.is_active,
        "mfa_enabled": user.mfa_enabled,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


async def role_by_code(session: AsyncSession, code: str) -> Role | None:
    return (await session.execute(select(Role).where(Role.code == code))).scalar_one_or_none()
