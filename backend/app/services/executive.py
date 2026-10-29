"""The executive control dashboard (R10, ADR-030 §7).

One rule decides this file: **every number is a query, and every query is the one the module it reports on
already uses.** `outstanding_ar` is `services.finance.ar_summary`, `cashbox_balance` is
`services.finance.cashbox`, `collections_mtd` is the posted-in sum of the ledger. Nothing is cached, nothing
is estimated, and the one figure the office cannot compute yet — a collection target — is returned as
`null` instead of being invented, because a made-up denominator silently turns a real numerator into a fake
percentage.

The response shape is fixed (see `ExecutiveDashboardOut`): `period`, `kpis`, `sales_performance`,
`ar_aging`, `channel_mix`, `renewals_next_30d`, `alerts`. `tests/test_finance.py` recomputes each KPI with
its own SQL and asserts the two agree, so "the dashboard lies" cannot ship again.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.scope import Scope
from app.domain.enums import (
    Channel,
    CommissionStatus,
    LedgerDirection,
    LedgerStatus,
    PolicyStatus,
    RequestStatus,
    SettlementStatus,
)
from app.models.auth import User
from app.models.clients import Client
from app.models.finance import CommissionSettlement, LedgerEntry
from app.models.leads import Lead
from app.models.phase2 import Commission, Policy
from app.models.requests import ServiceRequest
from app.repositories.scope_filters import with_scope
from app.services import finance as finance_svc

CHANNEL_LABELS: dict[str, str] = {
    Channel.WHATSAPP.value: "واتساب",
    Channel.CALL.value: "مكالمات",
    Channel.EMAIL.value: "بريد إلكتروني",
    Channel.MANUAL.value: "إدخال يدوي",
}


def month_bounds(today: date | None = None) -> tuple[date, date]:
    moment = today or date.today()
    start = moment.replace(day=1)
    nxt = (start + timedelta(days=32)).replace(day=1)
    return start, nxt - timedelta(days=1)


def _month_start_dt(today: date | None = None) -> datetime:
    start, _ = month_bounds(today)
    return datetime.combine(start, datetime.min.time(), tzinfo=UTC)


async def _sum_ledger(
    session: AsyncSession,
    scope: Scope,
    *conditions: Any,
) -> Decimal:
    stmt = with_scope(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).select_from(LedgerEntry), scope, "ledger").where(
        LedgerEntry.status == LedgerStatus.POSTED, *conditions
    )
    return finance_svc.money((await session.execute(stmt)).scalar_one())


async def _count(session: AsyncSession, stmt: Any) -> int:
    return int((await session.execute(stmt)).scalar_one() or 0)


async def dashboard(session: AsyncSession, scope: Scope, *, today: date | None = None) -> dict[str, Any]:
    moment = today or date.today()
    month_start, month_end = month_bounds(moment)
    month_start_dt = _month_start_dt(moment)

    # ---------------------------------------------------------------- money
    collections_mtd = await _sum_ledger(
        session,
        scope,
        LedgerEntry.direction == LedgerDirection.IN,
        LedgerEntry.posted_at >= month_start_dt,
    )
    ar = await finance_svc.ar_summary(session, scope, today=moment)
    cash = await finance_svc.cashbox(session, scope)

    visible_commissions = with_scope(select(Commission), scope, "commission")
    commissions_accrued = finance_svc.money(
        (
            await session.execute(
                select(func.coalesce(func.sum(Commission.amount), 0))
                .select_from(Commission)
                .where(
                    Commission.id.in_(visible_commissions.with_only_columns(Commission.id)),
                    Commission.status == CommissionStatus.ACCRUED,
                )
            )
        ).scalar_one()
    )
    paid_mtd = finance_svc.money(
        (
            await session.execute(
                select(func.coalesce(func.sum(CommissionSettlement.amount), 0))
                .select_from(CommissionSettlement)
                .where(
                    CommissionSettlement.commission_id.in_(visible_commissions.with_only_columns(Commission.id)),
                    CommissionSettlement.status == SettlementStatus.PAID,
                    CommissionSettlement.paid_at >= month_start_dt,
                )
            )
        ).scalar_one()
    )

    # ---------------------------------------------------------------- operations
    open_statuses = [status.value for status in RequestStatus.open_statuses()]
    open_requests = await _count(
        session,
        with_scope(select(func.count()).select_from(ServiceRequest), scope, "request").where(ServiceRequest.status.in_(open_statuses)),
    )
    overdue_requests = await _count(
        session,
        with_scope(select(func.count()).select_from(ServiceRequest), scope, "request").where(
            ServiceRequest.status.in_(open_statuses), ServiceRequest.is_overdue.is_(True)
        ),
    )
    new_leads_mtd = await _count(
        session,
        with_scope(select(func.count()).select_from(Lead), scope, "lead").where(Lead.created_at >= month_start_dt),
    )
    converted_leads_mtd = await _count(
        session,
        with_scope(select(func.count()).select_from(Lead), scope, "lead").where(Lead.converted_at >= month_start_dt),
    )
    # R10.1: `issued_at` only — no `coalesce(issued_at, created_at)`. A policy whose issue date was never
    # recorded (no `policy.issued` audit row) is *not* counted under a month it may not belong to; it is
    # reported as unknown below instead. Guessing here is exactly the wrong number the screen must not show.
    issued = with_scope(select(Policy), scope, "policy").where(
        Policy.status == PolicyStatus.ISSUED,
        Policy.issued_at.is_not(None),
        Policy.issued_at >= month_start_dt,
    )
    policies_issued_mtd = await _count(session, issued.with_only_columns(func.count()).order_by(None))
    issue_date_unknown = await _count(
        session,
        with_scope(select(func.count()).select_from(Policy), scope, "policy").where(
            Policy.status == PolicyStatus.ISSUED,
            Policy.issued_at.is_(None),
        ),
    )

    return {
        "period": {"from_date": month_start, "to_date": month_end, "label": f"{month_start:%Y-%m}"},
        "kpis": {
            "collections_mtd": float(collections_mtd),
            # no target model exists yet; a number here would be a fabricated KPI (ADR-030 §8)
            "collections_target": None,
            "outstanding_ar": ar["outstanding_total"],
            "overdue_ar": ar["overdue_total"],
            "commissions_accrued": float(commissions_accrued),
            "commissions_paid_mtd": float(paid_mtd),
            "open_requests": open_requests,
            "overdue_requests": overdue_requests,
            "new_leads_mtd": new_leads_mtd,
            "converted_leads_mtd": converted_leads_mtd,
            "policies_issued_mtd": policies_issued_mtd,
            "cashbox_balance": cash["balance"],
        },
        "sales_performance": await sales_performance(session, scope, today=moment),
        "ar_aging": ar["buckets"],
        "channel_mix": await channel_mix(session, scope, today=moment),
        "renewals_next_30d": await renewals(session, scope, today=moment),
        "alerts": [
            {
                "code": "issue_date_unknown",
                "severity": "low",
                # an issue that was never recorded is not counted in `policies_issued_mtd`; saying so is the
                # difference between an honest undercount and a number nobody can reproduce
                "message": "سياسات صادرة بلا تاريخ إصدار مسجَّل — لا تُحتسب في «صادر هذا الشهر»",
                "count": issue_date_unknown,
            },
            {
                "code": "overdue_ar",
                "severity": "high",
                "message": "مستحقات متأخرة تحتاج متابعة تحصيل",
                "count": sum(bucket["count"] for bucket in ar["buckets"] if bucket["bucket"] != "current"),
            },
            {"code": "overdue_requests", "severity": "high", "message": "طلبات تجاوزت زمن الاستجابة", "count": overdue_requests},
            {
                "code": "commissions_to_approve",
                "severity": "medium",
                "message": "عمولات مستحقة لم تُعتمد بعد",
                "count": await _count(
                    session,
                    select(func.count())
                    .select_from(Commission)
                    .where(
                        Commission.id.in_(visible_commissions.with_only_columns(Commission.id)),
                        Commission.status == CommissionStatus.ACCRUED,
                    ),
                ),
            },
        ],
    }


async def sales_performance(session: AsyncSession, scope: Scope, *, today: date | None = None) -> list[dict[str, Any]]:
    """Per rep: policies issued this month, their premium, and what was actually collected from their book.

    Collections are attributed to the owner of the *client* the receipt belongs to — a rep's score is the
    money that came out of their own book, not whoever happened to press "record payment".
    """
    moment = today or date.today()
    month_start_dt = _month_start_dt(moment)
    clients = (await session.execute(with_scope(select(Client), scope, "client"))).scalars().all()
    owner_of: dict[UUID, UUID | None] = {row.id: row.account_owner_id for row in clients}
    if not owner_of:
        return []

    # Grouped aggregates, not full-row loads: this used to hydrate every MTD receipt as an ORM object
    # (with joined relations) and add it up in Python — the measured cost of /executive/dashboard at
    # 50k ledger rows. Grouping by client keeps the owner attribution (visible clients only) identical.
    stats: dict[UUID | None, dict[str, Any]] = {}

    def bucket(owner: UUID | None) -> dict[str, Any]:
        return stats.setdefault(owner, {"policies_issued_mtd": 0, "premium_mtd": Decimal(0), "collected_mtd": Decimal(0)})

    policy_rows = (
        await session.execute(
            with_scope(
                select(Policy.client_id, func.count(), func.coalesce(func.sum(Policy.premium_amount), 0))
                .select_from(Policy)
                .group_by(Policy.client_id),
                scope,
                "policy",
            ).where(
                Policy.status == PolicyStatus.ISSUED,
                Policy.issued_at.is_not(None),
                Policy.issued_at >= month_start_dt,
            )
        )
    ).all()
    for client_id, count, premium in policy_rows:
        row = bucket(owner_of.get(client_id))
        row["policies_issued_mtd"] += int(count)
        row["premium_mtd"] += Decimal(str(premium or 0))

    receipt_rows = (
        await session.execute(
            with_scope(
                select(LedgerEntry.client_id, func.coalesce(func.sum(LedgerEntry.amount), 0))
                .select_from(LedgerEntry)
                .group_by(LedgerEntry.client_id),
                scope,
                "ledger",
            ).where(
                LedgerEntry.status == LedgerStatus.POSTED,
                LedgerEntry.direction == LedgerDirection.IN,
                LedgerEntry.posted_at >= month_start_dt,
            )
        )
    ).all()
    for client_id, total in receipt_rows:
        bucket(owner_of.get(client_id) if client_id else None)["collected_mtd"] += Decimal(str(total or 0))

    owners = {row for row in stats if row is not None}
    names: dict[UUID, str] = {}
    if owners:
        for user in (await session.execute(select(User).where(User.id.in_(owners)))).scalars().all():
            names[user.id] = user.full_name

    out: list[dict[str, Any]] = []
    for owner, row in stats.items():
        out.append(
            {
                "user_id": owner,
                "name": names.get(owner, "بدون مسؤول") if owner else "بدون مسؤول",
                "policies_issued_mtd": row["policies_issued_mtd"],
                "premium_mtd": float(finance_svc.money(row["premium_mtd"])),
                "collected_mtd": float(finance_svc.money(row["collected_mtd"])),
            }
        )
    out.sort(key=lambda row: (-row["collected_mtd"], -row["premium_mtd"], str(row["name"])))
    return out


async def channel_mix(session: AsyncSession, scope: Scope, *, today: date | None = None) -> list[dict[str, Any]]:
    """Requests this month per channel — the same `channel` values the inbox shows, nothing invented."""
    moment = today or date.today()
    rows = (
        await session.execute(
            with_scope(select(ServiceRequest.channel, func.count()).select_from(ServiceRequest), scope, "request")
            .where(ServiceRequest.created_at >= _month_start_dt(moment))
            .group_by(ServiceRequest.channel)
        )
    ).all()
    counts = {str(getattr(channel, "value", channel)): int(count) for channel, count in rows}
    out = [
        {"channel": channel.value, "count": counts.pop(channel.value, 0)}
        for channel in (Channel.WHATSAPP, Channel.CALL, Channel.EMAIL, Channel.MANUAL)
    ]
    out.extend({"channel": channel, "count": count} for channel, count in counts.items())
    return out


async def renewals(session: AsyncSession, scope: Scope, *, today: date | None = None) -> list[dict[str, Any]]:
    """Policies expiring in the next 30 days — the renewal conversation, oldest deadline first."""
    moment = today or date.today()
    horizon = moment + timedelta(days=30)
    rows = (
        (
            await session.execute(
                with_scope(select(Policy), scope, "policy")
                .where(
                    Policy.status == PolicyStatus.ISSUED,
                    Policy.end_date.is_not(None),
                    Policy.end_date >= moment,
                    Policy.end_date <= horizon,
                )
                .order_by(Policy.end_date)
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "policy_id": row.id,
            "policy_no": row.policy_no,
            "client_name": row.client.name if row.client is not None else None,
            "insurer_name": row.insurer.name if row.insurer is not None else None,
            "end_date": row.end_date,
            "days_left": (row.end_date - moment).days if row.end_date else 0,
            "premium_amount": float(row.premium_amount or 0),
        }
        for row in rows
    ]
