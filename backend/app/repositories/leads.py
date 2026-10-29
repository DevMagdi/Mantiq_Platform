"""Lead queries. All of them scoped; none of them accept a raw id without a scope."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.pagination import PageParams
from app.core.scope import Scope
from app.domain.enums import ActivityType, LeadStatus
from app.models.auth import Role, User
from app.models.leads import Lead, LeadActivity, LeadMeeting
from app.repositories.scope_filters import with_scope

#: the unassigned pool: nobody owns it, and it is not already finished. `list_page(unassigned=true)`,
#: `unassigned_ids` and the count on the team card all read this one definition.
_UNASSIGNED_POOL: tuple[Any, ...] = (
    Lead.assigned_to.is_(None),
    Lead.status.notin_([LeadStatus.WON, LeadStatus.LOST]),
)

_SORTABLE = {
    "name": Lead.name,
    "status": Lead.status,
    "created_at": Lead.created_at,
    "next_followup_at": Lead.next_followup_at,
    "updated_at": Lead.updated_at,
}


def _base() -> Select[Any]:
    return select(Lead).options(
        selectinload(Lead.assignee).selectinload(User.role),
        selectinload(Lead.insurance_type),
        selectinload(Lead.current_insurer),
        selectinload(Lead.lost_reason),
    )


async def get_by_id(session: AsyncSession, lead_id: UUID, scope: Scope) -> Lead | None:
    return (await session.execute(with_scope(_base().where(Lead.id == lead_id), scope, "lead"))).scalar_one_or_none()


async def list_page(
    session: AsyncSession,
    scope: Scope,
    *,
    statuses: list[LeadStatus] | None = None,
    assigned_to: UUID | None = None,
    q: str | None = None,
    followup_overdue: bool = False,
    unassigned: bool = False,
    params: PageParams,
) -> tuple[list[Lead], int]:
    conds: list[Any] = []
    if statuses:
        conds.append(Lead.status.in_(statuses))
    if assigned_to is not None:
        conds.append(Lead.assigned_to == assigned_to)
    if unassigned:
        # screen 6 lets a manager drain the pool of leads nobody owns yet — the same predicate
        # `unassigned_ids` uses for the actual redistribution, or the card would list rows the button skips
        conds.extend(_UNASSIGNED_POOL)
    if q:
        like = f"%{q.strip().lower()}%"
        conds.append(
            or_(
                func.lower(Lead.name).like(like),
                func.lower(func.coalesce(Lead.contact_name, "")).like(like),
                func.lower(func.coalesce(Lead.mobile, "")).like(like),
            )
        )
    if followup_overdue:
        conds.append(Lead.next_followup_at < datetime.now(UTC))
        conds.append(Lead.status.notin_([LeadStatus.WON, LeadStatus.LOST]))

    stmt = with_scope(_base(), scope, "lead")
    count_stmt = with_scope(select(func.count(Lead.id)), scope, "lead")
    if conds:
        stmt = stmt.where(and_(*conds))
        count_stmt = count_stmt.where(and_(*conds))

    sort_col = _SORTABLE.get(params.sort or "created_at", Lead.created_at)
    stmt = stmt.order_by(sort_col.desc() if params.order == "desc" else sort_col.asc())
    stmt = stmt.offset(params.offset).limit(params.page_size)

    items = list((await session.execute(stmt)).scalars().unique())
    total = int((await session.execute(count_stmt)).scalar_one())
    return items, total


async def unassigned_ids(session: AsyncSession, scope: Scope, *, limit: int = 2_000) -> list[UUID]:
    """Ids of the unassigned pool, in the order a redistribution should hand them out (oldest first)."""
    stmt = with_scope(select(Lead.id).where(and_(*_UNASSIGNED_POOL)), scope, "lead").order_by(Lead.created_at).limit(limit)
    return list((await session.execute(stmt)).scalars())


async def board(
    session: AsyncSession,
    scope: Scope,
    *,
    assigned_to: UUID | None = None,
    q: str | None = None,
    unassigned: bool = False,
) -> dict[str, list[Lead]]:
    """Kanban payload (screen 3): 5 columns in one query, same predicate as the list.

    `assigned_to` is what a manager uses to answer "where does Mohamed stand?" — the same board shape,
    one rep's cards. It narrows; it can never widen, because `with_scope` is applied after it (a rep who
    passes a colleague's id gets an empty board, not that colleague's book).
    """
    conds: list[Any] = [Lead.status.in_(LeadStatus.board_columns())]
    if assigned_to is not None:
        conds.append(Lead.assigned_to == assigned_to)
    if unassigned:
        conds.extend(_UNASSIGNED_POOL)
    if q:
        like = f"%{q.strip().lower()}%"
        conds.append(func.lower(Lead.name).like(like))
    stmt = with_scope(_base().where(and_(*conds)), scope, "lead").order_by(Lead.updated_at.desc()).limit(500)
    leads = list((await session.execute(stmt)).scalars().unique())
    grouped: dict[str, list[Lead]] = {c.value: [] for c in LeadStatus.board_columns()}
    for lead in leads:
        grouped[lead.status.value].append(lead)
    return grouped


async def stats(session: AsyncSession, scope: Scope, *, rep_id: UUID | None = None) -> dict[str, Any]:
    """The KPI row of screen 3 (value + hint line each). Owner scope makes "leads of me" automatic."""
    now = datetime.now(UTC)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=7)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    mine_stmt = with_scope(select(func.count(Lead.id)), scope, "lead").where(Lead.status.notin_([LeadStatus.WON, LeadStatus.LOST]))
    if rep_id is not None:
        mine_stmt = mine_stmt.where(Lead.assigned_to == rep_id)

    meetings_stmt = with_scope(select(func.count(LeadMeeting.id)).join(Lead, Lead.id == LeadMeeting.lead_id), scope, "lead").where(
        LeadMeeting.scheduled_at >= week_start, LeadMeeting.scheduled_at < week_end
    )
    if rep_id is not None:
        meetings_stmt = meetings_stmt.where(Lead.assigned_to == rep_id)

    overdue_stmt = with_scope(select(func.count(Lead.id)), scope, "lead").where(
        Lead.next_followup_at < now, Lead.status.notin_([LeadStatus.WON, LeadStatus.LOST])
    )
    converted_stmt = with_scope(select(func.count(Lead.id)), scope, "lead").where(
        Lead.status == LeadStatus.WON, Lead.converted_at >= month_start
    )
    if rep_id is not None:
        converted_stmt = converted_stmt.where(Lead.assigned_to == rep_id)

    new_week_stmt = with_scope(select(func.count(Lead.id)), scope, "lead").where(Lead.created_at >= week_start)
    if rep_id is not None:
        new_week_stmt = new_week_stmt.where(Lead.assigned_to == rep_id)
    today_end = today + timedelta(days=1)
    today_meetings = with_scope(select(func.count(LeadMeeting.id)).join(Lead, Lead.id == LeadMeeting.lead_id), scope, "lead").where(
        LeadMeeting.scheduled_at >= today, LeadMeeting.scheduled_at < today_end
    )
    if rep_id is not None:
        today_meetings = today_meetings.where(Lead.assigned_to == rep_id)
    lost_month_stmt = with_scope(select(func.count(Lead.id)), scope, "lead").where(
        Lead.status == LeadStatus.LOST, Lead.updated_at >= month_start
    )
    if rep_id is not None:
        lost_month_stmt = lost_month_stmt.where(Lead.assigned_to == rep_id)

    won_month = int((await session.execute(converted_stmt)).scalar_one())
    lost_month = int((await session.execute(lost_month_stmt)).scalar_one())
    return {
        "my_leads": int((await session.execute(mine_stmt)).scalar_one()),
        "new_week": int((await session.execute(new_week_stmt)).scalar_one()),
        "meetings_week": int((await session.execute(meetings_stmt)).scalar_one()),
        "meetings_today": int((await session.execute(today_meetings)).scalar_one()),
        "followups_overdue": int((await session.execute(overdue_stmt)).scalar_one()),
        "converted_month": won_month,
        "conversion_rate_pct": round((won_month / (won_month + lost_month)) * 100.0, 1) if (won_month + lost_month) else None,
    }


async def find_active_by_mobile(session: AsyncSession, mobile: str, scope: Scope) -> Lead | None:
    """Duplicate detection (screen 10). Managers see the duplicate anywhere; reps see it only if
    they own it — otherwise the API must not confirm existence of someone else's lead."""
    stmt = with_scope(_base().where(Lead.mobile == mobile, Lead.deleted_at.is_(None)), scope, "lead").order_by(Lead.created_at.desc())
    return (await session.execute(stmt.limit(1))).scalar_one_or_none()


