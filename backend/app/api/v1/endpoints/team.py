"""The team tab of screen 6: add a seat, move a seat (spec: team-management round).

Gated on `leads.assign` and capped to the office roles by `app/services/team.py`; the admin surface for
users, portal links and role permission lists stays on `/users` behind `users.manage`.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.v1.deps import DbSession, ScopeDep, require_perm
from app.domain import permissions as P
from app.middleware_ip import ip_of
from app.models.auth import User
from app.schemas.common import EmailAddress
from app.schemas.out import TeamMemberCreatedOut, TeamMemberOut, TeamRosterOut
from app.services import team as svc

router = APIRouter(tags=["team"])

#: whoever may decide which desk a lead sits at may decide who sits at the desk
_Members = Annotated[User, Depends(require_perm(P.LEADS_ASSIGN))]


class MemberCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=160)
    email: EmailAddress = Field(default="")
    role_code: str = Field(min_length=2, max_length=40)
    mobile: str | None = Field(default=None, max_length=40)
    #: blank means the server generates one and returns it once
    password: str = Field(default="", max_length=200)
    is_active: bool = True


class MemberPatch(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=160)
    role_code: str | None = Field(default=None, min_length=2, max_length=40)
    mobile: str | None = Field(default=None, max_length=40)
    is_active: bool | None = None
    password: str | None = Field(default=None, max_length=200)


@router.get(
    "/sales-team/members",
    summary="Roster of the sales office, each seat's load, and the roles this screen may assign",
    response_model=TeamRosterOut,
)
async def members(session: DbSession, scope: ScopeDep, _actor: _Members) -> dict[str, Any]:
    return await svc.roster(session, scope)


@router.post("/sales-team/members", status_code=201, summary="Add a member to the sales office", response_model=TeamMemberCreatedOut)
async def add_member(body: MemberCreate, session: DbSession, scope: ScopeDep, actor: _Members) -> dict[str, Any]:  # type: ignore[valid-type]
    user, generated = await svc.create_member(session, actor, body.model_dump(), ip=ip_of())
    await session.commit()
    return {
        "id": str(user.id),
        "email": user.email,
        "role": user.role.code,
        "is_active": user.is_active,
        "temporary_password": generated,
    }


@router.patch("/sales-team/members/{user_id}", summary="Change a member's role, active flag or contact", response_model=TeamMemberOut)
async def patch_member(user_id: UUID, body: MemberPatch, session: DbSession, scope: ScopeDep, actor: _Members) -> dict[str, Any]:
    await svc.patch_member(session, actor, user_id, body.model_dump(exclude_unset=True), ip=ip_of())
    await session.commit()
    rows = await svc.list_members(session, scope)
    return next(row for row in rows if row["id"] == str(user_id))
