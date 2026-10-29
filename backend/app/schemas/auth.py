"""Auth wire models. The frontend consumes these through the generated OpenAPI types only."""

from __future__ import annotations

from pydantic import BaseModel


class ProfileOut(BaseModel):
    """The signed-in principal: identity, role, effective permissions and data scope."""

    id: str
    email: str
    full_name: str
    role: str
    role_name: str
    permissions: list[str]
    is_active: bool
    mfa_enabled: bool
    last_login_at: str | None = None


class MeOut(ProfileOut):
    scope_mode: str
    client_ids: list[str] = []
    portal_client_id: str | None = None


class LoginOut(BaseModel):
    """`user` is the *same* shape as `GET /auth/me`, so the client never has to refetch to route."""

    access_token: str
    expires_in: int
    token_type: str = "bearer"
    user: MeOut


class RefreshOut(BaseModel):
    access_token: str
    expires_in: int
    token_type: str = "bearer"
    user: MeOut


class ErrorResponse(BaseModel):
    """Uniform error envelope for every non-2xx response (spec §7.2)."""

    code: str
    message: str
    details: object | None = None
