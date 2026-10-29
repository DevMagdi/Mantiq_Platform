"""Channel intake: normalize → match caller → open/extend request → store media → notify.

Shared by the WhatsApp and telephony webhooks so both channels produce identical rows (spec §8).
Every entry point is idempotent on the provider's message/call id.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import NotFound
from app.domain.enums import (
    ActivityType,
    AttachmentOwner,
    CallDirection,
    Channel,
    DeliveryStatus,
    MessageDirection,
    NotificationType,
    RequestPriority,
    RequestStatus,
)
from app.domain.phone import normalize_mobile
from app.models.leads import Lead
from app.models.requests import Attachment, CallRecord, RequestMessage, ServiceRequest
from app.repositories import clients as clients_repo
from app.repositories import leads as leads_repo
from app.repositories import requests_repo as requests_repo
from app.services import audit, requests_svc


class MatchKind:
    INSURED = "insured_person"
    LEAD = "lead"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CallerMatch:
    """Insured person wins over lead, lead wins over unknown (matching order from spec section 8)."""

    kind: str
    insured_person_id: UUID | None = None
    display_name: str | None = None
    client_id: UUID | None = None
    lead_id: UUID | None = None


async def match_caller(session: AsyncSession, raw_mobile: str | None) -> CallerMatch:
    mobile = normalize_mobile(raw_mobile)
    if not mobile:
        return CallerMatch(kind=MatchKind.UNKNOWN)
    insured = await clients_repo.find_by_mobile(session, mobile)
    if insured is not None:
        return CallerMatch(
            kind=MatchKind.INSURED,
            insured_person_id=insured.id,
            display_name=insured.name,
            client_id=insured.client_id,
        )
    lead = (
        await session.execute(
            select(Lead).where(Lead.mobile == mobile, Lead.deleted_at.is_(None)).order_by(Lead.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if lead is not None:
        return CallerMatch(kind=MatchKind.LEAD, display_name=lead.name, lead_id=lead.id)
    return CallerMatch(kind=MatchKind.UNKNOWN)


async def ingest_whatsapp_message(
    session: AsyncSession,
    *,
    external_id: str,
    mobile: str | None,
    body: str | None,
    sender_name: str | None = None,
    media_kind: str | None = None,
    media_object_key: str | None = None,
    media_mime: str | None = None,
    media_size: int = 0,
    media_filename: str | None = None,
    duration_s: int | None = None,
    occurred_at: datetime | None = None,
    raw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    now = occurred_at or datetime.now(UTC)

    if await requests_repo.external_id_seen(session, external_id):
        return {"status": "duplicate", "external_id": external_id}

    match = await match_caller(session, mobile)
    kind, insured_id, name, client_id, lead_id = (
        match.kind,
        match.insured_person_id,
        match.display_name,
        match.client_id,
        match.lead_id,
    )
    normalized = normalize_mobile(mobile)
    created_request_id: UUID | None = None
    request: ServiceRequest | None = None

    if kind == MatchKind.INSURED and insured_id is not None:
        request = await requests_repo.open_for_thread(session, insured_id, settings.REPLY_THREAD_WINDOW_MINUTES)
        if request is None:
            request = await _open_request(
                session,
                client_id=client_id,
                insured_person_id=insured_id,
                subject=(body or "استفسار عبر واتساب").strip().splitlines()[0][:120] if body else "استفسار عبر واتساب",
                channel=Channel.WHATSAPP,
                contact_name=name,
                contact_mobile=normalized,
                external_thread_id=external_id.split(":")[0],
                now=now,
                raw=raw,
            )
            created_request_id = request.id

    if request is not None:
        message = RequestMessage(
            request_id=request.id,
            direction=MessageDirection.IN,
            channel=Channel.WHATSAPP,
            body=body,
            media_kind=media_kind,
            duration_s=duration_s,
            sender_ref=sender_name or name,
            external_id=external_id,
            delivery_status=DeliveryStatus.DELIVERED,
        )
        session.add(message)
        if media_object_key:
            from app.models.requests import Attachment

            await session.flush()  # the attachment belongs to this message (and, via request_id, the request)
            session.add(
                Attachment(
                    owner_type=AttachmentOwner.REQUEST,
                    owner_id=message.id,
                    request_id=request.id,
                    filename=(media_filename or media_object_key.rsplit("/", 1)[-1])[:255],
                    object_key=media_object_key,
                    mime=media_mime or "application/octet-stream",
                    size=media_size,
                )
            )
        request.last_activity_at = now
        if request.status in {RequestStatus.REPLIED, RequestStatus.RESOLVED}:
            request.status = RequestStatus.IN_PROGRESS
    elif kind == MatchKind.LEAD and lead_id is not None:
        await leads_repo.add_activity(
            session,
            lead_id=lead_id,
            type_=ActivityType.WHATSAPP,
            body=body or "(media)",
            meta={"external_id": external_id, "media": media_kind},
            actor_id=None,
            occurred_at=now,
        )
    else:
        # Unmatched senders land in the unsorted inbox as a client-less request (spec §8).
        request = await _open_request(
            session,
            client_id=None,
            insured_person_id=None,
            subject="رسالة غير مصنفة",
            channel=Channel.WHATSAPP,
            contact_name=sender_name,
            contact_mobile=normalized,
            external_thread_id=external_id.split(":")[0],
            now=now,
            raw=raw,
            body=body,
            media_kind=media_kind,
            media_object_key=media_object_key,
            media_mime=media_mime,
            media_size=media_size,
            media_filename=media_filename,
            duration_s=duration_s,
            external_id=external_id,
            priority=RequestPriority.NORMAL,
        )
        created_request_id = request.id

    await audit.write(
        session,
        actor=None,
        action="webhook.whatsapp",
        entity_type="request" if created_request_id else "request_message",
        entity_id=created_request_id or (request.id if request else None),
        after={"external_id": external_id, "matched": kind, "priority": "inbound"},
    )
    await session.flush()
    if request is not None:
        await _notify_team(session, request, NotificationType.NEW_REQUEST, f"طلب #{request.number} عبر واتساب")
    return {
        "status": "created" if created_request_id else "appended",
        "request_id": str(request.id) if request else None,
        "request_number": request.number if request else None,
        "matched_kind": kind,
        "matched_name": name,
    }


async def ingest_email_message(
    session: AsyncSession,
    *,
    external_id: str,
    from_email: str,
    from_name: str | None = None,
    subject: str | None = None,
    body: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    occurred_at: datetime | None = None,
) -> dict[str, Any]:
    """Inbound email (R12 §D, ADR-032): a signed provider webhook lands on the same Request model.

    Threading is by sender address (`external_thread_id`): a second mail from the same address appends
    to the still-open email request instead of opening a duplicate ticket. Attachments arrive already
    staged in our storage by the webhook (same model as WhatsApp documents).
    """
    now = occurred_at or datetime.now(UTC)
    if await requests_repo.external_id_seen(session, external_id):
        return {"status": "duplicate", "external_id": external_id}
    email = from_email.strip().lower()

    # match a known client contact by email so the request lands on the client file, not the void
    from app.models.clients import ClientContact

    contact = (await session.execute(select(ClientContact).where(sa.func.lower(ClientContact.email) == email))).scalars().first()

    request = (
        (
            await session.execute(
                select(ServiceRequest)
                .where(
                    ServiceRequest.channel == Channel.EMAIL,
                    ServiceRequest.external_thread_id == email,
                    ServiceRequest.status.notin_([RequestStatus.RESOLVED]),
                )
                .order_by(ServiceRequest.last_activity_at.desc())
            )
        )
        .scalars()
        .first()
    )

    created = False
    if request is None:
        request = await _open_request(
            session,
            client_id=contact.client_id if contact else None,
            insured_person_id=None,
            subject=(subject or "رسالة بريد إلكتروني")[:255],
            channel=Channel.EMAIL,
            contact_name=from_name or (contact.name if contact else None),
            contact_mobile=None,
            external_thread_id=email,
            now=now,
            raw=None,
        )
        created = True

    message = RequestMessage(
        request_id=request.id,
        direction=MessageDirection.IN,
        channel=Channel.EMAIL,
        body=body,
        sender_ref=from_name or email,
        external_id=external_id,
        delivery_status=DeliveryStatus.DELIVERED,
    )
    session.add(message)
    await session.flush()
    for att in attachments or []:
        session.add(
            Attachment(
                owner_type=AttachmentOwner.REQUEST,
                owner_id=message.id,
                request_id=request.id,
                filename=str(att.get("filename") or "attachment")[:255],
                object_key=str(att["object_key"]),
                mime=str(att.get("mime") or "application/octet-stream"),
                size=int(att.get("size") or 0),
            )
        )
    request.last_activity_at = now
    if request.status in {RequestStatus.REPLIED, RequestStatus.RESOLVED}:
        request.status = RequestStatus.IN_PROGRESS
    await audit.write(
        session,
        actor=None,
        action="webhook.email",
        entity_type="request",
        entity_id=request.id,
        after={"external_id": external_id, "matched_client": str(contact.client_id) if contact else None},
    )
    await session.flush()
    await _notify_team(session, request, NotificationType.NEW_REQUEST, f"طلب #{request.number} عبر البريد")
    return {"status": "created" if created else "appended", "request_id": str(request.id), "request_number": request.number}


async def ingest_call_event(
    session: AsyncSession,
    *,
    provider_call_id: str,
    event: str,
    direction: str = "inbound",
    from_number: str | None = None,
    to_number: str | None = None,
    caller_name: str | None = None,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    duration_s: int | None = None,
    recording_object_key: str | None = None,
    recording_consent: bool = False,
    recording_size: int | None = None,
    provider_name: str = "mobile-calls",
) -> dict[str, Any]:
    """A call to the platform's mobile number (R9 §1.C).

    `call.started` opens/links the request by the caller's mobile number, `call.ended` closes the timing,
    `recording_ready` attaches the recording. `provider_name` is the configured mobile-calls provider
    (`twilio` | `generic`), stored on the row — the old hardcoded `"sim"` made every production call claim
    to be simulated.
    """
    existing = (await session.execute(select(CallRecord).where(CallRecord.provider_call_id == provider_call_id))).scalar_one_or_none()

    match = await match_caller(session, from_number if direction == "inbound" else to_number)
    kind, insured_id, name, client_id = match.kind, match.insured_person_id, match.display_name, match.client_id

    if event == "call.started":
        if existing is not None:
            return {"status": "duplicate", "call_id": str(existing.id)}
        request: ServiceRequest | None = None
        if insured_id is not None:
            request = await requests_repo.open_for_thread(session, insured_id, get_settings().REPLY_THREAD_WINDOW_MINUTES)
            if request is None:
                request = await _open_request(
                    session,
                    client_id=client_id,
                    insured_person_id=insured_id,
                    subject="مكالمة موبايل واردة",
                    channel=Channel.CALL,
                    contact_name=name,
                    contact_mobile=normalize_mobile(from_number),
                    now=started_at or datetime.now(UTC),
                    raw={"provider_call_id": provider_call_id},
                )
        elif direction == "inbound":
            # A call from an unknown or not-yet-client number still has to land in the staff queue,
            # otherwise nobody can call back (spec §8.4). It carries no client, so only `all`-scope
            # staff (managers / admins) see it until it is linked (see `request_predicate`).
            request = await _open_request(
                session,
                client_id=None,
                insured_person_id=None,
                subject="مكالمة موبايل واردة — رقم غير معروف",
                channel=Channel.CALL,
                contact_name=caller_name or name,
                contact_mobile=normalize_mobile(from_number),
                now=started_at or datetime.now(UTC),
                raw={"provider_call_id": provider_call_id, "matched_kind": kind},
            )
        call = CallRecord(
            request_id=request.id if request else None,
            provider_call_id=provider_call_id,
            provider=provider_name,
            direction=CallDirection(direction),
            from_number=normalize_mobile(from_number),
            to_number=normalize_mobile(to_number),
            caller_name=caller_name or name,
            started_at=started_at or datetime.now(UTC),
            duration_s=duration_s or 0,
            recording_consent=recording_consent,
        )
        session.add(call)
        await session.flush()
        if request is not None:
            session.add(
                RequestMessage(
                    request_id=request.id,
                    direction=MessageDirection.IN,
                    channel=Channel.CALL,
                    body="مكالمة واردة",
                    sender_ref=caller_name or name,
                    external_id=f"call:{provider_call_id}",
                    delivery_status=DeliveryStatus.SENT,
                )
            )
            await session.flush()
        await audit.write(
            session,
            actor=None,
            action="mobile_calls.call_started",
            entity_type="call_record",
            entity_id=call.id,
            after={"matched": kind, "from": _mask(call.from_number)},
        )
        return {"status": "started", "call_id": str(call.id), "request_id": str(request.id) if request else None, "matched_kind": kind}

    if existing is None:
        raise NotFound("Unknown call id")

    if event == "call.ended":
        existing.ended_at = ended_at or datetime.now(UTC)
        existing.duration_s = duration_s if duration_s is not None else int((existing.ended_at - existing.started_at).total_seconds())
        await session.flush()
        return {"status": "ended", "call_id": str(existing.id), "duration_s": existing.duration_s}

    if event == "recording_ready":
        existing.recording_object_key = recording_object_key
        existing.recording_size = recording_size
        existing.recording_consent = bool(recording_consent)
        await session.flush()
        if existing.request_id is not None:
            await session.execute(select(ServiceRequest).where(ServiceRequest.id == existing.request_id))
            await audit.write(
                session,
                actor=None,
                action="mobile_calls.recording_ready",
                entity_type="call_record",
                entity_id=existing.id,
                after={"object_key": recording_object_key},
            )
        return {"status": "recording_attached", "call_id": str(existing.id)}

    return {"status": "ignored", "event": event}


def _mask(value: str | None) -> str:
    from app.domain.phone import mask_mobile

    return mask_mobile(value)


async def _open_request(
    session: AsyncSession,
    *,
    client_id: UUID | None,
    insured_person_id: UUID | None,
    subject: str,
    channel: Channel,
    contact_name: str | None,
    contact_mobile: str | None,
    now: datetime,
    raw: dict[str, Any] | None,
    body: str | None = None,
    media_kind: str | None = None,
    media_object_key: str | None = None,
    media_mime: str | None = None,
    media_size: int = 0,
    media_filename: str | None = None,
    duration_s: int | None = None,
    external_id: str | None = None,
    priority: RequestPriority = RequestPriority.NORMAL,
    external_thread_id: str | None = None,
) -> ServiceRequest:
    number = await requests_svc.next_number(session)
    insurer_id = await requests_svc._insurer_of(session, client_id) if client_id else None
    request = ServiceRequest(
        number=number,
        client_id=client_id,
        insured_person_id=insured_person_id,
        insurer_id=insurer_id,
        channel=channel,
        subject=subject,
        description=body,
        contact_name=contact_name,
        contact_mobile=contact_mobile,
        status=RequestStatus.NEW,
        priority=priority,
        created_at=now,
        updated_at=now,
        last_activity_at=now,
        external_thread_id=external_thread_id,
    )
    request.sla_due_at = await requests_svc.sla_deadline(session, priority, now)
    session.add(request)
    await session.flush()
    if body or media_kind:
        message = RequestMessage(
            request_id=request.id,
            direction=MessageDirection.IN,
            channel=channel,
            body=body,
            media_kind=media_kind,
            duration_s=duration_s,
            external_id=external_id,
            delivery_status=DeliveryStatus.DELIVERED,
        )
        session.add(message)
        await session.flush()
        if media_object_key:
            from app.models.requests import Attachment

            session.add(
                Attachment(
                    owner_type=AttachmentOwner.REQUEST,
                    owner_id=message.id,
                    request_id=request.id,
                    filename=(media_filename or media_object_key.rsplit("/", 1)[-1])[:255],
                    object_key=media_object_key,
                    mime=media_mime or "application/octet-stream",
                    size=media_size,
                )
            )
        await session.flush()
    _ = raw
    return request


async def _notify_team(session: AsyncSession, request: ServiceRequest, type_: NotificationType, title: str) -> None:
    from app.models.auth import User
    from app.models.requests import Notification

    recipients = (
        (
            await session.execute(
                select(User.id).where(
                    User.is_active.is_(True),
                    User.deleted_at.is_(None),
                    User.id != request.assigned_to,
                )
            )
        )
        .scalars()
        .all()
    )
    if request.assigned_to is not None:
        recipients = [*recipients, request.assigned_to]
    for user_id in recipients[:12]:
        session.add(Notification(user_id=user_id, type=type_, title=title, entity_type="request", entity_id=request.id))
    await session.flush()
