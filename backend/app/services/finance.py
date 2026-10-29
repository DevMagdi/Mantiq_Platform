"""The R10 money layer: ledger, cashbox, customer AR, commission payouts (ADR-030).

Everything the office collects, owes or spends becomes a row in `ledger_entries`, and every balance on a
screen is a *sum of posted rows* — not a stored number that could drift. Four rules hold the module up:

* **One receipt per installment.** `installments.pay()` posts `premium_receivable`; the partial unique
  index on `(entry_type, installment_id) WHERE status = 'posted'` plus the installment's own `409` mean a
  double click cannot double the collected total. A void frees the slot, so re-paying after a void works.
* **A void is a state, not a deletion.** The row keeps its amount, author and reason; every sum filters
  `status = 'posted'`, which is why voiding reverses a cashbox balance "for free" and why history stays
  readable a year later.
* **Aging is measured from the due date, and not-yet-due money is not overdue.** The five buckets are
  `current`, `d0_30`, `d31_60`, `d61_90`, `d90_plus`; folding "due next month" into "0–30 overdue" would
  be a wrong number on the executive screen, which is the one thing R10 must not ship.
* **Paying a commission twice is impossible.** `commission_settlements.commission_id` is unique and the
  service refuses anything that is not `approved`, so the second call is a `409` with the payout it found.

Office-level rows (an expense with no client) are manager-only by scope: `ledger_predicate` matches on the
entry's client, and a rep's scope has no client to match them on (ADR-030 §6).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.pagination import Page, PageParams
from app.core.scope import Scope, build_scope
from app.domain.enums import (
    CommissionStatus,
    InstallmentStatus,
    LedgerDirection,
    LedgerEntryType,
    LedgerStatus,
    PaymentMethod,
    PolicyStatus,
    SettlementStatus,
)
from app.domain.permissions import FINANCE_PERIOD_CLOSE
from app.models.auth import User
from app.models.clients import Client
from app.models.finance import CashboxOpening, CommissionSettlement, FinancePeriodClose, LedgerEntry
from app.models.phase2 import Commission, Installment, Policy
from app.models.requests import RequestNumberCounter
from app.repositories.scope_filters import row_visible, with_scope
from app.services import audit

MONEY = Decimal("0.01")

#: bucket key → (lowest days overdue, highest days overdue); `None` is open-ended
BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("current", -10_000, 0),
    ("d0_30", 1, 30),
    ("d31_60", 31, 60),
    ("d61_90", 61, 90),
    ("d90_plus", 91, None),
)
BUCKET_KEYS: tuple[str, ...] = tuple(key for key, _, _ in BUCKETS)


def money(value: Any) -> Decimal:
    """Quantize once, at the edges, so a float never decides what a balance is."""
    return Decimal(str(value if value is not None else 0)).quantize(MONEY)


def bucket_for(days_overdue: int) -> str:
    for key, low, high in BUCKETS:
        if days_overdue >= low and (high is None or days_overdue <= high):
            return key
    return "d90_plus"


def empty_buckets() -> dict[str, dict[str, Any]]:
    return {key: {"bucket": key, "amount": 0.0, "count": 0} for key in BUCKET_KEYS}


def _add_to_bucket(buckets: dict[str, dict[str, Any]], key: str, amount: Decimal) -> None:
    buckets[key]["amount"] = round(buckets[key]["amount"] + float(amount), 2)
    buckets[key]["count"] = int(buckets[key]["count"]) + 1


# --------------------------------------------------------------------------- entry numbers ---------
async def next_entry_no(session: AsyncSession) -> str:
    """`LE-000123`, gap-free-ish, from a locked counter row (the pattern `services.requests` uses)."""
    if session.get_bind().dialect.name == "postgresql":
        await session.execute(sa.text("SELECT pg_advisory_xact_lock(hashtext('ledger'))"))
    row = (
        await session.execute(select(RequestNumberCounter).where(RequestNumberCounter.key == "ledger").with_for_update())
    ).scalar_one_or_none()
    if row is None:
        row = RequestNumberCounter(key="ledger", last_value=0)
        session.add(row)
        await session.flush()
    row.last_value += 1
    await session.flush()
    return f"LE-{row.last_value:06d}"


# --------------------------------------------------------------------------- posting ---------------
def check_direction(entry_type: LedgerEntryType, direction: LedgerDirection) -> None:
    """A receipt is never `out` and an expense is never `in` — catching it here keeps a mis-typed form
    from turning a payment into a collection, which would then show up as a wrong `collections_mtd`."""
    if entry_type in LedgerEntryType.money_in() and direction != LedgerDirection.IN:
        raise ValidationFailed(f"{entry_type.value} can only be posted as money in", details={"field": "direction", "expected": "in"})
    if entry_type in LedgerEntryType.money_out() and direction != LedgerDirection.OUT:
        raise ValidationFailed(f"{entry_type.value} can only be posted as money out", details={"field": "direction", "expected": "out"})


async def post_entry(
    session: AsyncSession,
    actor: User | None,
    *,
    entry_type: LedgerEntryType,
    direction: LedgerDirection,
    amount: Any,
    currency: str = "EGP",
    client_id: UUID | None = None,
    policy_id: UUID | None = None,
    installment_id: UUID | None = None,
    user_id: UUID | None = None,
    method: PaymentMethod | None = None,
    note: str | None = None,
    posted_at: datetime | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> LedgerEntry:
    """Write one movement. Validates the sign, the direction and the installment's single receipt."""
    # ADR-033: a closed month refuses new postings; only `finance.period_close` holders may post into
    # it, and that exception is audited under its own action so it can be found later.
    when = posted_at or datetime.now(UTC)
    closed = await period_close_for(session, when)
    if closed is not None and not (actor is not None and build_scope(actor).can(FINANCE_PERIOD_CLOSE)):
        raise Conflict(
            "This finance period is closed",
            details={"period": closed.period_key, "closed_by": str(closed.closed_by) if closed.closed_by else None},
        )
    value = money(amount)
    if value <= 0:
        # the API rejects this at the schema (422); the service rejects it too, because the seed, the
        # installment hook and any future job also post through here
        raise ValidationFailed("A ledger entry must be greater than zero", details={"field": "amount"})
    check_direction(entry_type, direction)

    if installment_id is not None:
        twice = (
            await session.execute(
                select(LedgerEntry).where(
                    LedgerEntry.installment_id == installment_id,
                    LedgerEntry.entry_type == entry_type,
                    LedgerEntry.status == LedgerStatus.POSTED,
                )
            )
        ).scalar_one_or_none()
        if twice is not None:
            raise Conflict(
                "This installment already has a posted entry",
                details={"entry_no": twice.entry_no, "installment_id": str(installment_id)},
            )

    entry = LedgerEntry(
        entry_no=await next_entry_no(session),
        entry_type=entry_type,
        direction=direction,
        amount=value,
        currency=currency or "EGP",
        status=LedgerStatus.POSTED,
        client_id=client_id,
        policy_id=policy_id,
        installment_id=installment_id,
        user_id=user_id,
        method=method,
        note=note,
        posted_at=when,
        posted_by=actor.id if actor else None,
    )
    session.add(entry)
    await session.flush()
    await audit.write(
        session,
        actor=actor,
        action="ledger.posted_in_closed_period" if closed is not None else "ledger.posted",
        entity_type="ledger_entry",
        entity_id=entry.id,
        after={
            "entry_no": entry.entry_no,
            "entry_type": entry.entry_type.value,
            "direction": entry.direction.value,
            "amount": entry.amount,
            "currency": entry.currency,
            "client_id": entry.client_id,
            "policy_id": entry.policy_id,
            "installment_id": entry.installment_id,
            "method": entry.method.value if entry.method else None,
        },
        ip=ip,
        user_agent=user_agent,
    )
    return entry


