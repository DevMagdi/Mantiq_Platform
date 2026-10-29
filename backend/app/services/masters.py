"""Reference data + CSV loading of the real 47-insurer list (spec §5)."""

from __future__ import annotations

import csv
import io
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationFailed
from app.models.masters import InsuranceType, Insurer, LostReason, SlaPolicy


async def insurers(session: AsyncSession, *, active_only: bool = True) -> list[dict[str, Any]]:
    stmt = select(Insurer).order_by(Insurer.name)
    if active_only:
        stmt = stmt.where(Insurer.is_active.is_(True))
    return [
        {"id": str(r.id), "code": r.code, "name": r.name, "name_en": r.name_en, "is_active": r.is_active}
        for r in (await session.execute(stmt)).scalars()
    ]


async def insurance_types(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        (await session.execute(select(InsuranceType).where(InsuranceType.is_active.is_(True)).order_by(InsuranceType.code))).scalars().all()
    )
    return [{"id": str(r.id), "code": r.code, "name": r.name} for r in rows]


async def lost_reasons(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (await session.execute(select(LostReason).where(LostReason.is_active.is_(True)).order_by(LostReason.sort_order))).scalars().all()
    return [{"id": str(r.id), "name": r.name} for r in rows]


async def sla_policies(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (await session.execute(select(SlaPolicy).order_by(SlaPolicy.priority))).scalars().all()
    return [
        {
            "priority": r.priority.value,
            "first_response_minutes": r.first_response_minutes,
            "resolution_minutes": r.resolution_minutes,
            "warn_before_minutes": r.warn_before_minutes,
        }
        for r in rows
    ]


async def import_insurers_csv(session: AsyncSession, content: bytes) -> dict[str, int]:
    """`code,name[,name_en]` — upsert by code, never deletes."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationFailed("The CSV must be UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "code" not in [f.strip().lower() for f in reader.fieldnames]:
        raise ValidationFailed("CSV must have a 'code' column", details={"header": reader.fieldnames})
    normalized = {f.strip().lower(): f for f in (reader.fieldnames or [])}
    created = updated = 0
    skipped = 0
    for row in reader:
        code = str(row.get(normalized["code"], "")).strip()
        name = str(row.get(normalized.get("name", ""), "")).strip()
        name_en = str(row.get(normalized.get("name_en", ""), "")).strip() or None
        if not code or not name:
            skipped += 1
            continue
        existing = (await session.execute(select(Insurer).where(Insurer.code == code))).scalar_one_or_none()
        if existing is None:
            session.add(Insurer(code=code, name=name, name_en=name_en, is_active=True))
            created += 1
        else:
            existing.name = name
            if name_en:
                existing.name_en = name_en
            existing.is_active = True
            updated += 1
        await session.flush()
    await session.flush()
    return {"created": created, "updated": updated, "skipped": skipped}
