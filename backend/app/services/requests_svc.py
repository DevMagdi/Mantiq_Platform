"""Service-desk use-cases: manual creation, SLA stamping, replies, status, assignment, notes,
recording URLs. Inbound channel intake lives in services/intake.py."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import Forbidden, NotFound
from app.core.scope import Scope
from app.core.security import make_signed_media_url
from app.domain.enums import (
    Channel,
    DeliveryStatus,
    MessageDirection,
    NotificationType,
    RequestPriority,
    RequestStatus,
)
from app.domain.sla import BusinessHours, add_business_minutes
from app.models.auth import User
from app.models.clients import Client, InsuredPerson
from app.models.masters import BusinessHoursRow, SlaPolicy
from app.models.requests import (
    Attachment,
    CallRecord,
    InternalNote,
    Notification,
    RequestMessage,
    RequestNumberCounter,
    ServiceRequest,
)
from app.repositories import requests_repo as repo
from app.services import audit

if TYPE_CHECKING:
    from app.schemas.requests import RequestCreate, RequestUpdate

_TERMINAL = {RequestStatus.RESOLVED, RequestStatus.CLOSED}


async def next_number(session: AsyncSession) -> int:
    """Gap-free-ish human id (`#2841`) via a locked counter row — portable across PG/SQLite."""
    if session.get_bind().dialect.name == "postgresql":
        await session.execute(sa.text("SELECT pg_advisory_xact_lock(hashtext('requests'))"))
    row = (
        await session.execute(select(RequestNumberCounter).where(RequestNumberCounter.key == "requests").with_for_update())
    ).scalar_one_or_none()
    if row is None:
        row = RequestNumberCounter(key="requests", last_value=1000)
        session.add(row)
        await session.flush()
        return row.last_value
    row.last_value += 1
    await session.flush()
    return row.last_value


async def business_hours(session: AsyncSession) -> BusinessHours:
    rows = (await session.execute(select(BusinessHoursRow))).scalars().all()
    if not rows:
        return BusinessHours()
    return BusinessHours.from_rows([(r.weekday, r.open_time, r.close_time, r.is_open) for r in rows])


async def sla_deadline(session: AsyncSession, priority: RequestPriority, created_at: datetime) -> datetime | None:
    policy = (await session.execute(select(SlaPolicy).where(SlaPolicy.priority == priority))).scalar_one_or_none()
    if policy is None:
        return None
    return add_business_minutes(created_at, policy.first_response_minutes, await business_hours(session))


async def create(session: AsyncSession, actor: User, scope: Scope, payload: RequestCreate, *, ip: str | None = None) -> ServiceRequest:
    now = datetime.now(UTC)
    number = await next_number(session)
    priority = RequestPriority(payload.priority)
    request = ServiceRequest(
        number=number,
        client_id=payload.client_id,
        insured_person_id=payload.insured_person_id,
        policy_id=payload.policy_id,
        insurer_id=payload.insurer_id,
        insurance_type_id=payload.insurance_type_id,
        channel=Channel(payload.channel),
        subject=payload.subject.strip(),
        description=payload.description,
        contact_name=payload.contact_name,
        contact_mobile=payload.contact_mobile,
        status=RequestStatus.NEW,
        priority=priority,
        assigned_to=payload.assigned_to or actor.id,
        created_at=now,
        updated_at=now,
        last_activity_at=now,
    )
    request.sla_due_at = await sla_deadline(session, priority, now)
    if payload.insured_person_id is not None:
        insured = await session.get(InsuredPerson, payload.insured_person_id)
        if insured is not None:
            request.client_id = request.client_id or insured.client_id
            request.contact_mobile = request.contact_mobile or insured.mobile
            request.contact_name = request.contact_name or insured.name
    elif payload.client_id is not None:
        client = await session.get(Client, payload.client_id)
        if client is not None and client.account_owner_id is not None and payload.assigned_to is None:
            request.assigned_to = client.account_owner_id
    if request.insurer_id is None and request.client_id is not None:
        request.insurer_id = await _insurer_of(session, request.client_id)

    session.add(request)
    await session.flush()

    body = payload.first_message or payload.description
    if body:
        session.add(
            RequestMessage(
                request_id=request.id,
                direction=MessageDirection.IN,
                channel=request.channel,
                body=body,
                delivery_status=DeliveryStatus.SENT,
            )
        )
        await session.flush()

    await audit.write(
        session,
        actor=actor,
        action="request.created",
        entity_type="request",
        entity_id=request.id,
        after={
            "number": request.number,
            "client_id": str(request.client_id) if request.client_id else None,
            "channel": request.channel.value,
            "priority": request.priority.value,
        },
        ip=ip,
    )
    if request.assigned_to is not None and request.assigned_to != actor.id:
        _enqueue_notification(session, request.assigned_to, NotificationType.REQUEST_ASSIGNED, f"طلب #{request.number}", request.id)
    # `serialize_request` reads the joined relationships; a flushed-but-never-queried row has them
    # unloaded, and touching them from the response would raise MissingGreenlet. Load them once here.
    await session.refresh(request, attribute_names=["client", "insured_person", "insurer", "insurance_type", "assignee"])
    return request


async def get(session: AsyncSession, request_id: UUID, scope: Scope) -> ServiceRequest:
    row = await repo.get_by_id(session, request_id, scope)
    if row is None:
        raise NotFound("Request not found")
    return row


async def update(
    session: AsyncSession, actor: User, scope: Scope, request_id: UUID, payload: RequestUpdate, *, ip: str | None = None
) -> ServiceRequest:
    request = await get(session, request_id, scope)
    before = {"status": request.status.value, "priority": request.priority.value, "subject": request.subject}
    data = payload.model_dump(exclude_unset=True)
    if "priority" in data and data["priority"] is not None:
        request.priority = RequestPriority(data.pop("priority"))
        request.sla_due_at = await sla_deadline(session, request.priority, request.created_at)
    for field, value in list(data.items()):
        setattr(request, field, value)
    request.last_activity_at = datetime.now(UTC)
    await session.flush()
    await audit.write(
        session,
        actor=actor,
        action="request.updated",
        entity_type="request",
        entity_id=request.id,
        before=before,
        after={"status": request.status.value, "priority": request.priority.value, "subject": request.subject},
        ip=ip,
    )
    return request


async def change_status(
    session: AsyncSession,
    actor: User,
    scope: Scope,
    request_id: UUID,
    *,
    status: RequestStatus,
    note: str | None = None,
    ip: str | None = None,
) -> ServiceRequest:
    request = await get(session, request_id, scope)
    if request.status is status:
        return request
    before = request.status.value
    request.status = status
    now = datetime.now(UTC)
    request.last_activity_at = now
    if status in {RequestStatus.REPLIED} and request.first_response_at is None:
        request.first_response_at = now
    if status in _TERMINAL:
        request.resolved_at = now
        request.is_overdue = False
    elif status in {RequestStatus.IN_PROGRESS, RequestStatus.WAITING_INSURER} and request.resolved_at:
        request.resolved_at = None
    await session.flush()
    await audit.write(
        session,
        actor=actor,
        action="request.status_changed",
        entity_type="request",
        entity_id=request.id,
        before={"status": before},
        after={"status": status.value, "note": note},
        ip=ip,
    )
    return request


async def assign(
    session: AsyncSession, actor: User, scope: Scope, request_id: UUID, *, to_user_id: UUID | None, ip: str | None = None
) -> ServiceRequest:
    request = await get(session, request_id, scope)
    previous = request.assigned_to
    request.assigned_to = to_user_id
    request.last_activity_at = datetime.now(UTC)
    await session.flush()
    if to_user_id is not None and to_user_id != actor.id:
        _enqueue_notification(session, to_user_id, NotificationType.REQUEST_ASSIGNED, f"طلب #{request.number}", request.id)
    await audit.write(
        session,
        actor=actor,
        action="request.assigned",
        entity_type="request",
        entity_id=request.id,
        before={"assigned_to": str(previous) if previous else None},
        after={"assigned_to": str(to_user_id) if to_user_id else None},
        ip=ip,
    )
    return request


async def add_outbound_message(
    session: AsyncSession,
    actor: User,
    scope: Scope,
    request_id: UUID,
    *,
    body: str,
    channel: Channel | None = None,
    attachment_ids: list[UUID] | None = None,
    ip: str | None = None,
) -> RequestMessage:
    """Staff reply. Sends through the channel provider and stamps the first-response time."""
    request = await get(session, request_id, scope)
    now = datetime.now(UTC)
    used_channel = channel or request.channel
    message = RequestMessage(
        request_id=request.id,
        direction=MessageDirection.OUT,
        channel=used_channel,
        body=body,
        author_id=actor.id,
        delivery_status=DeliveryStatus.QUEUED if used_channel is Channel.WHATSAPP else DeliveryStatus.SENT,
    )
    session.add(message)
    await repo.mark_first_response(request, now)
    if request.status in {RequestStatus.NEW, RequestStatus.IN_PROGRESS, RequestStatus.WAITING_INSURER}:
        request.status = RequestStatus.REPLIED
    if attachment_ids:
        await session.flush()  # the message needs its id before attachments can point at it
        await session.execute(
            sa.update(Attachment).where(Attachment.id.in_(attachment_ids)).values(request_id=request.id, owner_id=message.id)
        )
    await session.flush()

    if used_channel is Channel.WHATSAPP:
        from app.integrations.registry import whatsapp_provider

        provider = whatsapp_provider()
        target = request.contact_mobile
        if provider is not None and target:
            result = await provider.send_text(to=target, body=body)
            message.external_id = result.get("external_id") or result.get("message_id")
            message.delivery_status = DeliveryStatus.SENT if result.get("ok") else DeliveryStatus.FAILED
            # Optional document on the same reply: upload-then-send each attached file; any failure
            # marks the row FAILED so the operator sees it on the message, not in a log (R12 §B.6).
            if message.delivery_status is DeliveryStatus.SENT and attachment_ids and hasattr(provider, "send_document"):
                from app.integrations.storage import storage

                rows = (await session.execute(sa.select(Attachment).where(Attachment.id.in_(attachment_ids)))).scalars().all()
                for att in rows:
                    try:
                        doc = await provider.send_document(
                            to=target, content=storage().get_object(att.object_key), filename=att.filename, mime=att.mime
                        )
                    except Exception as exc:  # provider/network failure must surface on the row, not 500 the reply
                        doc = {"ok": False, "detail": f"{type(exc).__name__}: {str(exc)[:160]}"}
                    if not doc.get("ok"):
                        message.delivery_status = DeliveryStatus.FAILED
    elif used_channel is Channel.EMAIL:
        # Outbound email reply (R12 §D.3): SMTP when connected; otherwise the row is FAILED, visibly.
        from app.integrations.registry import email_notifier

        target_email = request.external_thread_id if request.external_thread_id and "@" in request.external_thread_id else None
        notifier = email_notifier()
        sent_ok = False
        if target_email is not None and hasattr(notifier, "send_email"):
            try:
                sent_ok = bool(await notifier.send_email(to=target_email, subject=f"Re: {request.subject}", body=body))
            except Exception:
                sent_ok = False
        message.delivery_status = DeliveryStatus.SENT if sent_ok else DeliveryStatus.FAILED
    await audit.write(
        session,
        actor=actor,
        action="request.replied",
        entity_type="request",
        entity_id=request.id,
        after={"channel": used_channel.value, "chars": len(body)},
        ip=ip,
    )
    return message


async def add_note(session: AsyncSession, actor: User, scope: Scope, request_id: UUID, body: str, *, ip: str | None = None) -> InternalNote:
    request = await get(session, request_id, scope)
    note = InternalNote(request_id=request.id, author_id=actor.id, body=body)
    session.add(note)
    await session.flush()
    await audit.write(
        session,
        actor=actor,
        action="request.note_added",
        entity_type="request",
        entity_id=request.id,
        after={"chars": len(body)},
        ip=ip,
    )
    return note


async def list_calls(session: AsyncSession, actor: User, scope: Scope, request_id: UUID) -> list[CallRecord]:
    request = await get(session, request_id, scope)
    return await repo.calls(session, request.id)


async def recording_url(session: AsyncSession, actor: User, scope: Scope, call_id: UUID, *, ip: str | None = None) -> dict[str, Any]:
    """Every listen is permission-checked (calls.listen), URL is short-lived, and each play is audited."""
    if not scope.can("calls.listen"):
        raise Forbidden("Not allowed to listen to call recordings")
    record = (await session.execute(select(CallRecord).where(CallRecord.id == call_id))).scalar_one_or_none()
    if record is None:
        raise NotFound("Call not found")
    if record.request_id is not None:
        await get(session, record.request_id, scope)  # 404 if the parent request is out of scope
    elif record.lead_id is not None:
        from app.repositories import leads as leads_repo

        if await leads_repo.get_by_id(session, record.lead_id, scope) is None:
            raise NotFound("Call not found")
    if not record.recording_object_key:
        raise NotFound("Recording is not available yet")
    settings = get_settings()
    url, expires_at = make_signed_media_url(record.recording_object_key, settings.RECORDING_SIGNED_URL_TTL_SECONDS)
    record.listen_count += 1
    await audit.write(
        session,
        actor=actor,
        action="call.recording_listened",
        entity_type="call_record",
        entity_id=record.id,
        after={"ttl_seconds": settings.RECORDING_SIGNED_URL_TTL_SECONDS, "consent": record.recording_consent},
        ip=ip,
    )
    await session.flush()
    return {
        "url": url,
        "expires_at": datetime.fromtimestamp(expires_at, UTC).isoformat(),
        "duration_s": record.duration_s,
        "recording_consent": record.recording_consent,
    }


async def sweep_overdue(session: AsyncSession, *, now: datetime | None = None) -> dict[str, int]:
    """Celery beat entry point: recompute `is_overdue`, notify on late + approaching."""
    now = now or datetime.now(UTC)
    settings = get_settings()
    overdue_ids = (
        await session.execute(
            select(ServiceRequest.id, ServiceRequest.number, ServiceRequest.assigned_to, ServiceRequest.sla_due_at).where(
                ServiceRequest.status.notin_(_TERMINAL),
                ServiceRequest.sla_due_at.is_not(None),
            )
        )
    ).all()
    flipped = 0
    for request_id, number, assigned_to, due in overdue_ids:
        is_over = now > due
        current = (await session.execute(select(ServiceRequest.is_overdue).where(ServiceRequest.id == request_id))).scalar_one()
        if current != is_over:
            await session.execute(sa.update(ServiceRequest).where(ServiceRequest.id == request_id).values(is_overdue=is_over))
            flipped += 1
        if is_over and assigned_to is not None:
            hours_late = int((now - due).total_seconds() // 3600)
            already = (
                await session.execute(
                    sa.select(
                        sa.exists().where(
                            Notification.entity_id == request_id,
                            Notification.type == NotificationType.REQUEST_OVERDUE,
                        )
                    )
                )
            ).scalar_one()
            if not already and hours_late >= 1:
                _enqueue_notification(
                    session,
                    assigned_to,
                    NotificationType.REQUEST_OVERDUE,
                    f"متأخر {hours_late} س · طلب #{number}",
                    request_id,
                )
    _ = settings
    await session.flush()
    return {"checked": len(overdue_ids), "flipped": flipped}


async def _insurer_of(session: AsyncSession, client_id: UUID) -> UUID | None:
    from app.models.phase2 import Policy

    return (
        await session.execute(
            select(Policy.insurer_id)
            .where(Policy.client_id == client_id, Policy.insurer_id.is_not(None))
            .order_by(Policy.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _enqueue_notification(session: AsyncSession, user_id: UUID, type_: NotificationType, title: str, entity_id: UUID | None) -> None:
    session.add(
        Notification(
            user_id=user_id,
            type=type_,
            title=title,
            entity_type="request" if "request" in type_.value else "lead",
            entity_id=entity_id,
        )
    )
