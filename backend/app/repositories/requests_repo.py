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


def list_options() -> list[Any]:
    """The eager-load policy for any payload that renders request *briefs* (R15 P2).

    Public because the dashboard builds the same rows: `recent_requests` and the «اليوم» board serialise briefs,
    and they were quietly paying for the full auth graph per row — measured at **60 SQL statements for one
    `/dashboard/home`**, of which 27 were repeated loads of `roles→permissions`, `user_scopes` and
    `client_portal_users` (one trio per assignee), 6 loads of `document_requirements` (one per insurance type;
    that relationship is `selectin` on the model) and 3 of `client_contacts`.

    One list, one meaning: a screen that shows a name must not pay for the relations hanging off it.
    """
    from sqlalchemy.orm import joinedload

    from app.models.auth import Role, User
    from app.models.clients import Client, InsuredPerson
    from app.models.masters import InsuranceType

    unused_assignee = [
        joinedload(ServiceRequest.assignee).noload(User.scopes),
        joinedload(ServiceRequest.assignee).noload(User.portal_link),
        joinedload(ServiceRequest.assignee).joinedload(User.role).noload(Role.permissions),
    ]
    return [
        joinedload(ServiceRequest.client).noload(Client.contacts),
        joinedload(ServiceRequest.client).noload(Client.account_owner),
        joinedload(ServiceRequest.client).noload(Client.source_lead),
        joinedload(ServiceRequest.insured_person),
        joinedload(ServiceRequest.insured_person).joinedload(InsuredPerson.client).noload(Client.contacts),
        joinedload(ServiceRequest.insured_person).joinedload(InsuredPerson.client).noload(Client.account_owner),
        joinedload(ServiceRequest.insured_person).joinedload(InsuredPerson.client).noload(Client.source_lead),
        joinedload(ServiceRequest.insurer),
        # the type's document checklist belongs to the settings screen, not to a KPI row or a queue
        joinedload(ServiceRequest.insurance_type).noload(InsuranceType.document_requirements),
        *unused_assignee,
    ]


