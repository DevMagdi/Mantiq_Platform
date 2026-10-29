"""The executive control dashboard route (R10, screen 15).

One endpoint, one permission (`reports.executive`), one shape — `ExecutiveDashboardOut`. Everything behind
it is a live query (`services.executive`); there is no snapshot table and no cached figure, because a stale
executive number is worse than a slowly loaded one.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.v1.deps import DbSession, ReportsExecutive, ScopeDep
from app.schemas.out import ExecutiveDashboardOut
from app.services import executive as svc

router = APIRouter(prefix="/executive", tags=["executive"])


@router.get(
    "/dashboard",
    summary="Executive control dashboard (every figure is a live query)",
    response_model=ExecutiveDashboardOut,
)
async def dashboard(session: DbSession, scope: ScopeDep, actor: ReportsExecutive) -> Any:
    return await svc.dashboard(session, scope)
