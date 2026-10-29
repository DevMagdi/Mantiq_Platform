"""Service-desk queries (module named `requests_repo` so it never shadows the HTTP `requests` lib)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.pagination import PageParams
from app.core.scope import Scope
from app.domain.enums import RequestStatus
from app.models.requests import Attachment, CallRecord, InternalNote, RequestMessage, ServiceRequest
from app.repositories.scope_filters import with_scope

_OPEN = [RequestStatus.NEW, RequestStatus.IN_PROGRESS, RequestStatus.WAITING_INSURER]


def overdue_condition() -> sa.ColumnElement[bool]:
    """The single definition of «متأخر»: the SLA was missed **and** the request is still open.

    The dashboard KPI, the status breakdown and the `only_overdue` list filter all read this function. They
    used to carry three copies of the same two clauses, and the list's copy forgot the status half — so the
    KPI said «7 متأخرة» and the screen it linked to showed 9, including two that were resolved months ago.
    A deep link whose count disagrees with its card is worse than no deep link.
    """
    return sa.and_(
        ServiceRequest.is_overdue.is_(True),
        ServiceRequest.status.notin_([RequestStatus.RESOLVED, RequestStatus.CLOSED]),
    )


_SORTABLE = {
    "last_activity_at": ServiceRequest.last_activity_at,
    "created_at": ServiceRequest.created_at,
    "sla_due_at": ServiceRequest.sla_due_at,
    "number": ServiceRequest.number,
}


def _base() -> Select[Any]:
    return select(ServiceRequest).options(
        selectinload(ServiceRequest.client),
        selectinload(ServiceRequest.insured_person),
        selectinload(ServiceRequest.insurer),
        selectinload(ServiceRequest.insurance_type),
        selectinload(ServiceRequest.assignee),
    )


def _list_base() -> Select[Any]:
    """List pages: many-to-one relations as JOINs in the page query itself, not five extra
    selectin round-trips per page (perf gate, product-score8 follow-up).

    The brief serializer reads only names off the joined rows, so the eager collections
    hanging off them (client contacts, the assignee's scopes/permissions/portal link, the
    client's account owner chain) are `noload`-ed: without this they fired one selectin
    query per relation per page for data the list never renders."""
    from sqlalchemy.orm import joinedload

    from app.models.auth import Role, User
    from app.models.clients import Client

    return select(ServiceRequest).options(
        joinedload(ServiceRequest.client).noload(Client.contacts),
        joinedload(ServiceRequest.client).noload(Client.account_owner),
        joinedload(ServiceRequest.client).noload(Client.source_lead),
        joinedload(ServiceRequest.insured_person),
        joinedload(ServiceRequest.insurer),
        joinedload(ServiceRequest.insurance_type),
        joinedload(ServiceRequest.assignee).noload(User.scopes),
        joinedload(ServiceRequest.assignee).noload(User.portal_link),
        joinedload(ServiceRequest.assignee).joinedload(User.role).noload(Role.permissions),
    )


async def get_by_id(session: AsyncSession, request_id: UUID, scope: Scope) -> ServiceRequest | None:
    return (await session.execute(with_scope(_base().where(ServiceRequest.id == request_id), scope, "request"))).scalar_one_or_none()


async def list_page(
    session: AsyncSession,
    scope: Scope,
    *,
    statuses: list[RequestStatus] | None = None,
    channel: str | None = None,
    client_id: UUID | None = None,
    assigned_to: UUID | None = None,
    only_open: bool = False,
    only_overdue: bool = False,
    q: str | None = None,
    params: PageParams,
) -> tuple[list[ServiceRequest], int]:
    conds: list[Any] = []
    if statuses:
        conds.append(ServiceRequest.status.in_(statuses))
    if only_open:
        conds.append(ServiceRequest.status.in_(_OPEN))
    if only_overdue:
        conds.append(overdue_condition())
    if channel:
        conds.append(ServiceRequest.channel == channel)
    if client_id:
        conds.append(ServiceRequest.client_id == client_id)
    if assigned_to:
        conds.append(ServiceRequest.assigned_to == assigned_to)
    if q:
        like = f"%{q.strip().lower()}%"
        conds.append(
            or_(
                func.lower(ServiceRequest.subject).like(like),
                func.lower(func.coalesce(ServiceRequest.contact_name, "")).like(like),
                func.lower(func.coalesce(ServiceRequest.contact_mobile, "")).like(like),
                func.lower(func.cast(ServiceRequest.number, sa.String)).like(like),
            )
        )

    stmt = with_scope(_list_base(), scope, "request")
    count_stmt = with_scope(select(func.count(ServiceRequest.id)), scope, "request")
    if conds:
        stmt, count_stmt = stmt.where(and_(*conds)), count_stmt.where(and_(*conds))
    col = _SORTABLE.get(params.sort or "last_activity_at", ServiceRequest.last_activity_at)
    stmt = stmt.order_by(col.desc() if params.order == "desc" else col.asc()).offset(params.offset).limit(params.page_size)
    items = list((await session.execute(stmt)).scalars().unique())
    return items, int((await session.execute(count_stmt)).scalar_one())


async def messages(session: AsyncSession, request_id: UUID) -> list[RequestMessage]:
    stmt = select(RequestMessage).where(RequestMessage.request_id == request_id).order_by(RequestMessage.created_at.asc())
    return list((await session.execute(stmt)).scalars())


async def latest_messages(session: AsyncSession, request_ids: list[UUID]) -> dict[UUID, RequestMessage]:
    """Latest message per request for a whole page, in ONE window-function query.

    The list endpoint used to run `messages()` once per row — 25 queries per page, each loading a
    full thread just to render a one-line snippet (`_snippet` reads the newest body). That was the
    measured cost of the inbox list."""
    if not request_ids:
        return {}
    rn = func.row_number().over(partition_by=RequestMessage.request_id, order_by=RequestMessage.created_at.desc()).label("rn")
    ranked = select(RequestMessage.id.label("mid"), rn).where(RequestMessage.request_id.in_(request_ids)).subquery()
    stmt = select(RequestMessage).join(ranked, sa.and_(RequestMessage.id == ranked.c.mid, ranked.c.rn == 1))
    rows = (await session.execute(stmt)).scalars().all()
    return {row.request_id: row for row in rows}


async def notes(session: AsyncSession, request_id: UUID) -> list[InternalNote]:
    stmt = (
        select(InternalNote)
        .where(InternalNote.request_id == request_id)
        .options(selectinload(InternalNote.author))
        .order_by(InternalNote.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars())


async def attachments(session: AsyncSession, request_id: UUID) -> list[Attachment]:
    """Every file of the request thread, voice notes included (spec §5 attachments)."""
    stmt = select(Attachment).where(Attachment.request_id == request_id).order_by(Attachment.created_at.desc())
    return list((await session.execute(stmt)).scalars())


async def calls(session: AsyncSession, request_id: UUID) -> list[CallRecord]:
    stmt = select(CallRecord).where(CallRecord.request_id == request_id).order_by(CallRecord.started_at.desc())
    return list((await session.execute(stmt)).scalars())


async def open_for_thread(session: AsyncSession, insured_person_id: UUID, window_minutes: int) -> ServiceRequest | None:
    """Reply-threading rule (spec §8): reuse an open request for the same person inside the window."""
    since = datetime.now(UTC) - timedelta(minutes=window_minutes)
    stmt = (
        select(ServiceRequest)
        .where(
            ServiceRequest.insured_person_id == insured_person_id,
            ServiceRequest.status.in_(_OPEN),
            ServiceRequest.last_activity_at >= since,
        )
        .order_by(ServiceRequest.last_activity_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def find_by_external_thread(session: AsyncSession, thread_id: str) -> ServiceRequest | None:
    return (
        await session.execute(select(ServiceRequest).where(ServiceRequest.external_thread_id == thread_id).limit(1))
    ).scalar_one_or_none()


async def external_id_seen(session: AsyncSession, external_id: str) -> bool:
    value = (
        await session.execute(select(RequestMessage.id).where(RequestMessage.external_id == external_id).limit(1))
    ).scalar_one_or_none()
    return value is not None


async def kpis(session: AsyncSession, scope: Scope) -> dict[str, Any]:
    """Screen 1 KPI row: today's volume, avg first response, SLA compliance, overdue count."""
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = int(
        (
            await session.execute(
                with_scope(select(func.count(ServiceRequest.id)), scope, "request").where(ServiceRequest.created_at >= today)
            )
        ).scalar_one()
    )
    overdue = int(
        (await session.execute(with_scope(select(func.count(ServiceRequest.id)), scope, "request").where(overdue_condition()))).scalar_one()
    )
    open_count = int(
        (
            await session.execute(
                with_scope(select(func.count(ServiceRequest.id)), scope, "request").where(ServiceRequest.status.in_(_OPEN))
            )
        ).scalar_one()
    )
    yesterday = today - timedelta(days=1)
    yesterday_count = int(
        (
            await session.execute(
                with_scope(select(func.count(ServiceRequest.id)), scope, "request").where(
                    ServiceRequest.created_at >= yesterday, ServiceRequest.created_at < today
                )
            )
        ).scalar_one()
    )
    since = today - timedelta(days=30)
    within = with_scope(
        select(func.count(ServiceRequest.id)).where(
            ServiceRequest.created_at >= since,
            ServiceRequest.sla_due_at.is_not(None),
            ServiceRequest.first_response_at.is_not(None),
            ServiceRequest.first_response_at <= ServiceRequest.sla_due_at,
        ),
        scope,
        "request",
    )
    total30 = with_scope(select(func.count(ServiceRequest.id)).where(ServiceRequest.created_at >= since), scope, "request")
    on_time, denom = int((await session.execute(within)).scalar_one()), int((await session.execute(total30)).scalar_one())
    avg = await avg_first_response_minutes(session, scope, days=30)
    # the hint line under the KPI: how much faster/slower the last 30 days are vs the prior 30
    prev_avg = await avg_first_response_minutes(session, scope, days=60)
    delta = None
    if avg is not None and prev_avg is not None:
        delta = round(float(prev_avg) - float(avg), 1)
    return {
        "requests_today": today_count,
        "requests_yesterday": yesterday_count,
        "avg_first_response_delta_minutes": delta,
        "open_requests": open_count,
        "overdue": overdue,
        "sla_compliance_pct": round((on_time / denom) * 100.0, 1) if denom else None,
        "avg_first_response_minutes": avg,
    }


async def avg_first_response_minutes(session: AsyncSession, scope: Scope, *, days: int = 30) -> float | None:
    since = datetime.now(UTC) - timedelta(days=days)
    stmt = with_scope(
        select(func.avg(func.extract("epoch", ServiceRequest.first_response_at) - func.extract("epoch", ServiceRequest.created_at))).where(
            ServiceRequest.first_response_at.is_not(None), ServiceRequest.created_at >= since
        ),
        scope,
        "request",
    )
    value = (await session.execute(stmt)).scalar_one_or_none()
    return round(float(value) / 60.0, 1) if value is not None else None


async def channels_breakdown(session: AsyncSession, scope: Scope, *, days: int = 7) -> list[dict[str, Any]]:
    """Screen 1 bar chart: per-day, per-channel counts (zero-filled so the UI never infers)."""
    since = (datetime.now(UTC) - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    day_expr = func.date(ServiceRequest.created_at)
    stmt = with_scope(
        select(day_expr.label("d"), ServiceRequest.channel, func.count(ServiceRequest.id))
        .where(ServiceRequest.created_at >= since)
        .group_by(day_expr, ServiceRequest.channel),
        scope,
        "request",
    )
    rows = (await session.execute(stmt)).all()
    buckets: dict[str, dict[str, Any]] = {}
    for i in range(days - 1, -1, -1):
        key = (since + timedelta(days=i)).date().isoformat()
        buckets[key] = {"date": key, "whatsapp": 0, "call": 0, "email": 0, "manual": 0}
    for d, channel, count in rows:
        key = d.isoformat() if hasattr(d, "isoformat") else str(d)
        if key in buckets:
            buckets[key][channel.value if hasattr(channel, "value") else str(channel)] = int(count)
    return list(buckets.values())


async def status_breakdown(session: AsyncSession, scope: Scope) -> list[dict[str, Any]]:
    stmt = with_scope(
        select(ServiceRequest.status, func.count(ServiceRequest.id))
        .where(ServiceRequest.status.notin_([RequestStatus.CLOSED]))
        .group_by(ServiceRequest.status),
        scope,
        "request",
    )
    counts = dict.fromkeys(("new", "in_progress", "replied"), 0)
    overdue = 0
    for status, count in (await session.execute(stmt)).all():
        value = status.value if hasattr(status, "value") else str(status)
        if value in counts:
            counts[value] += int(count)
    overdue = int(
        (await session.execute(with_scope(select(func.count(ServiceRequest.id)).where(overdue_condition()), scope, "request"))).scalar_one()
    )
    total = sum(counts.values()) + overdue
    ordered = [("new", counts["new"]), ("in_progress", counts["in_progress"]), ("replied", counts["replied"]), ("overdue", overdue)]
    return [{"key": key, "count": count, "pct": round((count / total) * 100.0) if total else 0} for key, count in ordered]


async def recent(session: AsyncSession, scope: Scope, *, limit: int = 8) -> list[ServiceRequest]:
    stmt = with_scope(_base(), scope, "request").order_by(ServiceRequest.last_activity_at.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().unique())


async def mark_first_response(request: ServiceRequest, moment: datetime) -> None:
    """First *outbound* reply stops the SLA clock permanently (spec §8)."""
    if request.first_response_at is None:
        request.first_response_at = moment
    request.last_activity_at = moment
