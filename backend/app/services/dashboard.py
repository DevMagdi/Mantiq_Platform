"""Dashboard aggregates (screen 1) — everything reads through the same scope predicate."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.scope import Scope
from app.models.clients import Client
from app.repositories import requests_repo as repo
from app.services import clients as clients_svc


async def summary(session: AsyncSession, scope: Scope) -> dict[str, Any]:
    kpis = await repo.kpis(session, scope)
    if scope.mode in {"all", "owner", "seeded"}:
        kpis["clients_total"] = int((await session.execute(_client_count_stmt(scope))).scalar_one())
    return kpis


def _client_count_stmt(scope: Scope) -> Any:
    from app.repositories.scope_filters import with_scope

    return with_scope(select(func.count(Client.id)), scope, "client")


async def channels(session: AsyncSession, scope: Scope, *, days: int = 7) -> list[dict[str, Any]]:
    return await repo.channels_breakdown(session, scope, days=days)


async def status_breakdown(session: AsyncSession, scope: Scope) -> list[dict[str, Any]]:
    return await repo.status_breakdown(session, scope)


async def recent_requests(session: AsyncSession, scope: Scope, *, limit: int = 8) -> list[dict[str, Any]]:
    rows = await repo.recent(session, scope, limit=limit)
    return [serialize_request(r) for r in rows]


async def today_board(session: AsyncSession, scope: Scope, *, limit: int = 10) -> dict[str, Any]:
    """«اليوم» (A5): what needs a human today, from data the system already owns — overdue SLA
    requests, lead follow-ups due, and installments falling due within 14 days. Every list reads
    through the caller's scope; counts are SQL aggregates, lists are capped."""
    from datetime import UTC, date, datetime, timedelta

    from app.domain.enums import InstallmentStatus, RequestStatus
    from app.models.leads import Lead
    from app.models.phase2 import Installment, Policy
    from app.models.requests import ServiceRequest
    from app.repositories.scope_filters import with_scope

    now = datetime.now(UTC)
    open_statuses = [RequestStatus.NEW, RequestStatus.IN_PROGRESS, RequestStatus.WAITING_INSURER]

    overdue_stmt = with_scope(select(ServiceRequest), scope, "request").where(
        ServiceRequest.status.in_(open_statuses),
        (ServiceRequest.is_overdue.is_(True)) | (ServiceRequest.sla_due_at < now),
    )
    overdue_count = int(
        (
            await session.execute(
                with_scope(select(func.count()).select_from(ServiceRequest), scope, "request").where(
                    ServiceRequest.status.in_(open_statuses),
                    (ServiceRequest.is_overdue.is_(True)) | (ServiceRequest.sla_due_at < now),
                )
            )
        ).scalar_one()
    )
    overdue_rows = (
        (await session.execute(overdue_stmt.order_by(ServiceRequest.sla_due_at.asc().nulls_first()).limit(limit))).scalars().all()
    )

    follow_stmt = with_scope(select(Lead), scope, "lead").where(Lead.next_followup_at.is_not(None), Lead.next_followup_at <= now)
    follow_count = int(
        (
            await session.execute(
                with_scope(select(func.count()).select_from(Lead), scope, "lead").where(
                    Lead.next_followup_at.is_not(None), Lead.next_followup_at <= now
                )
            )
        ).scalar_one()
    )
    follow_rows = (await session.execute(follow_stmt.order_by(Lead.next_followup_at.asc()).limit(limit))).scalars().all()

    horizon = date.today() + timedelta(days=14)
    due_stmt = (
        with_scope(
            select(Installment, Policy.policy_no, Policy.client_id).join(Policy, Installment.policy_id == Policy.id), scope, "policy"
        )
        .where(Installment.status != InstallmentStatus.PAID, Installment.paid_at.is_(None), Installment.due_date <= horizon)
        .order_by(Installment.due_date.asc())
        .limit(limit)
    )
    due_rows = (await session.execute(due_stmt)).all()
    due_count = int(
        (
            await session.execute(
                with_scope(
                    select(func.count()).select_from(Installment).join(Policy, Installment.policy_id == Policy.id), scope, "policy"
                ).where(Installment.status != InstallmentStatus.PAID, Installment.paid_at.is_(None), Installment.due_date <= horizon)
            )
        ).scalar_one()
    )
    return {
        "generated_at": now,
        "overdue_requests": {"count": overdue_count, "items": [serialize_request(r) for r in overdue_rows]},
        "lead_followups": {
            "count": follow_count,
            "items": [
                {
                    "id": lead.id,
                    "name": lead.name,
                    "mobile": lead.mobile,
                    "status": lead.status.value,
                    "next_followup_at": lead.next_followup_at,
                }
                for lead in follow_rows
            ],
        },
        "installments_due": {
            "count": due_count,
            "items": [
                {
                    "id": inst.id,
                    "policy_no": policy_no,
                    "client_id": client_id,
                    "sequence_no": inst.sequence_no,
                    "due_date": inst.due_date,
                    "amount": float(inst.amount),
                    "status": inst.status.value,
                }
                for inst, policy_no, client_id in due_rows
            ],
        },
    }


def serialize_request(row: Any) -> dict[str, Any]:
    """Shared by dashboard + inbox: the row shape screen 1/2 tables expect."""
    from datetime import UTC, datetime

    person = row.insured_person
    client = row.client
    remaining = None
    hours_late = None
    if row.sla_due_at is not None:
        delta = row.sla_due_at - datetime.now(UTC)
        minutes = int(delta.total_seconds() // 60)
        if minutes >= 0:
            remaining = minutes
        else:
            hours_late = -minutes // 60
    return {
        "id": str(row.id),
        "number": row.number,
        "subject": row.subject,
        "status": row.status.value,
        "priority": row.priority.value,
        "channel": row.channel.value,
        "is_overdue": row.is_overdue,
        "sla_due_at": row.sla_due_at.isoformat() if row.sla_due_at else None,
        "sla_minutes_remaining": remaining,
        "sla_hours_late": hours_late,
        "employee_name": (person.name if person else None) or row.contact_name or "موظف غير معروف",
        "mobile": row.contact_mobile or (person.mobile if person else None),
        "client_id": str(client.id) if client else None,
        "client_name": client.name if client else "غير مصنّف",
        "insurance_type": row.insurance_type.name if row.insurance_type else None,
        "insurer": row.insurer.name if row.insurer else None,
        "assigned_to": str(row.assigned_to) if row.assigned_to else None,
        "assignee_name": row.assignee.full_name if row.assignee else None,
        "created_at": row.created_at.isoformat(),
        "last_activity_at": row.last_activity_at.isoformat(),
        "first_response_at": row.first_response_at.isoformat() if row.first_response_at else None,
    }


_ = clients_svc