def _list_base() -> Select[Any]:
    """List pages: many-to-one relations as JOINs in the page query itself, not five extra
    selectin round-trips per page (perf gate, product-score8 follow-up).

    The brief serializer reads only names off the joined rows, so the eager collections
    hanging off them (client contacts, the assignee's scopes/permissions/portal link, the
    client's account owner chain) are `noload`-ed: without this they fired one selectin
    query per relation per page for data the list never renders.

    R17 §6: there are **two** paths to `clients` per row — `client` and `insured_person.client`
    (`InsuredPerson.client` is `lazy="joined"`) — and `Client.account_owner` is eager too. Leaving the
    second path unpruned joined the same `users` row twice per request, so SQLAlchemy reported
    «Multiple rows returned with uselist=False for eagerly-loaded attribute 'Client.account_owner'»,
    once per request on the busiest screen in the product. Both paths get the same three noloads."""
    from sqlalchemy.orm import joinedload

    from app.models.auth import Role, User
    from app.models.clients import Client, InsuredPerson

    return select(ServiceRequest).options(
        joinedload(ServiceRequest.client).noload(Client.contacts),
        joinedload(ServiceRequest.client).noload(Client.account_owner),
        joinedload(ServiceRequest.client).noload(Client.source_lead),
        joinedload(ServiceRequest.insured_person),
        joinedload(ServiceRequest.insured_person).joinedload(InsuredPerson.client).noload(Client.contacts),
        joinedload(ServiceRequest.insured_person).joinedload(InsuredPerson.client).noload(Client.account_owner),
        joinedload(ServiceRequest.insured_person).joinedload(InsuredPerson.client).noload(Client.source_lead),
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


async def messages(
    session: AsyncSession,
    request_id: UUID,
    *,
    limit: int | None = None,
    before: datetime | None = None,
) -> list[RequestMessage]:
    """The thread, oldest first. `limit` keeps the **newest** page — a chat opens at the bottom.

    The unbounded version was the R15 P1 measurement: a negotiation thread with hundreds of WhatsApp
    messages loaded and serialised the entire history on every open, and the browser then mounted every
    bubble. Ordering is ascending because that is what the screen renders; the *selection* is descending so
    the newest page is one index scan from the end, not a scan of everything before it.
    """
    stmt = select(RequestMessage).where(RequestMessage.request_id == request_id)
    if before is not None:
        stmt = stmt.where(RequestMessage.created_at < before)
    if limit is None:
        return list((await session.execute(stmt.order_by(RequestMessage.created_at.asc()))).scalars())
    newest_first = list((await session.execute(stmt.order_by(RequestMessage.created_at.desc()).limit(limit))).scalars())
    return list(reversed(newest_first))


async def thread_totals(session: AsyncSession, request_id: UUID) -> dict[str, int]:
    """How much exists behind each window, in ONE round trip (four scalar subqueries).

    The client needs this to say "load older" honestly; four separate `count(*)` calls would have cost four
    round trips to answer a question the screen asks on every open.
    """

    def _count(model: type[Any]) -> Any:
        return select(func.count(model.id)).where(model.request_id == request_id).scalar_subquery()  # type: ignore[attr-defined]

    row = (
        await session.execute(
            select(
                _count(RequestMessage).label("messages"),
                _count(InternalNote).label("notes"),
                _count(CallRecord).label("calls"),
                _count(Attachment).label("attachments"),
            )
        )
    ).one()
    return {"messages": int(row.messages), "notes": int(row.notes), "calls": int(row.calls), "attachments": int(row.attachments)}


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


async def notes(session: AsyncSession, request_id: UUID, *, limit: int | None = None) -> list[InternalNote]:
    stmt = (
        select(InternalNote)
        .where(InternalNote.request_id == request_id)
        .options(selectinload(InternalNote.author))
        .order_by(InternalNote.created_at.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return list((await session.execute(stmt)).scalars())


async def attachments(session: AsyncSession, request_id: UUID, *, limit: int | None = None) -> list[Attachment]:
    """Files of the request thread, voice notes included (spec §5 attachments), newest first."""
    stmt = select(Attachment).where(Attachment.request_id == request_id).order_by(Attachment.created_at.desc())
    if limit is not None:
        stmt = stmt.limit(limit)
    return list((await session.execute(stmt)).scalars())


async def calls(session: AsyncSession, request_id: UUID, *, limit: int | None = None) -> list[CallRecord]:
    stmt = select(CallRecord).where(CallRecord.request_id == request_id).order_by(CallRecord.started_at.desc())
    if limit is not None:
        stmt = stmt.limit(limit)
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
    """Screen 1 KPI row: today's volume, avg first response, SLA compliance, overdue count.

    **One round trip** (R15 P2). This used to be eight — six `count(*)`s, two averages and one more count for
    the client total — all against the same table, all through the same scope predicate, all serialised behind
    each other on one pooled connection. On the dashboard's critical path that was ~60% of the request.

    Every clause is unchanged and still comes from one place: `overdue_condition()` is the single definition of
    «متأخر» that the KPI, the breakdown and the `only_overdue` list share, and it is used here as-is. A scalar
    subquery returns exactly what the separate statement returned, so a KPI cannot drift from the list it
    deep-links to — which is the failure this row already had once.
    """
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)
    since = today - timedelta(days=30)
    older = today - timedelta(days=60)  # the window the "vs the previous 30 days" hint compares against
    epoch_seconds = func.extract("epoch", ServiceRequest.first_response_at) - func.extract("epoch", ServiceRequest.created_at)
    responded = ServiceRequest.first_response_at.is_not(None)

    def count(where: Any) -> Any:
        return with_scope(select(func.count(ServiceRequest.id)).where(where), scope, "request").scalar_subquery()

    row = (
        await session.execute(
            select(
                count(ServiceRequest.created_at >= today).label("today"),
                count(and_(ServiceRequest.created_at >= yesterday, ServiceRequest.created_at < today)).label("yesterday"),
                count(overdue_condition()).label("overdue"),
                count(ServiceRequest.status.in_(_OPEN)).label("open"),
                count(
                    and_(
                        ServiceRequest.created_at >= since,
                        ServiceRequest.sla_due_at.is_not(None),
                        responded,
                        ServiceRequest.first_response_at <= ServiceRequest.sla_due_at,
                    )
                ).label("on_time"),
                count(ServiceRequest.created_at >= since).label("total30"),
                with_scope(select(func.avg(epoch_seconds)).where(responded, ServiceRequest.created_at >= since), scope, "request")
                .scalar_subquery()
                .label("avg30"),
                with_scope(select(func.avg(epoch_seconds)).where(responded, ServiceRequest.created_at >= older), scope, "request")
                .scalar_subquery()
                .label("avg60"),
            )
        )
    ).one()

    avg = round(float(row.avg30) / 60.0, 1) if row.avg30 is not None else None
    prev_avg = round(float(row.avg60) / 60.0, 1) if row.avg60 is not None else None
    delta = round(prev_avg - avg, 1) if avg is not None and prev_avg is not None else None
    denom = int(row.total30)
    return {
        "requests_today": int(row.today),
        "requests_yesterday": int(row.yesterday),
        "avg_first_response_delta_minutes": delta,
        "open_requests": int(row.open),
        "overdue": int(row.overdue),
        "sla_compliance_pct": round((int(row.on_time) / denom) * 100.0, 1) if denom else None,
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
    """The dashboard's "latest requests" — serialised as briefs, so it takes the brief load policy (R15 P2)."""
    stmt = (
        with_scope(select(ServiceRequest).options(*list_options()), scope, "request")
        .order_by(ServiceRequest.last_activity_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().unique())


async def mark_first_response(request: ServiceRequest, moment: datetime) -> None:
    """First *outbound* reply stops the SLA clock permanently (spec §8)."""
    if request.first_response_at is None:
        request.first_response_at = moment
    request.last_activity_at = moment