async def mobile_exists_anywhere(session: AsyncSession, mobile: str) -> bool:
    """System-level check used by the create endpoint to decide 'duplicate' vs 'new' for the actor."""
    value = (await session.execute(select(Lead.id).where(Lead.mobile == mobile, Lead.deleted_at.is_(None)).limit(1))).scalar_one_or_none()
    return value is not None


async def add_activity(
    session: AsyncSession,
    *,
    lead_id: UUID,
    type_: ActivityType | str,
    body: str | None,
    meta: dict[str, Any] | None,
    actor_id: UUID | None,
    occurred_at: datetime | None = None,
) -> LeadActivity:
    activity = LeadActivity(
        lead_id=lead_id,
        type=ActivityType(type_),
        body=body,
        meta=meta,
        actor_id=actor_id,
        occurred_at=occurred_at or datetime.now(UTC),
    )
    session.add(activity)
    await session.flush()
    return activity


async def list_activities(session: AsyncSession, lead_id: UUID, scope: Scope) -> list[LeadActivity]:
    stmt = (
        select(LeadActivity)
        .where(LeadActivity.lead_id == lead_id)
        .options(selectinload(LeadActivity.actor))
        .order_by(LeadActivity.occurred_at.desc())
        .limit(200)
    )
    # activity of an invisible lead must stay invisible: gate by the parent lead
    stmt = stmt.where(select(1).where(with_scope(select(Lead.id).where(Lead.id == lead_id), scope, "lead").exists()).exists())
    return list((await session.execute(stmt)).scalars())


