"""Underwriting defaults for Phase 2 (spec §8, ADR-016 §6).

These are the numbers a manager decides once for the whole workspace — default commission rate, default
payment plan, currency — as opposed to feature flags, which
are booleans, and integration settings, which are credentials. They live in `system_settings` as one JSON
document under a key, so adding a fifth default is a code change and not a migration.

Nothing here is a secret, so nothing here is encrypted or masked: `GET /settings/policies` returns the
values verbatim and that is deliberate.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationFailed
from app.domain.enums import PaymentPlan
from app.models.auth import User
from app.models.settings import SystemSetting
from app.services import audit

KEY = "underwriting.defaults"

#: the whole document, and every key here is read by `POST /policies` as a seed. The renewal horizon on
#: screen 12 stays `expiring_90d` (its name says 90, so making it editable would need a schema change, not
#: a config field) — ADR-016 §8: no setting without a consumer.
DEFAULTS: dict[str, Any] = {
    "default_commission_pct": 5.0,
    "default_payment_plan": PaymentPlan.MONTHLY.value,
    "default_currency": "EGP",
}

_PLANS = tuple(plan.value for plan in PaymentPlan)


async def get_defaults(session: AsyncSession) -> dict[str, Any]:
    row = await _row(session)
    stored = dict(row.value) if row and row.value else {}
    merged = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}
    return _validate(merged)


async def set_defaults(
    session: AsyncSession,
    actor: User,
    values: dict[str, Any],
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    unknown = sorted(set(values) - set(DEFAULTS))
    if unknown:
        raise ValidationFailed(
            "Some fields need attention",
            details=[{"field": name, "message": "مفتاح غير معروف", "type": "extra"} for name in unknown],
        )
    row = await _row(session)
    before = dict(row.value) if row and row.value else dict(DEFAULTS)
    merged = _validate({**before, **values})
    if row is None:
        row = SystemSetting(key=KEY, value=merged, updated_by=actor.id)
        session.add(row)
    else:
        row.value = merged
        row.updated_by = actor.id
    await audit.write(
        session,
        actor=actor,
        action="settings.underwriting_update",
        entity_type="system_setting",
        entity_id=row.id,
        before=_plain(before),
        after=_plain(merged),
        ip=ip,
        user_agent=user_agent,
    )
    await session.commit()
    return merged


async def _row(session: AsyncSession) -> SystemSetting | None:
    return (await session.execute(select(SystemSetting).where(SystemSetting.key == KEY))).scalar_one_or_none()


def _plain(values: dict[str, Any]) -> dict[str, Any]:
    return {k: (float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v) for k, v in values.items()}


def _validate(values: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        pct = float(values.get("default_commission_pct", DEFAULTS["default_commission_pct"]))
    except (TypeError, ValueError):
        raise _bad("default_commission_pct", "نسبة مئوية رقمية") from None
    if not 0 <= pct <= 100:
        raise _bad("default_commission_pct", "بين 0 و100")
    out["default_commission_pct"] = round(pct, 2)

    plan = str(values.get("default_payment_plan") or DEFAULTS["default_payment_plan"])
    if plan not in _PLANS:
        raise _bad("default_payment_plan", " / ".join(_PLANS))
    out["default_payment_plan"] = plan

    currency = str(values.get("default_currency") or "").strip().upper()
    if not (3 <= len(currency) <= 8 and currency.isalpha()):
        raise _bad("default_currency", "رمز عملة من 3 إلى 8 أحرف")
    out["default_currency"] = currency

    return out


def _bad(field_name: str, message: str) -> ValidationFailed:
    return ValidationFailed("Some fields need attention", details=[{"field": field_name, "message": message, "type": "invalid"}])
