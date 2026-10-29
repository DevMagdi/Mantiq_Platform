from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.serializers import notification_brief
from app.api.v1.deps import CurrentUser, DbSession, RequestsView, ScopeDep
from app.models.requests import Notification
from app.schemas.out import DashboardChannelOut, DashboardStatusOut, DashboardSummaryOut, RequestOut
from app.services import dashboard

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/summary", summary="KPI row (today, avg first response, SLA, overdue)", response_model=DashboardSummaryOut)
async def summary(session: DbSession, scope: ScopeDep, actor: RequestsView) -> dict[str, Any]:
    return await dashboard.summary(session, scope)


@router.get("/dashboard/channels", summary="Requests per channel per day", response_model=list[DashboardChannelOut])
async def channels(
    session: DbSession, scope: ScopeDep, actor: RequestsView, days: int = Query(default=7, ge=1, le=90)
) -> list[dict[str, Any]]:
    return await dashboard.channels(session, scope, days=days)


@router.get("/dashboard/status-breakdown", response_model=list[DashboardStatusOut])
async def status_breakdown(session: DbSession, scope: ScopeDep, actor: RequestsView) -> list[dict[str, Any]]:
    return await dashboard.status_breakdown(session, scope)


@router.get("/dashboard/recent-requests", response_model=list[RequestOut])
async def recent(
    session: DbSession, scope: ScopeDep, actor: RequestsView, limit: int = Query(default=8, ge=1, le=50)
) -> list[dict[str, Any]]:
    return await dashboard.recent_requests(session, scope, limit=limit)


@router.get("/dashboard/today", summary="«اليوم»: overdue SLA, lead follow-ups, installments due (A5)")
async def today(session: DbSession, scope: ScopeDep, actor: RequestsView) -> dict[str, Any]:
    return await dashboard.today_board(session, scope)


@router.get("/notifications", summary="In-app bell")
async def notifications(session: DbSession, scope: ScopeDep, actor: CurrentUser, unread_only: bool = False) -> list[dict[str, Any]]:
    stmt = select(Notification).where(Notification.user_id == actor.id).order_by(Notification.created_at.desc()).limit(50)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    return [notification_brief(r) for r in (await session.execute(stmt)).scalars()]


@router.post("/notifications/read-all")
async def read_all(session: DbSession, actor: CurrentUser) -> dict[str, int]:
    result = await session.execute(
        sa.update(Notification).where(Notification.user_id == actor.id, Notification.read_at.is_(None)).values(read_at=datetime.now(UTC))
    )
    await session.commit()
    return {"updated": int(getattr(result, "rowcount", 0) or 0)}


@router.post("/notifications/{notification_id}/read")
async def read_one(notification_id: UUID, session: DbSession, actor: CurrentUser) -> dict[str, Any]:
    result = await session.execute(
        sa.update(Notification)
        .where(Notification.id == notification_id, Notification.user_id == actor.id)
        .values(read_at=datetime.now(UTC))
    )
    await session.commit()
    return {"updated": int(getattr(result, "rowcount", 0) or 0)}
