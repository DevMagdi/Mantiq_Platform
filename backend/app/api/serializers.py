"""Model → wire mapping. The portal-safe projection lives here so a serializer can't leak by accident."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import inspect as sa_inspect

from app.core.config import get_settings
from app.core.scope import Scope
from app.core.security import make_signed_media_url
from app.domain import phone
from app.models.auth import User
from app.models.clients import Client, ClientContact, ImportJob, InsuredPerson
from app.models.leads import Lead, LeadActivity
from app.models.masters import InsuranceType, Insurer, LostReason
from app.models.phase2 import Policy
from app.models.requests import (
    CallRecord,
    InternalNote,
    Notification,
    RequestMessage,
    ServiceRequest,
)


def lead_brief(lead: Lead) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "id": lead.id,
        "name": lead.name,
        "type": lead.type.value,
        "status": lead.status.value,
        "mobile": lead.mobile,
        "contact_name": lead.contact_name,
        "contact_title": lead.contact_title,
        "headcount": lead.headcount,
        "next_followup_at": lead.next_followup_at,
        "followup_overdue": bool(lead.next_followup_at and lead.next_followup_at < now and lead.status not in {"won", "lost"}),
        "insurance_type": lead.insurance_type.name if lead.insurance_type else None,
        "insurer": lead.current_insurer.name if lead.current_insurer else None,
        "assignee_name": lead.assignee.full_name if lead.assignee else None,
        "assignee_id": lead.assigned_to,
        "last_activity_at": lead.updated_at,
        "converted_client_id": lead.converted_client_id,
    }


def lead_detail(lead: Lead) -> dict[str, Any]:
    return {
        **lead_brief(lead),
        "email": lead.email,
        "source": lead.source,
        "budget": lead.budget,
        "needs_text": lead.needs_text,
        "current_policy_expiry": lead.current_policy_expiry,
        "created_at": lead.created_at,
        "converted_at": lead.converted_at,
        "lost_note": lead.lost_note,
        "lost_reason": lead.lost_reason.name if lead.lost_reason else None,
    }


def lead_activity(row: LeadActivity) -> dict[str, Any]:
    return {
        "id": row.id,
        "type": row.type.value,
        "body": row.body,
        "meta": row.meta,
        "occurred_at": row.occurred_at,
        "actor_name": row.actor.full_name if row.actor else None,
    }


def client_brief(client: Client, *, counts: dict[str, int] | None = None) -> dict[str, Any]:
    counts = counts or {}
    policy = client.__dict__.get("_policy")
    return {
        "id": client.id,
        "name": client.name,
        "type": client.type.value,
        "status": client.status.value,
        "since_date": client.since_date,
        "converted_at": client.converted_at,
        "is_converted_from_lead": client.source_lead_id is not None,
        "source_lead_id": client.source_lead_id,
        "account_owner_id": client.account_owner_id,
        "account_owner_name": client.account_owner.full_name if client.account_owner else None,
        "insurance_type": policy.insurance_type.name if policy and policy.insurance_type else None,
        "insurer": policy.insurer.name if policy and policy.insurer else None,
        "insured_count": counts.get("insured", 0),
        "open_requests": counts.get("open_requests", 0),
        "end_date": policy.end_date if policy else None,
    }


def contact_brief(row: ClientContact) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "title": row.title,
        "mobile": row.mobile,
        "email": row.email,
        "is_primary": row.is_primary,
    }


def insured_brief(row: InsuredPerson) -> dict[str, Any]:
    """`client_name` is only read when the relationship is already loaded.

    The create/patch endpoints hand us a freshly built instance; touching `row.client` there would
    lazy-load outside the greenlet and 500 the request.
    """
    today = datetime.now(UTC).date()
    unloaded = sa_inspect(row).unloaded
    status = row.coverage_status.value
    if row.coverage_end and row.coverage_end < today:
        status = "ended"
    elif row.coverage_start and row.coverage_start > today:
        status = "pending"
    return {
        "id": row.id,
        "client_id": row.client_id,
        "client_name": (row.client.name if row.client else None) if "client" not in unloaded else None,
        "name": row.name,
        "mobile": row.mobile,
        "department": row.department,
        "job_title": row.job_title,
        "national_id": row.national_id,
        "coverage_status": status,
        "coverage_start": row.coverage_start,
        "coverage_end": row.coverage_end,
        "notes": row.notes,
        "external_ref": row.external_ref,
    }


def import_job_brief(row: ImportJob) -> dict[str, Any]:
    return {
        "id": row.id,
        "client_id": row.client_id,
        "type": row.type.value,
        "status": row.status.value,
        "filename": row.filename,
        "total_rows": row.total_rows,
        "stats": row.stats,
        "error_report": row.error_report[:200],
        "preview": row.rows_payload[:50],
        "created_at": row.created_at,
    }


def request_brief(row: ServiceRequest, *, first_message: str | None = None) -> dict[str, Any]:
    from app.services.dashboard import serialize_request

    data = serialize_request(row)
    data["first_message"] = first_message
    return data


def request_detail(
    row: ServiceRequest,
    *,
    messages: list[RequestMessage],
    notes: list[InternalNote],
    calls: list[CallRecord],
    scope: Scope,
    attachments: list[Any] | None = None,
) -> dict[str, Any]:
    from app.services.dashboard import serialize_request

    data = serialize_request(row)
    data["description"] = row.description
    data["messages"] = [
        {
            "id": m.id,
            "direction": m.direction.value,
            "channel": m.channel.value,
            "body": m.body,
            "media_kind": m.media_kind,
            "duration_s": m.duration_s,
            "waveform": m.waveform,
            "sender_ref": m.sender_ref,
            # the provider's own id (wamid / SMS id): the handle a support ticket quotes when a reply
            # "was sent" but the client says it never arrived
            "external_id": m.external_id,
            "delivery_status": m.delivery_status.value,
            "created_at": m.created_at,
            "attachments": [],
        }
        for m in messages
    ]
    data["calls"] = [call_brief(c, scope=scope) for c in calls]
    files = attachments or []
    by_owner: dict[str, list[dict[str, Any]]] = {}
    payload: list[dict[str, Any]] = []
    for attachment in files:
        entry = {
            "id": str(attachment.id),
            "filename": attachment.filename,
            "mime": attachment.mime,
            "size": attachment.size,
            "created_at": attachment.created_at,
            "owner_type": str(attachment.owner_type.value if hasattr(attachment.owner_type, "value") else attachment.owner_type),
            "owner_id": str(attachment.owner_id),
        }
        # Staff stream through a short-TTL signed URL; the portal goes through its own audited route.
        entry["url"] = (
            f"/api/v1/portal/attachments/{attachment.id}"
            if scope.is_portal
            else make_signed_media_url(attachment.object_key, get_settings().RECORDING_SIGNED_URL_TTL_SECONDS)[0]
        )
        payload.append(entry)
        by_owner.setdefault(entry["owner_id"], []).append(entry)
    data["attachments"] = payload
    for message in data["messages"]:
        message["attachments"] = list(by_owner.get(str(message["id"]), []))
    # Intake stores inbound media at request level (there is no `message` owner type in the enum), so a
    # voice bubble would otherwise never find its own clip: pair each media message with the next
    # unclaimed audio file, oldest first. Explicit message-owned attachments always win.
    message_ids = {str(message["id"]) for message in data["messages"]}
    claimed = {entry["id"] for owner, files in by_owner.items() if owner in message_ids for entry in files}
    audio_pool = [entry for entry in payload if entry["id"] not in claimed and str(entry["mime"]).startswith("audio/")]
    for message in data["messages"]:
        if message["media_kind"] and not message["attachments"] and audio_pool:
            message["attachments"].append(audio_pool.pop(0))
    if scope.is_portal:
        data["notes"] = []  # internal notes never reach the portal (spec §6)
        data["mobile"] = phone.mask_mobile(row.contact_mobile)
        for key in ("assignee_name",):
            data.pop(key, None)
    else:
        data["notes"] = [
            {"id": n.id, "body": n.body, "author_name": n.author.full_name if n.author else None, "created_at": n.created_at} for n in notes
        ]
    return data


def call_brief(row: CallRecord, *, scope: Scope) -> dict[str, Any]:
    return {
        "id": row.id,
        "provider_call_id": row.provider_call_id,
        "direction": row.direction.value,
        "from_number": row.from_number if not scope.is_portal else phone.mask_mobile(row.from_number),
        "to_number": row.to_number,
        "caller_name": row.caller_name,
        "started_at": row.started_at,
        "ended_at": row.ended_at,
        "duration_s": row.duration_s,
        "has_recording": bool(row.recording_object_key),
        "recording_consent": row.recording_consent,
        # The portal never gets recordings, even though it can see the call row (spec §6).
        "listen_allowed": bool(row.recording_object_key) and scope.can("calls.listen") and not scope.is_portal,
        "transcript": row.transcript,
        "summary": row.summary,
    }


def notification_brief(row: Notification) -> dict[str, Any]:
    return {
        "id": row.id,
        "type": row.type.value,
        "title": row.title,
        "body": row.body,
        "entity_type": row.entity_type,
        "entity_id": str(row.entity_id) if row.entity_id else None,
        "read_at": row.read_at,
        "created_at": row.created_at,
    }


def policy_brief(row: Policy) -> dict[str, Any]:
    return {
        "id": row.id,
        "client_id": row.client_id,
        "policy_no": row.policy_no,
        "status": row.status.value,
        "start_date": row.start_date,
        "end_date": row.end_date,
        "premium_amount": row.premium_amount,
        "currency": row.currency,
        "payment_plan": row.payment_plan.value,
        "commission_rate": row.commission_rate,
        "headcount": row.headcount,
        "client": None if row.client is None else {"id": str(row.client.id), "name": row.client.name},
        "insurer": None if row.insurer is None else {"id": str(row.insurer.id), "name": row.insurer.name},
        "insurance_type": None
        if row.insurance_type is None
        else {"id": str(row.insurance_type.id), "code": row.insurance_type.code, "name": row.insurance_type.name},
        # `None` while the policy is still a draft; the list then offers «إصدار» instead of «تحميل».
        # `documents` is `lazy="selectin"`, so this costs the query the page already needed.
        "document": None if not row.documents else _document_brief(row.documents[0], row),
    }


def _document_brief(document: Any, policy: Policy) -> dict[str, Any]:
    return {
        "filename": document.filename,
        "size": document.size,
        "created_at": document.created_at,
        "download_url": f"/api/v1/policies/{policy.id}/document",
    }


def user_brief(row: User, *, scope: Scope | None = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "email": row.email,
        "full_name": row.full_name,
        "mobile": row.mobile if scope is None or not scope.is_portal else None,
        "role": row.role.code,
        "role_name": row.role.name,
        "is_active": row.is_active,
        "mfa_enabled": row.mfa_enabled,
        "last_login_at": row.last_login_at,
    }


def master_brief(row: Insurer | InsuranceType | LostReason) -> dict[str, Any]:
    return {"id": row.id, "code": getattr(row, "code", None), "name": row.name}
