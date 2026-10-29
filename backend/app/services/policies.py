"""Policy creation, and with it the installment plan and the commission row (spec §2/§8, ADR-016 §6).

Before this module screen 12 could only *read*: the policies in the demo came from the seeder, so nothing
in the product answered "what happens when a policy is issued". The answer is that a policy without an
installment schedule is a number on a card, so the schedule and the commission accrual are written in the
**same transaction** as the policy — three separate commits would leave a half-written policy whenever one
of them failed, and a policy with no schedule is indistinguishable from a policy nobody has billed yet.

Rounding rule: every installment is `premium / n` rounded to the gros, and the **last one absorbs the
remainder**, so the schedule sums to the premium exactly. Rounding on all n rows would silently lose up to
`n-1` gros per policy, which is the kind of drift that ends in a reconciliation meeting.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound, ValidationFailed
from app.core.scope import Scope
from app.domain.enums import CommissionStatus, InstallmentStatus, PaymentPlan, PolicyStatus
from app.models.auth import User
from app.models.clients import Client
from app.models.masters import InsuranceType, Insurer
from app.models.phase2 import Commission, Installment, Policy
from app.repositories.scope_filters import row_visible
from app.services import audit, policy_settings

INSTALLMENTS_BY_PLAN: dict[str, int] = {
    PaymentPlan.MONTHLY.value: 12,
    PaymentPlan.QUARTERLY.value: 4,
    PaymentPlan.SEMIANNUAL.value: 2,
    PaymentPlan.CASH.value: 1,
}
MONTHS_PER_PLAN: dict[str, int] = {
    PaymentPlan.MONTHLY.value: 1,
    PaymentPlan.QUARTERLY.value: 3,
    PaymentPlan.SEMIANNUAL.value: 6,
    PaymentPlan.CASH.value: 0,
}

_TWOPLACES = Decimal("0.01")


def _money(value: Decimal | float | int | str) -> Decimal:
    return Decimal(str(value)).quantize(_TWOPLACES, rounding=ROUND_HALF_UP)


def add_months(start: date, months: int) -> date:
    """Calendar months, clamped to the last day of the short month (31 Jan + 1m = 28 Feb)."""
    total = start.month - 1 + months
    year = start.year + total // 12
    month = total % 12 + 1
    day = min(start.day, monthrange(year, month)[1])
    return date(year, month, day)


def build_schedule(*, premium: Decimal, start: date, plan: str) -> list[tuple[int, date, Decimal]]:
    count = INSTALLMENTS_BY_PLAN[plan]
    step = MONTHS_PER_PLAN[plan]
    each = _money(premium / count)
    rows: list[tuple[int, date, Decimal]] = []
    running = Decimal("0.00")
    for index in range(count):
        due = start if step == 0 else add_months(start, step * index)
        if index == count - 1:
            amount = _money(premium - running)  # the remainder lands here, so the sum is exact
        else:
            amount = each
            running += each
        rows.append((index + 1, due, amount))
    return rows


async def create_policy(
    session: AsyncSession,
    actor: User,
    scope: Scope,
    body: dict[str, Any],
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Issue a draft policy for a client in the caller's scope, with its schedule and commission row."""
    defaults = await policy_settings.get_defaults(session)
    client_id = body.get("client_id")
    client = await row_visible(session, select(Client).where(Client.id == client_id), scope, "client")
    if client is None:
        raise NotFound("Client not found")  # never 403: a foreign client is not a permission problem

    for field, model in (("insurer_id", Insurer), ("insurance_type_id", InsuranceType)):
        value = body.get(field)
        if value is not None and (await session.get(model, value)) is None:
            raise NotFound(f"{field.replace('_id', '').replace('_', ' ').capitalize()} not found")

    raw_premium = body.get("premium_amount")
    if raw_premium in (None, ""):
        raise ValidationFailed(
            "Some fields need attention",
            details=[{"field": "premium_amount", "message": "قيمة القسط السنوي مطلوبة لتوليد جدول الأقساط", "type": "missing"}],
        )
    premium = _money(raw_premium)
    if premium <= 0:
        raise ValidationFailed(
            "Some fields need attention", details=[{"field": "premium_amount", "message": "رقم موجب", "type": "invalid"}]
        )

    start = _as_date(body.get("start_date"))
    if start is None:
        raise ValidationFailed(
            "Some fields need attention", details=[{"field": "start_date", "message": "تاريخ بدء مطلوب", "type": "missing"}]
        )
    end = _as_date(body.get("end_date")) or _one_year_later(start)
    if end <= start:
        raise ValidationFailed(
            "Some fields need attention",
            details=[{"field": "end_date", "message": "التاريخ اللاحق يجب أن يكون بعد تاريخ البدء", "type": "invalid"}],
        )

    plan = str(body.get("payment_plan") or defaults["default_payment_plan"])
    if plan not in INSTALLMENTS_BY_PLAN:
        raise ValidationFailed(
            "Some fields need attention",
            details=[{"field": "payment_plan", "message": " / ".join(INSTALLMENTS_BY_PLAN), "type": "invalid"}],
        )
    currency = str(body.get("currency") or defaults["default_currency"]).upper()
    rate_raw = body.get("commission_rate")
    rate = _money(rate_raw if rate_raw not in (None, "") else defaults["default_commission_pct"])
    if not Decimal("0") <= rate <= Decimal("100"):
        raise ValidationFailed(
            "Some fields need attention", details=[{"field": "commission_rate", "message": "بين 0 و100", "type": "invalid"}]
        )

    policy = Policy(
        client_id=client.id,
        insurer_id=body.get("insurer_id"),
        insurance_type_id=body.get("insurance_type_id"),
        policy_no=(str(body["policy_no"]).strip() or None) if body.get("policy_no") else None,
        start_date=start,
        end_date=end,
        premium_amount=premium,
        currency=currency,
        payment_plan=PaymentPlan(plan),
        commission_rate=rate,
        status=PolicyStatus(str(body.get("status") or PolicyStatus.DRAFT.value)),
        headcount=body.get("headcount"),
        notes=(str(body["notes"]).strip() or None) if body.get("notes") else None,
    )
    session.add(policy)
    await session.flush()

    schedule = build_schedule(premium=premium, start=start, plan=plan)
    for sequence_no, due, amount in schedule:
        session.add(
            Installment(
                policy_id=policy.id,
                sequence_no=sequence_no,
                due_date=due,
                amount=amount,
                status=InstallmentStatus.UPCOMING,
            )
        )
    commission_amount = _money(premium * rate / Decimal("100"))
    if commission_amount > 0:
        session.add(Commission(policy_id=policy.id, amount=commission_amount, status=CommissionStatus.ACCRUED))
    await session.flush()

    await audit.write(
        session,
        actor=actor,
        action="policy.create",
        entity_type="policy",
        entity_id=policy.id,
        after={
            "client_id": str(client.id),
            "policy_no": policy.policy_no,
            "premium_amount": float(premium),
            "currency": currency,
            "payment_plan": plan,
            "installments": len(schedule),
            "commission_amount": float(commission_amount),
        },
        ip=ip,
        user_agent=user_agent,
    )
    await session.commit()
    return {
        "id": str(policy.id),
        "policy_no": policy.policy_no,
        "status": policy.status.value,
        "premium_amount": float(premium),
        "currency": currency,
        "payment_plan": plan,
        "installments": [
            {"sequence_no": seq, "due_date": due.isoformat(), "amount": float(amount), "status": InstallmentStatus.UPCOMING.value}
            for seq, due, amount in schedule
        ],
        "commission_amount": float(commission_amount),
    }


def _as_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, str):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ValidationFailed(
            "Some fields need attention",
            details=[{"field": "date", "message": "YYYY-MM-DD", "type": "date"}],
        ) from None


def _one_year_later(start: date) -> date:
    try:
        return start.replace(year=start.year + 1)
    except ValueError:  # 29 Feb → 28 Feb
        return start.replace(year=start.year + 1, day=28)
