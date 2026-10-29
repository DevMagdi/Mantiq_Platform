from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.deps import DbSession, ScopeDep, require_perm
from app.core.errors import NotFound
from app.domain import permissions as P
from app.middleware_ip import ip_of
from app.models.auth import User
from app.models.masters import FeatureFlag
from app.schemas.common import EmailAddress
from app.schemas.out import FeatureFlagOut, PermissionOut, RoleOut, UserCreatedOut, UserOut
from app.services import audit
from app.services import users as svc

router = APIRouter(tags=["users & roles"])

_Manage = Annotated[User, Depends(require_perm(P.USERS_MANAGE))]
_Settings = Annotated[User, Depends(require_perm(P.SETTINGS_MANAGE))]


class UserCreate(BaseModel):
    email: EmailAddress = Field(default="")
    #: blank means "generate one" and the answer carries it back once (screen 15's «توليد تلقائي»)
    password: str = Field(default="", max_length=200)
    full_name: str = Field(min_length=2, max_length=160)
    role_code: str
    mobile: str | None = None
    is_active: bool = True
    portal_client_id: UUID | None = None
    scopes: list[dict[str, str]] = Field(default_factory=list)


class UserPatch(BaseModel):
    full_name: str | None = None
    role_code: str | None = None
    mobile: str | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=10, max_length=200)


class FeatureFlagPatch(BaseModel):
    enabled: bool


class ScopesIn(BaseModel):
    scopes: list[dict[str, str]] = Field(default_factory=list, description="[{'type':'client'|'insurer','id':uuid}]")


class RolePermissionsIn(BaseModel):
    permissions: list[str]


@router.get("/users", response_model=list[UserOut])
async def list_users(session: DbSession, scope: ScopeDep, user: _Manage) -> list[dict[str, Any]]:
    return await svc.list_users(session, scope)


@router.post("/users", status_code=201, response_model=UserCreatedOut)
async def create_user(body: UserCreate, session: DbSession, user: _Manage) -> dict[str, Any]:  # type: ignore[valid-type]
    created, generated = await svc.create_user(session, user, body.model_dump(), ip=ip_of())
    await session.commit()
    return {"id": str(created.id), "email": created.email, "role": created.role.code, "temporary_password": generated}


@router.patch("/users/{user_id}")
async def patch_user(user_id: UUID, body: UserPatch, session: DbSession, actor: _Manage) -> dict[str, Any]:
    updated = await svc.update_user(session, actor, user_id, body.model_dump(exclude_unset=True))
    await session.commit()
    return {"id": str(updated.id), "role": updated.role.code, "is_active": updated.is_active}


@router.put("/users/{user_id}/scopes")
async def put_scopes(user_id: UUID, body: ScopesIn, session: DbSession, actor: _Manage) -> dict[str, int]:
    if await session.get(User, user_id) is None:
        raise NotFound("User not found")
    await svc.set_scopes(session, actor, user_id, body.scopes)
    await session.commit()
    return {"updated": len(body.scopes)}


@router.get("/roles", response_model=list[RoleOut])
async def roles(session: DbSession, _scope: ScopeDep, user: _Manage) -> list[dict[str, Any]]:
    return await svc.roles_with_permissions(session)


@router.put("/roles/{role_id}/permissions")
async def put_role_permissions(role_id: UUID, body: RolePermissionsIn, session: DbSession, actor: _Manage) -> dict[str, Any]:
    role = await svc.set_role_permissions(session, actor, role_id, body.permissions)
    await session.commit()
    return {"id": str(role.id), "code": role.code, "permissions": sorted(p.code for p in role.permissions)}


@router.get("/permissions", response_model=list[PermissionOut])
async def permissions(session: DbSession, _user: _Manage) -> list[dict[str, Any]]:
    return await svc.permissions(session)


@router.get("/feature-flags", response_model=list[FeatureFlagOut])
async def feature_flags(session: DbSession, _scope: ScopeDep) -> list[dict[str, Any]]:
    rows = (await session.execute(select(FeatureFlag).order_by(FeatureFlag.key))).scalars().all()
    return [{"key": flag.key, "enabled": flag.enabled, "description": flag.description} for flag in rows]


@router.patch("/feature-flags/{key}", summary="Toggle a feature flag (admins only, audited)", response_model=FeatureFlagOut)
async def patch_feature_flag(key: str, body: FeatureFlagPatch, session: DbSession, actor: _Settings) -> dict[str, Any]:
    flag = (await session.execute(select(FeatureFlag).where(FeatureFlag.key == key))).scalar_one_or_none()
    if flag is None:
        raise NotFound("Unknown feature flag")
    flag.enabled = body.enabled
    await audit.write(
        session,
        actor=actor,
        action="feature_flag.update",
        entity_type="feature_flag",
        entity_id=flag.id,
        after={"key": flag.key, "enabled": flag.enabled},
    )
    await session.commit()
    return {"key": flag.key, "enabled": flag.enabled}