async def void_entry(
    session: AsyncSession,
    actor: User,
    entry_id: UUID,
    reason: str,
    *,
    scope: Scope,
    ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[LedgerEntry, dict[str, Any] | None]:
    """Take a posted entry back — the row *and* the business fact that produced it (R10.1).

    A void used to move `status` to `void` and stop there, which left the ledger and the plan disagreeing:
    the money was gone from every sum, but the installment it paid stayed `paid` — so the customer's plan
    still read "collected", and `installments.pay()` answered `409 already marked paid` when the office tried
    to take the money again. The reversal is now part of the same transaction:

    * a `premium_receivable` receipt reopens its installment (`paid_at = None`, `status = unpaid`), and paying
      it again is an ordinary payment that posts a new receipt with its own `entry_no`;
    * a `commission_payable` payout returns its settlement to `approved` and the commission to `approved`, so
      the payout can be made again instead of leaving an orphaned "paid" row behind a voided transfer;
    * an office entry (expense, adjustment) has no dependent row and is a pure ledger reversal.

    Each reopening writes its own audit row, so "why is this installment unpaid again?" has an answer with a
    name and a reason attached.
    """
    entry = await row_visible(session, select(LedgerEntry).where(LedgerEntry.id == entry_id), scope, "ledger")
    if entry is None:
        raise NotFound("Ledger entry not found")
    entry = cast("LedgerEntry", entry)
    # ADR-033: voiding rewrites a closed month's totals, so it follows the same rule as posting.
    closed = await period_close_for(session, entry.posted_at)
    if closed is not None and not build_scope(actor).can(FINANCE_PERIOD_CLOSE):
        raise Conflict(
            "This finance period is closed",
            details={"period": closed.period_key, "closed_by": str(closed.closed_by) if closed.closed_by else None},
        )
    if entry.status == LedgerStatus.VOID:
        raise Conflict("This entry was already voided", details={"entry_no": entry.entry_no, "voided_at": str(entry.voided_at)})

    before = {"status": entry.status.value, "amount": float(entry.amount)}
    entry.status = LedgerStatus.VOID
    entry.voided_at = datetime.now(UTC)
    entry.voided_by = actor.id
    entry.void_reason = reason
    await session.flush()

    # R10.1's `_reopen_what_the_entry_paid` is the void↔installment reversal (ADR-031 adopts it as-is:
    # same rule, plus its own `installment.reopened` audit row and commission-settlement reopening)
    reopened = await _reopen_what_the_entry_paid(session, actor, entry, reason, ip=ip, user_agent=user_agent)
    await audit.write(
        session,
        actor=actor,
        action="ledger.voided",
        entity_type="ledger_entry",
        entity_id=entry.id,
        before=before,
        after={"status": entry.status.value, "reason": reason, "voided_at": entry.voided_at, "reopened": reopened},
        ip=ip,
        user_agent=user_agent,
    )
    return entry, reopened


async def _reopen_what_the_entry_paid(
    session: AsyncSession,
    actor: User,
    entry: LedgerEntry,
    reason: str,
    *,
    ip: str | None,
    user_agent: str | None,
) -> dict[str, Any] | None:
    """Undo the business fact behind a voided entry, in the same transaction (see `void_entry`).

    Returns a small description of what was reopened so the caller (and the audit trail) can say it out loud,
    or `None` for an entry that nothing depends on (an expense, a manual adjustment).
    """
    if entry.entry_type == LedgerEntryType.PREMIUM_RECEIVABLE and entry.installment_id is not None:
        installment = await session.get(Installment, entry.installment_id)
        if installment is None or installment.paid_at is None:
            return None
        installment.paid_at = None
        # the plan's own rule for an unpaid row: late once its due date has passed, upcoming before that
        installment.status = InstallmentStatus.LATE if installment.due_date < date.today() else InstallmentStatus.UPCOMING
        await session.flush()
        await audit.write(
            session,
            actor=actor,
            action="installment.reopened",
            entity_type="installment",
            entity_id=installment.id,
            before={"status": InstallmentStatus.PAID.value},
            after={
                "status": installment.status.value,
                "policy_id": str(installment.policy_id),
                "reason": reason,
                "voided_entry_no": entry.entry_no,
                "voided_entry_id": str(entry.id),
            },
            ip=ip,
            user_agent=user_agent,
        )
        return {"kind": "installment", "id": str(installment.id), "status": installment.status.value}

    if entry.entry_type == LedgerEntryType.COMMISSION_PAYABLE:
        settlement = (
            await session.execute(select(CommissionSettlement).where(CommissionSettlement.ledger_entry_id == entry.id))
        ).scalar_one_or_none()
        if settlement is None or settlement.status != SettlementStatus.PAID:
            return None
        commission = await session.get(Commission, settlement.commission_id)
        settlement.status = SettlementStatus.APPROVED
        settlement.paid_at = None
        settlement.paid_by = None
        settlement.ledger_entry_id = None
        settlement.method = None
        if commission is not None:
            # `commissions.status` is the *accrual* (accrued | settled | clawed_back): a voided payout puts
            # the accrual back to "owed", which is what the commissions screen and the executive KPI read
            commission.status = CommissionStatus.ACCRUED
            commission.settled_at = None
        await session.flush()
        await audit.write(
            session,
            actor=actor,
            action="commission.reopened",
            entity_type="commission_settlement",
            entity_id=settlement.id,
            before={"status": SettlementStatus.PAID.value},
            after={
                "status": settlement.status.value,
                "commission_id": str(settlement.commission_id),
                "reason": reason,
                "voided_entry_no": entry.entry_no,
            },
            ip=ip,
            user_agent=user_agent,
        )
        return {"kind": "commission", "id": str(settlement.id), "status": settlement.status.value}

    return None


PERIODS = ("month", "quarter", "semiannual", "year")


def period_range(period: str, *, today: date | None = None) -> tuple[date, date, str]:
    """(start, end_inclusive, label) for a named period anchored at `today`; unknown period → 422."""
    import calendar as _cal

    day = today or date.today()
    if period == "month":
        return day.replace(day=1), day.replace(day=_cal.monthrange(day.year, day.month)[1]), f"{day.year:04d}-{day.month:02d}"
    if period == "quarter":
        first_month = 3 * ((day.month - 1) // 3) + 1
        end_month = first_month + 2
        return (
            date(day.year, first_month, 1),
            date(day.year, end_month, _cal.monthrange(day.year, end_month)[1]),
            f"{day.year:04d} Q{(day.month - 1) // 3 + 1}",
        )
    if period == "semiannual":
        half = 1 if day.month <= 6 else 2
        return (
            date(day.year, 1 if half == 1 else 7, 1),
            date(day.year, 6 if half == 1 else 12, 30 if half == 1 else 31),
            f"{day.year:04d} H{half}",
        )
    if period == "year":
        return date(day.year, 1, 1), date(day.year, 12, 31), f"{day.year:04d}"
    raise ValidationFailed("Unknown period", details={"field": "period", "allowed": list(PERIODS)})


async def spend_by_period(session: AsyncSession, scope: Scope, period: str, *, today: date | None = None) -> dict[str, Any]:
    """Posted-only in/out totals for one period, grouped by entry type (ERP hub, ADR-031).

    One grouped aggregate (`entry_type × direction × currency`) rather than one sum per type: at
    50k+ rows this is a single indexed scan, which is what the load gate measures.
    """
    start, end, label = period_range(period, today=today)
    start_dt = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
    end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    stmt = with_scope(
        select(LedgerEntry.entry_type, LedgerEntry.direction, LedgerEntry.currency, func.coalesce(func.sum(LedgerEntry.amount), 0))
        .select_from(LedgerEntry)
        .group_by(LedgerEntry.entry_type, LedgerEntry.direction, LedgerEntry.currency),
        scope,
        "ledger",
    ).where(LedgerEntry.status == LedgerStatus.POSTED, LedgerEntry.posted_at >= start_dt, LedgerEntry.posted_at < end_dt)
    by_type_map: dict[str, dict[str, Any]] = {
        t.value: {"entry_type": t.value, "in_total": 0.0, "out_total": 0.0, "net": 0.0} for t in LedgerEntryType
    }
    total_in = Decimal("0")
    total_out = Decimal("0")
    currency = "EGP"
    for entry_type, direction, row_currency, amount in (await session.execute(stmt)).all():
        value = money(amount)
        currency = row_currency or currency
        row = by_type_map[entry_type.value]
        if direction == LedgerDirection.IN:
            row["in_total"] = float(money(row["in_total"]) + value)
            total_in += value
        else:
            row["out_total"] = float(money(row["out_total"]) + value)
            total_out += value
    by_type: list[dict[str, Any]] = []
    for row in by_type_map.values():
        row["net"] = float(money(row["in_total"]) - money(row["out_total"]))
        by_type.append(row)
    return {
        "currency": currency,
        "period": period,
        "label": label,
        "start": start,
        "end": end,
        "total_in": float(total_in),
        "total_out": float(total_out),
        "net": float(total_in - total_out),
        "by_type": by_type,
    }


# --------------------------------------------------------------------------- reads ----------------
def entry_brief(row: LedgerEntry) -> dict[str, Any]:
    client = row.client
    policy = row.policy
    return {
        "id": row.id,
        "entry_no": row.entry_no,
        "entry_type": row.entry_type.value,
        "direction": row.direction.value,
        "amount": float(row.amount),
        "currency": row.currency,
        "status": row.status.value,
        "client_id": row.client_id,
        "client_name": client.name if client is not None else None,
        "policy_id": row.policy_id,
        "policy_no": policy.policy_no if policy is not None else None,
        "installment_id": row.installment_id,
        "installment_sequence": (row.installment.sequence_no if row.installment is not None else None),
        "user_id": row.user_id,
        "user_name": row.user.full_name if row.user is not None else None,
        "method": row.method.value if row.method is not None else None,
        "note": row.note,
        "posted_at": row.posted_at,
        "posted_by_name": (row.posted_by_user.full_name if row.posted_by_user is not None else None),
        "voided_at": row.voided_at,
        "void_reason": row.void_reason,
    }


async def list_entries(
    session: AsyncSession,
    scope: Scope,
    params: PageParams,
    *,
    search: str | None = None,
    status: str | None = None,
    entry_type: str | None = None,
    direction: str | None = None,
    client_id: UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[dict[str, Any]]:
    stmt = with_scope(select(LedgerEntry), scope, "ledger")
    count_stmt = with_scope(select(func.count()).select_from(LedgerEntry), scope, "ledger")
    conditions = _ledger_filters(
        search=search,
        status=status,
        entry_type=entry_type,
        direction=direction,
        client_id=client_id,
        date_from=date_from,
        date_to=date_to,
    )
    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    total = int((await session.execute(count_stmt)).scalar_one() or 0)
    rows = (
        (
            await session.execute(
                stmt.order_by(LedgerEntry.posted_at.desc(), LedgerEntry.entry_no.desc()).offset(params.offset).limit(params.page_size)
            )
        )
        .scalars()
        .all()
    )
    return Page.build([entry_brief(row) for row in rows], total, params)


def _ledger_filters(
    *,
    search: str | None,
    status: str | None,
    entry_type: str | None,
    direction: str | None,
    client_id: UUID | None,
    date_from: date | None,
    date_to: date | None,
) -> list[Any]:
    conditions: list[Any] = []
    if search:
        like = f"%{search.strip()}%"
        conditions.append(or_(LedgerEntry.entry_no.ilike(like), LedgerEntry.note.ilike(like)))
    if status:
        conditions.append(LedgerEntry.status == LedgerStatus(status))
    if entry_type:
        conditions.append(LedgerEntry.entry_type == LedgerEntryType(entry_type))
    if direction:
        conditions.append(LedgerEntry.direction == LedgerDirection(direction))
    if client_id:
        conditions.append(LedgerEntry.client_id == client_id)
    if date_from:
        conditions.append(LedgerEntry.posted_at >= datetime.combine(date_from, datetime.min.time(), tzinfo=UTC))
    if date_to:
        conditions.append(LedgerEntry.posted_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time(), tzinfo=UTC))
    return conditions


async def _sum(
    session: AsyncSession,
    scope: Scope,
    *conditions: Any,
) -> Decimal:
    stmt = with_scope(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).select_from(LedgerEntry), scope, "ledger").where(
        LedgerEntry.status == LedgerStatus.POSTED, *conditions
    )
    return money((await session.execute(stmt)).scalar_one())


# ------------------------------------------------------------- period close + opening (ADR-033) --
def period_key_of(moment: datetime) -> str:
    return f"{moment.year:04d}-{moment.month:02d}"


async def period_close_for(session: AsyncSession, moment: datetime) -> FinancePeriodClose | None:
    return (
        await session.execute(select(FinancePeriodClose).where(FinancePeriodClose.period_key == period_key_of(moment)))
    ).scalar_one_or_none()


async def list_period_closes(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (await session.execute(select(FinancePeriodClose).order_by(FinancePeriodClose.period_key.desc()))).scalars().all()
    return [{"period": row.period_key, "note": row.note, "closed_by": row.closed_by, "closed_at": row.created_at} for row in rows]


async def close_period(
    session: AsyncSession, actor: User, period_key: str, *, note: str | None = None, ip: str | None = None
) -> dict[str, Any]:
    import re

    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period_key or ""):
        raise ValidationFailed("Period must be YYYY-MM", details={"field": "period"})
    existing = (await session.execute(select(FinancePeriodClose).where(FinancePeriodClose.period_key == period_key))).scalar_one_or_none()
    if existing is not None:
        raise Conflict("This finance period is already closed", details={"period": period_key})
    row = FinancePeriodClose(period_key=period_key, note=note, closed_by=actor.id)
    session.add(row)
    await session.flush()
    await audit.write(
        session,
        actor=actor,
        action="finance.period_closed",
        entity_type="finance_period",
        entity_id=row.id,
        after={"period": period_key, "note": note},
        ip=ip,
    )
    return {"period": period_key, "note": note, "closed_by": actor.id, "closed_at": row.created_at}


async def reopen_period(session: AsyncSession, actor: User, period_key: str, *, ip: str | None = None) -> dict[str, Any]:
    row = (await session.execute(select(FinancePeriodClose).where(FinancePeriodClose.period_key == period_key))).scalar_one_or_none()
    if row is None:
        raise NotFound("This finance period is not closed")
    await audit.write(
        session,
        actor=actor,
        action="finance.period_reopened",
        entity_type="finance_period",
        entity_id=row.id,
        before={"period": period_key},
        ip=ip,
    )
    await session.delete(row)
    await session.flush()
    return {"period": period_key, "reopened": True}


async def set_opening_balance(
    session: AsyncSession,
    actor: User,
    *,
    amount: Any,
    effective_at: datetime,
    note: str | None = None,
    ip: str | None = None,
) -> dict[str, Any]:
    value = money(amount)
    if value < 0:
        raise ValidationFailed("Opening balance cannot be negative", details={"field": "amount"})
    row = CashboxOpening(amount=value, effective_at=effective_at, note=note, created_by=actor.id)
    session.add(row)
    await session.flush()
    await audit.write(
        session,
        actor=actor,
        action="cashbox.opening_set",
        entity_type="cashbox_opening",
        entity_id=row.id,
        after={"amount": float(value), "effective_at": effective_at.isoformat(), "note": note},
        ip=ip,
    )
    return {"id": row.id, "amount": float(value), "currency": row.currency, "effective_at": effective_at, "note": note}


async def _latest_opening(session: AsyncSession, as_of: datetime) -> CashboxOpening | None:
    return (
        (
            await session.execute(
                select(CashboxOpening)
                .where(CashboxOpening.effective_at <= as_of)
                .order_by(CashboxOpening.effective_at.desc(), CashboxOpening.created_at.desc())
            )
        )
        .scalars()
        .first()
    )


async def cashbox(session: AsyncSession, scope: Scope, *, as_of: datetime | None = None) -> dict[str, Any]:
    """The office's cash position: posted **cash** rows only, in minus out.

    Bank and wallet receipts are real money but they are not in the drawer, so they are reported in
    `by_method` instead of being folded into `balance` — a cashbox total that includes a bank transfer is
    a number that cannot be counted at closing time.
    """
    moment = as_of or datetime.now(UTC)
    today_start = datetime.combine(moment.date(), datetime.min.time(), tzinfo=UTC)
    month_start = datetime.combine(moment.date().replace(day=1), datetime.min.time(), tzinfo=UTC)
    opening = await _latest_opening(session, moment)
    opening_at = opening.effective_at if opening is not None else None

    # One grouped aggregate for every total this screen prints (C1): method × direction, plus the
    # cash-only today/month windows via conditional sums — instead of ten separate SUM round-trips.
    def _window_sum(start: datetime) -> Any:
        return func.coalesce(func.sum(sa.case((LedgerEntry.posted_at >= start, LedgerEntry.amount), else_=0)), 0)

    agg = with_scope(
        select(
            LedgerEntry.method,
            LedgerEntry.direction,
            func.coalesce(func.sum(LedgerEntry.amount), 0),
            _window_sum(today_start),
            _window_sum(month_start),
        ).select_from(LedgerEntry),
        scope,
        "ledger",
    ).where(LedgerEntry.status == LedgerStatus.POSTED, LedgerEntry.posted_at <= moment)
    if opening_at is not None:
        agg = agg.where(LedgerEntry.posted_at >= opening_at)
    agg = agg.group_by(LedgerEntry.method, LedgerEntry.direction)
    totals: dict[tuple[str, str], tuple[Decimal, Decimal, Decimal]] = {}
    for method, direction, total, today_total, month_total in (await session.execute(agg)).all():
        if method is None:
            continue
        totals[(method.value, direction.value)] = (money(total), money(today_total), money(month_total))

    def _t(method: PaymentMethod, direction: LedgerDirection) -> tuple[Decimal, Decimal, Decimal]:
        return totals.get((method.value, direction.value), (Decimal("0"), Decimal("0"), Decimal("0")))

    by_method: list[dict[str, Any]] = []
    for method in (PaymentMethod.CASH, PaymentMethod.BANK, PaymentMethod.WALLET, PaymentMethod.OTHER):
        in_total, _, _ = _t(method, LedgerDirection.IN)
        out_total, _, _ = _t(method, LedgerDirection.OUT)
        by_method.append(
            {"method": method.value, "in_total": float(in_total), "out_total": float(out_total), "balance": float(in_total - out_total)}
        )
    cash_in, cash_today_in, cash_month_in = _t(PaymentMethod.CASH, LedgerDirection.IN)
    cash_out, cash_today_out, cash_month_out = _t(PaymentMethod.CASH, LedgerDirection.OUT)
    opening_amount = money(opening.amount) if opening is not None else Decimal("0")

    movements = (
        (
            await session.execute(
                with_scope(select(LedgerEntry), scope, "ledger")
                .where(LedgerEntry.method == PaymentMethod.CASH)
                .order_by(LedgerEntry.posted_at.desc(), LedgerEntry.entry_no.desc())
                .limit(25)
            )
        )
        .scalars()
        .all()
    )
    # R10.1: the screen has to be able to say *what* the number is. `since` is the first posted cash movement
    # this balance was accumulated from, so "net movement since 1 June" is a sentence the API can support —
    # there is no opening balance in the model and no daily close, and the screen says both out loud.
    since = (
        await session.execute(
            with_scope(select(func.min(LedgerEntry.posted_at)).select_from(LedgerEntry), scope, "ledger").where(
                LedgerEntry.status == LedgerStatus.POSTED, LedgerEntry.method == PaymentMethod.CASH
            )
        )
    ).scalar_one()
    return {
        "currency": "EGP",
        "as_of": moment,
        # Transparency (ADR-033/E4): with an opening counted, the balance is opening ± movements since
        # it; until then the screen keeps saying, in so many words, that this is net movements only.
        "is_net_movements_only": opening is None,
        "balance_note": "opening_plus_movements" if opening is not None else "net_movements_only",
        "opening_balance": float(opening_amount),
        "opening_effective_at": opening_at,
        "since": opening_at or since,
        "balance": float(opening_amount + cash_in - cash_out),
        "cash_in_total": float(cash_in),
        "cash_out_total": float(cash_out),
        "today_in": float(cash_today_in),
        "today_out": float(cash_today_out),
        "month_in": float(cash_month_in),
        "month_out": float(cash_month_out),
        "by_method": by_method,
        "movements": [entry_brief(row) for row in movements],
    }


# --------------------------------------------------------------------------- customer AR -----------
def _open_installments(scope: Scope) -> Any:
    """Unpaid installments of *issued* policies — drafts owe nothing, so they are not receivables.

    The client id and policy number are selected **with** the row instead of reached through
    `installment.policy`: an async session cannot lazy-load, and a relationship that happens to be warm in
    one request is not a contract (this raised `MissingGreenlet` on the AR drill-down).
    """
    return (
        with_scope(
            select(Installment, Policy.client_id, Policy.policy_no, Policy.id).join(Policy, Installment.policy_id == Policy.id),
            scope,
            "policy",
        )
        .where(
            Policy.status == PolicyStatus.ISSUED,
            Installment.paid_at.is_(None),
            Installment.status != InstallmentStatus.PAID,
        )
        .order_by(Installment.due_date)
    )


async def _ar_rows(session: AsyncSession, scope: Scope, *, today: date | None = None) -> list[dict[str, Any]]:
    moment = today or date.today()
    rows = (await session.execute(_open_installments(scope))).all()
    out: list[dict[str, Any]] = []
    for installment, client_id, policy_no, policy_id in rows:
        days = (moment - installment.due_date).days
        out.append(
            {
                "installment": installment,
                "client_id": client_id,
                "policy_no": policy_no,
                "policy_id": policy_id,
                "days_overdue": max(days, 0),
                "days_signed": days,
                "bucket": bucket_for(days),
                "amount": money(installment.amount),
            }
        )
    return out


async def ar_summary(session: AsyncSession, scope: Scope, *, today: date | None = None) -> dict[str, Any]:
    moment = today or date.today()
    rows = await _ar_rows(session, scope, today=moment)
    buckets = empty_buckets()
    for row in rows:
        _add_to_bucket(buckets, row["bucket"], row["amount"])
    outstanding = sum(float(row["amount"]) for row in rows)
    overdue = sum(float(row["amount"]) for row in rows if row["days_signed"] > 0)
    clients = {row["client_id"] for row in rows}
    overdue_clients = {row["client_id"] for row in rows if row["days_signed"] > 0}
    return {
        "currency": "EGP",
        "as_of": moment,
        "outstanding_total": round(outstanding, 2),
        "overdue_total": round(overdue, 2),
        "current_total": round(outstanding - overdue, 2),
        "clients_count": len(clients),
        "overdue_clients_count": len(overdue_clients),
        "buckets": [buckets[key] for key in BUCKET_KEYS],
    }


async def _last_payments(session: AsyncSession, scope: Scope, client_ids: list[UUID]) -> dict[UUID, datetime]:
    if not client_ids:
        return {}
    stmt = (
        with_scope(select(LedgerEntry), scope, "ledger")
        .where(
            LedgerEntry.client_id.in_(client_ids),
            LedgerEntry.status == LedgerStatus.POSTED,
            LedgerEntry.direction == LedgerDirection.IN,
        )
        .order_by(LedgerEntry.posted_at.desc())
    )
    out: dict[UUID, datetime] = {}
    for row in (await session.execute(stmt)).scalars().all():
        if row.client_id is not None and row.client_id not in out:
            out[row.client_id] = row.posted_at
    return out


def _client_rollup(rows: list[dict[str, Any]], *, today: date) -> dict[UUID, dict[str, Any]]:
    rollup: dict[UUID, dict[str, Any]] = {}
    for row in rows:
        client_id = row["client_id"]
        if client_id is None:
            continue
        entry = rollup.setdefault(
            client_id,
            {
                "client_id": client_id,
                "outstanding_total": 0.0,
                "overdue_total": 0.0,
                "current_total": 0.0,
                "oldest_due_date": None,
                "installments_count": 0,
                "buckets": empty_buckets(),
            },
        )
        amount = float(row["amount"])
        entry["outstanding_total"] = round(entry["outstanding_total"] + amount, 2)
        if row["days_signed"] > 0:
            entry["overdue_total"] = round(entry["overdue_total"] + amount, 2)
        else:
            entry["current_total"] = round(entry["current_total"] + amount, 2)
        due = row["installment"].due_date
        if entry["oldest_due_date"] is None or due < entry["oldest_due_date"]:
            entry["oldest_due_date"] = due
        entry["installments_count"] += 1
        _add_to_bucket(entry["buckets"], row["bucket"], row["amount"])
    for entry in rollup.values():
        entry["buckets"] = [entry["buckets"][key] for key in BUCKET_KEYS]
    return rollup


async def ar_clients(
    session: AsyncSession,
    scope: Scope,
    params: PageParams,
    *,
    search: str | None = None,
    bucket: str | None = None,
    today: date | None = None,
) -> Page[dict[str, Any]]:
    """One row per client: what they owe, how late, and how it ages — sorted by the oldest money first."""
    moment = today or date.today()
    rows = await _ar_rows(session, scope, today=moment)
    if bucket:
        rows = [row for row in rows if row["bucket"] == bucket]
    rollup = _client_rollup(rows, today=moment)
    names = await _client_names(session, scope, list(rollup))
    last_paid = await _last_payments(session, scope, list(rollup))
    items: list[dict[str, Any]] = []
    for client_id, entry in rollup.items():
        name = names.get(client_id, {"name": "—", "owner": None})
        if search and search.strip().lower() not in str(name["name"]).lower():
            continue
        items.append(
            {
                **entry,
                "client_name": name["name"],
                "owner_name": name["owner"],
                "last_payment_at": last_paid.get(client_id),
            }
        )
    items.sort(key=lambda row: (-row["overdue_total"], -row["outstanding_total"], row["client_name"]))
    total = len(items)
    window = items[params.offset : params.offset + params.page_size]
    return Page.build(window, total, params)


async def _client_names(session: AsyncSession, scope: Scope, client_ids: list[UUID]) -> dict[UUID, dict[str, Any]]:
    if not client_ids:
        return {}
    rows = (await session.execute(with_scope(select(Client), scope, "client").where(Client.id.in_(client_ids)))).scalars().all()
    owner_ids = {row.account_owner_id for row in rows if row.account_owner_id is not None}
    owners: dict[UUID, str] = {}
    if owner_ids:
        for user in (await session.execute(select(User).where(User.id.in_(owner_ids)))).scalars().all():
            owners[user.id] = user.full_name
    return {row.id: {"name": row.name, "owner": owners.get(row.account_owner_id) if row.account_owner_id else None} for row in rows}


async def ar_client(session: AsyncSession, scope: Scope, client_id: UUID, *, today: date | None = None) -> dict[str, Any]:
    """The drill-down behind one AR row: the installments themselves and the receipts against them."""
    moment = today or date.today()
    client = await row_visible(session, select(Client).where(Client.id == client_id), scope, "client")
    if client is None:
        raise NotFound("Client not found")
    rows = [row for row in await _ar_rows(session, scope, today=moment) if row["client_id"] == client_id]
    rollup = _client_rollup(rows, today=moment).get(client_id)
    names = await _client_names(session, scope, [client_id])
    last_paid = await _last_payments(session, scope, [client_id])
    payments = (
        (
            await session.execute(
                with_scope(select(LedgerEntry), scope, "ledger")
                .where(
                    LedgerEntry.client_id == client_id,
                    LedgerEntry.status == LedgerStatus.POSTED,
                    LedgerEntry.direction == LedgerDirection.IN,
                )
                .order_by(LedgerEntry.posted_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    return {
        "client_id": client_id,
        "client_name": names.get(client_id, {}).get("name", client.name),
        "owner_name": names.get(client_id, {}).get("owner"),
        "outstanding_total": rollup["outstanding_total"] if rollup else 0.0,
        "overdue_total": rollup["overdue_total"] if rollup else 0.0,
        "current_total": rollup["current_total"] if rollup else 0.0,
        "oldest_due_date": rollup["oldest_due_date"] if rollup else None,
        "installments_count": rollup["installments_count"] if rollup else 0,
        "last_payment_at": last_paid.get(client_id),
        "buckets": rollup["buckets"] if rollup else [empty_buckets()[key] for key in BUCKET_KEYS],
        "installments": [
            {
                "installment_id": row["installment"].id,
                "policy_id": row["policy_id"],
                "policy_no": row["policy_no"],
                "sequence_no": row["installment"].sequence_no,
                "due_date": row["installment"].due_date,
                "amount": float(row["amount"]),
                "days_overdue": row["days_overdue"],
                "bucket": row["bucket"],
                "status": row["installment"].status.value,
            }
            for row in rows
        ],
        "payments": [entry_brief(row) for row in payments],
    }


# --------------------------------------------------------------------------- commissions -----------
def settlement_status(commission: Commission, settlement: CommissionSettlement | None) -> str:
    if settlement is None:
        return SettlementStatus.ACCRUED.value if commission.status == CommissionStatus.ACCRUED else commission.status.value
    return settlement.status.value


def _settlement_row(commission: Commission, policy: Policy | None, settlement: CommissionSettlement | None) -> dict[str, Any]:
    """One commission in the shape the list, the approve response and the pay response all render."""
    client = policy.client if policy is not None else None
    return {
        "id": commission.id,
        "commission_id": commission.id,
        "settlement_id": settlement.id if settlement else None,
        "status": settlement_status(commission, settlement),
        "amount": float(commission.amount),
        "currency": settlement.currency if settlement else "EGP",
        "payee_user_id": (settlement.payee_user_id if settlement else None) or (client.account_owner_id if client else None),
        "payee_name": (settlement.payee.full_name if settlement is not None and settlement.payee is not None else None),
        "policy_id": commission.policy_id,
        "policy_no": policy.policy_no if policy is not None else None,
        "client_id": policy.client_id if policy is not None else None,
        "client_name": client.name if client is not None else None,
        "installment_id": commission.installment_id,
        "accrued_at": commission.created_at,
        "approved_at": settlement.approved_at if settlement else None,
        "paid_at": settlement.paid_at if settlement else commission.settled_at,
        "ledger_entry_id": settlement.ledger_entry_id if settlement else None,
        "ledger_entry_no": (settlement.ledger_entry.entry_no if settlement is not None and settlement.ledger_entry is not None else None),
        "method": settlement.method.value if settlement is not None and settlement.method else None,
        "note": settlement.note if settlement else None,
    }


async def _commission_policy(session: AsyncSession, scope: Scope, commission: Commission) -> Policy:
    policy = await row_visible(session, select(Policy).where(Policy.id == commission.policy_id), scope, "policy")
    if policy is None:
        raise NotFound("Commission not found")
    return cast("Policy", policy)


async def ensure_settlement(session: AsyncSession, commission: Commission, policy: Policy) -> CommissionSettlement:
    """The payout row for one commission — created on first approval, never duplicated (unique index)."""
    settlement = (
        await session.execute(select(CommissionSettlement).where(CommissionSettlement.commission_id == commission.id))
    ).scalar_one_or_none()
    if settlement is not None:
        return settlement
    settlement = CommissionSettlement(
        commission_id=commission.id,
        status=SettlementStatus.ACCRUED,
        amount=money(commission.amount),
        currency="EGP",
        payee_user_id=policy.client.account_owner_id if policy.client is not None else None,
    )
    session.add(settlement)
    await session.flush()
    return settlement


async def list_commissions(
    session: AsyncSession,
    scope: Scope,
    params: PageParams,
    *,
    status: str | None = None,
    search: str | None = None,
) -> Page[dict[str, Any]]:
    """Every commission with the state of its payout. `id` is the commission id the pay route takes."""
    stmt = with_scope(select(Commission), scope, "commission").order_by(Commission.created_at.desc())
    rows = (await session.execute(stmt)).scalars().all()
    settlements = {row.commission_id: row for row in (await session.execute(select(CommissionSettlement))).scalars().all()}
    policy_ids = {row.policy_id for row in rows}
    policies: dict[UUID, Policy] = {}
    if policy_ids:
        for policy in (await session.execute(with_scope(select(Policy), scope, "policy").where(Policy.id.in_(policy_ids)))).scalars().all():
            policies[policy.id] = policy
    items: list[dict[str, Any]] = []
    for row in rows:
        policy = policies.get(row.policy_id)
        settlement = settlements.get(row.id)
        state = settlement_status(row, settlement)
        if status and state != status:
            continue
        item = _settlement_row(row, policy, settlement)
        if search and search.strip().lower() not in f"{item['client_name'] or ''} {item['policy_no'] or ''}".lower():
            continue
        items.append(item)
    total = len(items)
    return Page.build(items[params.offset : params.offset + params.page_size], total, params)


async def _commission_in_scope(session: AsyncSession, scope: Scope, commission_id: UUID) -> tuple[Commission, Policy]:
    commission = await row_visible(session, select(Commission).where(Commission.id == commission_id), scope, "commission")
    if commission is None:
        raise NotFound("Commission not found")
    policy = await _commission_policy(session, scope, commission)
    return commission, policy


async def approve_commission(
    session: AsyncSession,
    actor: User,
    scope: Scope,
    commission_id: UUID,
    *,
    note: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """`accrued → approved`. Anything else is a `409` that names the state it found."""
    commission, policy = await _commission_in_scope(session, scope, commission_id)
    settlement = await ensure_settlement(session, commission, policy)
    if settlement.status == SettlementStatus.APPROVED:
        raise Conflict("This commission is already approved", details={"paid_at": None, "status": settlement.status.value})
    if settlement.status != SettlementStatus.ACCRUED:
        raise Conflict("Only an accrued commission can be approved", details={"status": settlement.status.value})
    settlement.status = SettlementStatus.APPROVED
    settlement.approved_at = datetime.now(UTC)
    settlement.approved_by = actor.id
    if note:
        settlement.note = note
    await session.flush()
    await audit.write(
        session,
        actor=actor,
        action="commission.approved",
        entity_type="commission_settlement",
        entity_id=settlement.id,
        before={"status": SettlementStatus.ACCRUED.value},
        after={"status": settlement.status.value, "amount": settlement.amount, "commission_id": commission.id},
        ip=ip,
        user_agent=user_agent,
    )
    return _settlement_row(commission, policy, settlement)


async def pay_commission(
    session: AsyncSession,
    actor: User,
    scope: Scope,
    commission_id: UUID,
    *,
    method: PaymentMethod = PaymentMethod.BANK,
    note: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """`approved → paid`, writing the outgoing ledger row in the same transaction. Paying twice is a `409`."""
    commission, policy = await _commission_in_scope(session, scope, commission_id)
    settlement = await ensure_settlement(session, commission, policy)
    if settlement.status == SettlementStatus.PAID or commission.status == CommissionStatus.SETTLED:
        raise Conflict(
            "This commission was already paid",
            details={
                "paid_at": settlement.paid_at.isoformat() if settlement.paid_at else None,
                "ledger_entry_id": str(settlement.ledger_entry_id) if settlement.ledger_entry_id else None,
            },
        )
    if settlement.status == SettlementStatus.CANCELLED:
        raise Conflict("This commission was cancelled", details={"status": settlement.status.value})
    if settlement.status != SettlementStatus.APPROVED:
        raise Conflict("Approve the commission before paying it", details={"status": settlement.status.value})

    entry = await post_entry(
        session,
        actor,
        entry_type=LedgerEntryType.COMMISSION_PAYABLE,
        direction=LedgerDirection.OUT,
        amount=settlement.amount,
        currency=settlement.currency,
        client_id=policy.client_id,
        policy_id=policy.id,
        user_id=settlement.payee_user_id,
        method=method,
        note=note or f"عمولة {policy.policy_no or ''}".strip(),
        ip=ip,
        user_agent=user_agent,
    )
    settlement.status = SettlementStatus.PAID
    settlement.paid_at = datetime.now(UTC)
    settlement.paid_by = actor.id
    settlement.method = method
    settlement.ledger_entry = entry
    if note:
        settlement.note = note
    # the accrual screen (screen 12) reads `commissions.status`, so paying keeps that row honest too
    commission.status = CommissionStatus.SETTLED
    commission.settled_at = settlement.paid_at
    await session.flush()
    await audit.write(
        session,
        actor=actor,
        action="commission.paid",
        entity_type="commission_settlement",
        entity_id=settlement.id,
        before={"status": SettlementStatus.APPROVED.value},
        after={
            "status": settlement.status.value,
            "amount": settlement.amount,
            "method": method.value,
            "ledger_entry_no": entry.entry_no,
            "payee_user_id": settlement.payee_user_id,
        },
        ip=ip,
        user_agent=user_agent,
    )
    return _settlement_row(commission, policy, settlement)
