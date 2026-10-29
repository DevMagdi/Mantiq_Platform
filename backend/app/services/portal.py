"""Client-company owner portal (screen 6). Sees one client, never internal notes/commissions/reps/recordings."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Forbidden, NotFound
from app.core.scope import Scope
from app.domain import phone
from app.models.clients import Client, InsuredPerson
from app.models.phase2 import Installment, Policy
from app.models.requests import Attachment, RequestMessage, ServiceRequest


async def _client(session: AsyncSession, scope: Scope) -> Client:
    if not scope.is_portal or scope.portal_client_id is None:
        raise Forbidden("Portal access is only available to client accounts")
    client = await session.get(Client, scope.portal_client_id)
    if client is None or client.deleted_at is not None:
        raise NotFound("Client not found")
    return client


async def overview(session: AsyncSession, scope: Scope) -> dict[str, Any]:
    client = await _client(session, scope)
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    requests_month = int(
        (
            await session.execute(
                select(func.count(ServiceRequest.id)).where(ServiceRequest.client_id == client.id, ServiceRequest.created_at >= month_start)
            )
        ).scalar_one()
    )
    avg = (
        await session.execute(
            select(
                func.avg(func.extract("epoch", ServiceRequest.first_response_at) - func.extract("epoch", ServiceRequest.created_at))
            ).where(ServiceRequest.client_id == client.id, ServiceRequest.first_response_at.is_not(None))
        )
    ).scalar_one_or_none()
    policy = (
        await session.execute(select(Policy).where(Policy.client_id == client.id).order_by(Policy.created_at.desc()).limit(1))
    ).scalar_one_or_none()
    premiums = await _premiums(session, policy)
    covered = int(
        (
            await session.execute(
                select(func.count(InsuredPerson.id)).where(
                    InsuredPerson.client_id == client.id,
                    InsuredPerson.deleted_at.is_(None),
                    InsuredPerson.coverage_status == "active",
                )
            )
        ).scalar_one()
    )
    return {
        "client": {
            "id": str(client.id),
            "name": client.name,
            "type": client.type.value,
            "since_date": client.since_date.isoformat() if client.since_date else None,
        },
        "policy_summary": None
        if policy is None
        else {
            "policy_no": policy.policy_no,
            "status": policy.status.value,
            "start_date": policy.start_date.isoformat() if policy.start_date else None,
            "end_date": policy.end_date.isoformat() if policy.end_date else None,
            "payment_plan": policy.payment_plan.value,
            "insurer": None if policy.insurer is None else policy.insurer.name,
            "insurance_type": None if policy.insurance_type is None else policy.insurance_type.name,
        },
        "kpis": {
            "requests_this_month": requests_month,
            "avg_response_minutes": round(float(avg) / 60.0, 1) if avg is not None else None,
            "premiums": premiums,
        },
        "covered_count": covered,
        "renewal_in_days": (policy.end_date - date.today()).days if policy and policy.end_date else None,
    }


async def _premiums(session: AsyncSession, policy: Policy | None) -> dict[str, Any]:
    if policy is None:
        return {"total": None, "paid": None, "next_due": None, "overdue": None}
    rows = (
        (await session.execute(select(Installment).where(Installment.policy_id == policy.id).order_by(Installment.due_date)))
        .scalars()
        .all()
    )
    today = date.today()
    total = sum(float(r.amount) for r in rows)
    paid = sum(float(r.amount) for r in rows if r.paid_at is not None)
    overdue = sum(float(r.amount) for r in rows if r.paid_at is None and r.due_date < today)
    next_due = next((r for r in rows if r.paid_at is None), None)
    # Commissions are the brokerage's internal margin: they must not appear in this payload at all,
    # not even as a flag (spec §6). Staff reads them from /commissions.
    return {
        "total": round(total, 2),
        "paid": round(paid, 2),
        "overdue": round(overdue, 2),
        "next_due": None if next_due is None else {"due_date": next_due.due_date.isoformat(), "amount": float(next_due.amount)},
    }


async def my_requests(session: AsyncSession, scope: Scope, *, page: int = 1, page_size: int = 20) -> dict[str, Any]:
    client = await _client(session, scope)
    conds = [ServiceRequest.client_id == client.id]
    total = int((await session.execute(select(func.count(ServiceRequest.id)).where(*conds))).scalar_one())
    rows = (
        (
            await session.execute(
                select(ServiceRequest)
                .where(*conds)
                .order_by(ServiceRequest.last_activity_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                **requests_svc_serialize(row),
                "messages": await thread(session, row.id),
            }
            for row in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def requests_svc_serialize(row: Any) -> dict[str, Any]:
    from app.services.dashboard import serialize_request

    data = serialize_request(row)
    # Portal-safe projection: no rep identity, no internal routing details.
    for key in ("assigned_to", "assignee_name", "mobile"):
        data.pop(key, None)
    data["mobile"] = phone.mask_mobile(row.contact_mobile)
    return data


async def thread(session: AsyncSession, request_id: Any) -> list[dict[str, Any]]:
    """Only the conversation body — internal notes are a separate table and are never queried here."""
    rows = (
        (
            await session.execute(
                select(RequestMessage).where(RequestMessage.request_id == request_id).order_by(RequestMessage.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(m.id),
            "direction": m.direction.value,
            "channel": m.channel.value,
            "body": m.body,
            "created_at": m.created_at.isoformat(),
            "delivery_status": m.delivery_status.value if m.delivery_status else None,
        }
        for m in rows
    ]


async def documents(session: AsyncSession, scope: Scope) -> list[dict[str, Any]]:
    """Everything the owner may download: their issued policy files **and** attachments on their requests.

    The policy files are the part that was missing: an owner could read a contract summary but could not
    hold the document (ADR-021). They are merged into this one list because the screen has one documents
    column — and because a client looking for "my policy" does not think in tables.
    """
    client = await _client(session, scope)
    rows = (
        await session.execute(
            select(Attachment, ServiceRequest.number)
            .join(ServiceRequest, ServiceRequest.id == Attachment.request_id)
            .where(ServiceRequest.client_id == client.id)
            .order_by(Attachment.created_at.desc())
            .limit(100)
        )
    ).all()
    attachments = [
        {
            "id": str(att.id),
            "kind": "request_attachment",
            "filename": att.filename,
            "mime": att.mime,
            "size": att.size,
            "created_at": att.created_at.isoformat(),
            "request_number": number,
            "policy_no": None,
            "download_url": f"/api/v1/portal/attachments/{att.id}",
        }
        for att, number in rows
    ]

    from app.models.phase2 import PolicyDocument

    policy_rows = (
        await session.execute(
            select(PolicyDocument, Policy.policy_no, Policy.start_date, Policy.end_date)
            .join(Policy, Policy.id == PolicyDocument.policy_id)
            .where(Policy.client_id == client.id, Policy.status == "issued")
            .order_by(PolicyDocument.created_at.desc())
            .limit(100)
        )
    ).all()
    policies = [
        {
            "id": str(document.id),
            "kind": "policy",
            "filename": document.filename,
            "mime": document.mime,
            "size": document.size,
            "created_at": document.created_at.isoformat(),
            "request_number": None,
            "policy_no": number,
            "download_url": f"/api/v1/portal/policies/{document.policy_id}/document",
        }
        for document, number, _start, _end in policy_rows
    ]
    return policies + attachments


async def my_policies(session: AsyncSession, scope: Scope) -> list[dict[str, Any]]:
    """The owner's contracts with their real status — the facts a client asks about on the phone.

    No commission rate, no `notes` (internal), no assignee: the policy row carries all three, and this
    projection is the one place that decides what leaves the building (spec §6).
    """
    client = await _client(session, scope)
    rows = (
        (await session.execute(select(Policy).where(Policy.client_id == client.id).order_by(Policy.start_date.desc().nullslast())))
        .unique()
        .scalars()
        .all()
    )
    from app.models.phase2 import PolicyDocument

    latest: dict[str, PolicyDocument] = {}
    if rows:
        documents = (
            (
                await session.execute(
                    select(PolicyDocument)
                    .where(PolicyDocument.policy_id.in_([row.id for row in rows]))
                    .order_by(PolicyDocument.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        for document in documents:
            latest.setdefault(str(document.policy_id), document)

    out: list[dict[str, Any]] = []
    for row in rows:
        attached: PolicyDocument | None = latest.get(str(row.id))
        out.append(
            {
                "id": str(row.id),
                "policy_no": row.policy_no,
                "status": row.status.value,
                "insurance_type": None if row.insurance_type is None else row.insurance_type.name,
                "insurer": None if row.insurer is None else row.insurer.name,
                "start_date": row.start_date.isoformat() if row.start_date else None,
                "end_date": row.end_date.isoformat() if row.end_date else None,
                "premium_amount": float(row.premium_amount) if row.premium_amount is not None else None,
                "currency": row.currency,
                "headcount": row.headcount,
                "document": None
                if attached is None
                else {
                    "filename": attached.filename,
                    "size": attached.size,
                    "created_at": attached.created_at.isoformat(),
                    "download_url": f"/api/v1/portal/policies/{row.id}/document",
                },
            }
        )
    return out


async def policy_document(session: AsyncSession, scope: Scope, policy_id: Any) -> tuple[str, str, bytes]:
    """Bytes of one issued policy file — only if the policy belongs to the caller's own company."""
    client = await _client(session, scope)
    policy = (await session.execute(select(Policy).where(Policy.id == policy_id, Policy.client_id == client.id))).scalar_one_or_none()
    if policy is None:
        raise NotFound("Document not found")  # a foreign policy is "not here", never "forbidden" (spec §6)
    from app.models.phase2 import PolicyDocument

    document = (
        await session.execute(
            select(PolicyDocument).where(PolicyDocument.policy_id == policy.id).order_by(PolicyDocument.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if document is None:
        raise NotFound("Document not found")
    from app.integrations.storage import storage

    try:
        payload = storage().get_object(document.object_key)
    except (FileNotFoundError, ValueError) as exc:
        raise NotFound("Document not found") from exc
    return document.filename, document.mime or "application/pdf", payload


async def create_request(session: AsyncSession, scope: Scope, *, subject: str, body: str | None) -> dict[str, Any]:
    """Owner may open a request for their own company (channel=manual)."""
    if not scope.is_portal:
        raise Forbidden("Portal only")
    client = await _client(session, scope)
    from app.domain.enums import Channel
    from app.models.auth import User
    from app.schemas.requests import RequestCreate
    from app.services import requests_svc as svc

    actor = await session.get(User, scope.user_id) if scope.user_id else None
    if actor is None:
        raise Forbidden("Portal user is inactive")
    payload = RequestCreate(
        client_id=client.id,
        channel=Channel.MANUAL.value,
        subject=subject,
        description=body,
        priority="normal",
    )
    request = await svc.create(session, actor, scope, payload)
    return {"request_id": str(request.id), "number": request.number}


_ = timedelta