async def rep_workload(session: AsyncSession) -> list[dict[str, Any]]:
    """Screen 7: per-rep counts + stage distribution (managers only, see router gate)."""
    stmt = (
        select(
            User.id,
            User.full_name,
            func.count(Lead.id).filter(Lead.status.notin_([LeadStatus.WON, LeadStatus.LOST])).label("open_leads"),
            func.count(Lead.id).filter(Lead.status == LeadStatus.MEETING_SCHEDULED).label("meetings"),
            func.count(Lead.id).filter(Lead.status == LeadStatus.WON).label("won"),
            User.is_active,
        )
        .outerjoin(Lead, and_(Lead.assigned_to == User.id, Lead.deleted_at.is_(None)))
        .join(Role, Role.id == User.role_id)
        # a deleted seat is nobody's workload; a suspended one still is — its open leads did not evaporate —
        # and `is_active` is what keeps it out of the redistribution picker
        .where(func.lower(Role.code) == "sales_rep", User.deleted_at.is_(None))
        .group_by(User.id, User.full_name, User.is_active)
        # deterministic order: the redistribute dialog renders this list as-is, and an unordered result put
        # whichever rep the planner returned first behind the first checkbox (R3: the e2e moved a lead to a
        # seat created minutes earlier in the same run, and no seeded rep could see it)
        .order_by(User.full_name)
    )
    rows = (await session.execute(stmt)).all()

    stage_stmt = (
        select(Lead.assigned_to, Lead.status, func.count(Lead.id))
        .where(Lead.deleted_at.is_(None), Lead.assigned_to.is_not(None))
        .group_by(Lead.assigned_to, Lead.status)
    )
    stages: dict[UUID, dict[str, int]] = {}
    for assigned_to, status, count in (await session.execute(stage_stmt)).all():
        stages.setdefault(assigned_to, {})[status.value if hasattr(status, "value") else status] = int(count)

    unassigned = int((await session.execute(select(func.count(Lead.id)).where(Lead.deleted_at.is_(None), *_UNASSIGNED_POOL))).scalar_one())
    return [
        {
            "rep_id": str(r.id),
            "name": r.full_name,
            "open_leads": int(r.open_leads),
            "meetings": int(r.meetings),
            "won": int(r.won),
            "stages": stages.get(r.id, {}),
            "unassigned": unassigned,
            "is_active": bool(r.is_active),
        }
        for r in rows
    ]
