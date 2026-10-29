"""Report aggregations. One SQL per report, consumed by the endpoint and by the exporter.

The exporter must not own a second copy of the query: spec §6 lists exports among the surfaces that
have to respect scoping, and duplicated SQL is exactly where those rules drift apart.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.scope import Scope
from app.models.masters import Insurer
from app.models.requests import ServiceRequest
from app.repositories.scope_filters import with_scope


def _tone(sla_pct: float) -> str:
    """The sheet's bands: ≥90 green, 80–89 amber, below 80 red (spec §9.14)."""
    if sla_pct >= 90:
        return "g"
    if sla_pct >= 80:
        return "a"
    return "r"


async def insurer_performance(session: AsyncSession, scope: Scope) -> list[dict[str, Any]]:
    """First-response performance per insurer, inside the caller's scope."""
    stmt = (
        select(
            Insurer.name,
            func.count(ServiceRequest.id),
            func.avg(func.extract("epoch", ServiceRequest.first_response_at) - func.extract("epoch", ServiceRequest.created_at)),
            func.sum(sa.case((ServiceRequest.is_overdue.is_(True), 1), else_=0)),
        )
        .join(ServiceRequest, ServiceRequest.insurer_id == Insurer.id)
        .group_by(Insurer.name)
    )
    rows = (await session.execute(with_scope(stmt, scope, "request"))).all()

    out: list[dict[str, Any]] = []
    for name, count, avg_first, overdue in rows:
        total = int(count or 0)
        overdue_count = int(overdue or 0)
        sla_pct = round(((total - overdue_count) / total) * 100.0, 1) if total else 0.0
        out.append(
            {
                "insurer": name,
                "requests": total,
                "avg_first_response_minutes": round(float(avg_first) / 60.0, 1) if avg_first else None,
                "sla_pct": sla_pct,
                "tone": _tone(sla_pct),
            }
        )
    out.sort(key=lambda item: (-item["requests"], str(item["insurer"])))
    return out
