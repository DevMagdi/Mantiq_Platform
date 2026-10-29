from __future__ import annotations

from typing import Literal, cast

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.deps import REFRESH_COOKIE, CurrentUser, DbSession
from app.core.errors import Forbidden
from app.schemas.auth import LoginOut, MeOut, RefreshOut
from app.services import auth

router = APIRouter(tags=["auth"])


def _samesite(value: str) -> Literal["lax", "strict", "none"]:
    """Settings are a plain string; Starlette wants a Literal."""
    return cast(Literal["lax", "strict", "none"], value if value in {"lax", "strict", "none"} else "lax")


class LoginIn(BaseModel):
    #: an e-mail address, or a portal username — resolved against both columns in `services.auth.login`.
    #: The field keeps the name `email` because every client, test and spec already sends it.
    email: str = Field(min_length=1, max_length=255, description="e-mail or portal username")
    username: str | None = Field(default=None, min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=200)
    totp_code: str | None = Field(default=None, min_length=6, max_length=6)
    remember_me: bool = False


def _profile_with_scope(user: object) -> dict[str, object]:
    """Public profile + the data scope, identical for login / refresh / me."""
    from app.core.scope import build_scope
    from app.services import auth as auth_svc

    profile = dict(auth_svc.public_profile(user))  # type: ignore[arg-type]
    scope = build_scope(user)  # type: ignore[arg-type]
    profile["scope_mode"] = scope.mode
    profile["client_ids"] = [str(client_id) for client_id in scope.client_ids]
    profile["portal_client_id"] = str(scope.portal_client_id) if scope.portal_client_id else None
    return profile


@router.post("/login", summary="Password login (sets the rotating refresh cookie)", response_model=LoginOut)
async def login(body: LoginIn, request: Request, response: Response, session: DbSession) -> dict[str, object]:
    from app.core.middleware import client_ip

    settings = get_settings()
    user, tokens = await auth.login(session, body.username or body.email, body.password, ip=client_ip(request, settings.TRUST_HOPS))
    if user.mfa_enabled and not auth.verify_mfa_sync(user, body.totp_code or ""):
        raise Forbidden("Two-factor code required", details={"mfa_required": True})
    await session.commit()
    response.set_cookie(
        REFRESH_COOKIE,
        tokens.refresh_token,
        max_age=settings.REFRESH_TOKEN_TTL_DAYS * 86400 if body.remember_me else None,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=_samesite(settings.COOKIE_SAMESITE),
        path="/api/v1/auth",
    )
    return {
        "access_token": tokens.access_token,
        "expires_in": tokens.access_ttl_seconds,
        "token_type": "bearer",
        "user": _profile_with_scope(user),
    }


@router.post("/refresh", summary="Rotate the refresh token", response_model=RefreshOut)
async def refresh(request: Request, response: Response, session: DbSession) -> dict[str, object]:
    token = request.cookies.get(REFRESH_COOKIE) or ""
    if not token:
        raise Forbidden("No refresh token")
    user, tokens = await auth.refresh(session, token)
    await session.commit()
    response.set_cookie(
        REFRESH_COOKIE,
        tokens.refresh_token,
        httponly=True,
        secure=get_settings().COOKIE_SECURE,
        samesite=_samesite(get_settings().COOKIE_SAMESITE),
        path="/api/v1/auth",
    )
    return {"access_token": tokens.access_token, "expires_in": tokens.access_ttl_seconds, "user": _profile_with_scope(user)}


@router.post("/logout", status_code=204, summary="Invalidate the refresh family")
async def logout(user: CurrentUser, response: Response, session: DbSession) -> None:
    await auth.logout(session, user)
    await session.commit()
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth")


@router.get("/me", summary="Current principal, permissions and scope mode", response_model=MeOut)
async def me(user: CurrentUser, session: DbSession) -> dict[str, object]:

    return _profile_with_scope(user)


@router.get("/audit/mine", summary="My recent audit entries")
async def my_audit(user: CurrentUser, session: DbSession) -> list[dict[str, object]]:
    from sqlalchemy import select

    from app.models.requests import AuditLog

    rows = (
        (await session.execute(select(AuditLog).where(AuditLog.actor_id == user.id).order_by(AuditLog.occurred_at.desc()).limit(100)))
        .scalars()
        .all()
    )
    return [
        {
            "id": str(r.id),
            "action": r.action,
            "entity_type": r.entity_type,
            "entity_id": str(r.entity_id) if r.entity_id else None,
            "occurred_at": r.occurred_at.isoformat(),
        }
        for r in rows
    ]
