"""Client, contact, insured-person and policy queries (always scoped)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.pagination import PageParams
from app.core.scope import Scope
from app.domain.enums import ClientStatus, CoverageStatus
from app.models.auth import User
from app.models.clients import Client, InsuredPerson
from app.models.leads import Lead
from app.models.phase2 import Policy
from app.models.requests import ServiceRequest
from app.repositories.scope_filters import with_scope

_SORTABLE = {"name": Client.name, "created_at": Client.created_at, "status": Client.status}


def _base() -> Select[Any]:
    return select(Client).options(
        selectinload(Client.account_owner).selectinload(User.role),
        selectinload(Client.contacts),
    )


async def get_by_id(session: AsyncSession, client_id: UUID, scope: Scope) -> Client | None:
    return (await session.execute(with_scope(_base().where(Client.id == client_id), scope, "client"))).scalar_one_or_none()


async def list_page(
    session: AsyncSession,
    scope: Scope,
    *,
    q: str | None = None,
    status: ClientStatus | None = None,
    only_converted: bool | None = None,
    params: PageParams,
) -> tuple[list[Client], int]:
    conds: list[Any] = []
    if q:
        like = f"%{q.strip().lower()}%"
        conds.append(or_(func.lower(Client.name).like(like), func.lower(func.coalesce(Client.tax_number, "")).like(like)))
    if status:
        conds.append(Client.status == status)
    if only_converted is True:
        conds.append(Client.source_lead_id.is_not(None))
    if only_converted is False:
        conds.append(Client.source_lead_id.is_(None))

    stmt = with_scope(_base(), scope, "client")
    count_stmt = with_scope(select(func.count(Client.id)), scope, "client")
    if conds:
        stmt = stmt.where(and_(*conds))
        count_stmt = count_stmt.where(and_(*conds))
    col = _SORTABLE.get(params.sort or "created_at", Client.created_at)
    stmt = stmt.order_by(col.desc() if params.order == "desc" else col.asc()).offset(params.offset).limit(params.page_size)
    items = list((await session.execute(stmt)).scalars().unique())
    total = int((await session.execute(count_stmt)).scalar_one())
    return items, total


async def kpis(session: AsyncSession, scope: Scope) -> dict[str, Any]:
    """Screen 8: total clients, converted-from-lead count, policies expiring in 90 days."""
    total = int((await session.execute(with_scope(select(func.count(Client.id)), scope, "client"))).scalar_one())
    converted = int(
        (
            await session.execute(with_scope(select(func.count(Client.id)), scope, "client").where(Client.source_lead_id.is_not(None)))
        ).scalar_one()
    )
    horizon = date.today() + timedelta(days=90)
    expiring = int(
        (
            await session.execute(
                with_scope(select(func.count(Policy.id)).join(Client, Client.id == Policy.client_id), scope, "client").where(
                    Policy.end_date.is_not(None), Policy.end_date <= horizon, Policy.end_date >= date.today()
                )
            )
        ).scalar_one()
    )
    return {"total_clients": total, "converted_from_leads": converted, "expiring_90d": expiring}


async def counts_for(session: AsyncSession, client_ids: list[UUID]) -> dict[UUID, dict[str, int]]:
    """Batched counters — avoids the N+1 the spec explicitly forbids in list rows."""
    if not client_ids:
        return {}
    insured_rows = (
        await session.execute(
            select(InsuredPerson.client_id, func.count(InsuredPerson.id))
            .where(InsuredPerson.client_id.in_(client_ids), InsuredPerson.deleted_at.is_(None))
            .group_by(InsuredPerson.client_id)
        )
    ).all()
    open_rows = (
        await session.execute(
            select(ServiceRequest.client_id, func.count(ServiceRequest.id))
            .where(
                ServiceRequest.client_id.in_(client_ids),
                ServiceRequest.status.notin_(["resolved", "closed"]),
            )
            .group_by(ServiceRequest.client_id)
        )
    ).all()
    out: dict[UUID, dict[str, int]] = {cid: {"insured": 0, "open_requests": 0} for cid in client_ids}
    for cid, count in insured_rows:
        out[cid]["insured"] = int(count)
    for cid, count in open_rows:
        out[cid]["open_requests"] = int(count)
    return out


async def list_insured(
    session: AsyncSession,
    client_id: UUID,
    scope: Scope,
    *,
    q: str | None = None,
    coverage_status: CoverageStatus | None = None,
    params: PageParams,
) -> tuple[list[InsuredPerson], int]:
    conds: list[Any] = [InsuredPerson.client_id == client_id, InsuredPerson.deleted_at.is_(None)]
    if q:
        like = f"%{q.strip().lower()}%"
        conds.append(
            or_(
                func.lower(InsuredPerson.name).like(like),
                func.lower(func.coalesce(InsuredPerson.mobile, "")).like(like),
                func.lower(func.coalesce(InsuredPerson.department, "")).like(like),
            )
        )
    if coverage_status:
        conds.append(InsuredPerson.coverage_status == coverage_status)
    stmt = select(InsuredPerson).where(and_(*conds))
    count_stmt = select(func.count(InsuredPerson.id)).where(and_(*conds))
    if scope.mode != "all":
        gate = with_scope(select(Client.id).where(Client.id == client_id), scope, "client")
        stmt, count_stmt = stmt.where(gate.exists()), count_stmt.where(gate.exists())
    stmt = stmt.order_by(InsuredPerson.name.asc()).offset(params.offset).limit(params.page_size)
    return list((await session.execute(stmt)).scalars()), int((await session.execute(count_stmt)).scalar_one())


async def find_by_mobile(session: AsyncSession, mobile: str) -> InsuredPerson | None:
    """Channel intake matching order (spec §8): insured person first."""
    row = (
        await session.execute(
            select(InsuredPerson)
            .where(InsuredPerson.mobile == mobile, InsuredPerson.deleted_at.is_(None))
            # prefer an actively-covered person when a number was reused across clients
            .order_by(
                sa.case((InsuredPerson.coverage_status == CoverageStatus.ACTIVE, 0), else_=1),
                InsuredPerson.updated_at.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    client = await session.get(Client, row.client_id)
    if client is None or client.deleted_at is not None:
        return None
    return row


async def lead_candidates(session: AsyncSession, mobile: str, scope: Scope) -> list[Lead]:
    stmt = with_scope(select(Lead).where(Lead.mobile == mobile, Lead.deleted_at.is_(None)), scope, "lead").order_by(Lead.created_at.desc())
    return list((await session.execute(stmt.limit(5))).scalars())


async def first_response_avg_minutes(session: AsyncSession, scope: Scope, *, days: int = 30) -> float | None:
    """Dashboard KPI. Calendar minutes between request creation and first outbound reply."""
    since = datetime.now(UTC) - timedelta(days=days)
    stmt = with_scope(
        select(func.avg(func.extract("epoch", ServiceRequest.first_response_at) - func.extract("epoch", ServiceRequest.created_at))).where(
            ServiceRequest.first_response_at.is_not(None),
            ServiceRequest.created_at >= since,
        ),
        scope,
        "request",
    )
    value = (await session.execute(stmt)).scalar_one_or_none()
    return round(float(value) / 60.0, 1) if value is not None else None
