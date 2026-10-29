"""The two live inbound channels (R9): WhatsApp Cloud API and **mobile phone calls**.

Both are signature-checked and idempotent, and both are the only way into the platform for their channel:
there is no simulator route in this module any more — the dev-only ones moved to `dev_sim.py`, which a
production build never mounts. The mobile-calls route is exported under its product name
(`/webhooks/mobile-calls`) with the old `/webhooks/telephony` path kept as a documented alias, so a gateway
that was already pasted into a provider console keeps delivering during the rename.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Header, Query, Request
from fastapi.responses import PlainTextResponse

from app.api.v1.deps import DbSession
from app.core.config import get_settings
from app.core.errors import Forbidden, ValidationFailed
from app.integrations import registry
from app.integrations.base import StorageProvider, WhatsAppProvider
from app.integrations.mobile_calls import parse_event as parse_call_event
from app.integrations.registry import mobile_calls_provider, whatsapp_provider
from app.integrations.storage import storage
from app.services import intake, integrations_settings

router = APIRouter(tags=["webhooks"])


async def stage_inbound_media(
    provider: WhatsAppProvider, storage_backend: StorageProvider, external_id: str, media_id: str | None
) -> tuple[str | None, str | None, int]:
    """Copy inbound provider media into our own bucket.

    Playback must never depend on the provider keeping the file, and the URLs the UI gets back are
    short-TTL signed ones (spec §6.4). Returns ``(object_key, mime, size)``; all ``None``/0 when the
    event carries no media.
    """
    if not media_id:
        return None, None, 0
    payload, mime = await provider.fetch_media(str(media_id))
    safe_external = re.sub(r"[^A-Za-z0-9_.-]", "_", external_id)[:80]
    safe_media = re.sub(r"[^A-Za-z0-9_.-]", "_", str(media_id))[:60]
    key = f"media/{safe_external}/{safe_media}"
    storage_backend.put_object(key, payload, mime or "application/octet-stream")
    return key, mime, len(payload)


def _expected_verify_token() -> str:
    """The token the operator pasted into Meta, from the settings row when there is one (ADR-016 §1).

    It is a *secret*, so it is read from the decrypted secret map and not from `config`: the screen writes
    it as a secret, and a handshake that looked in `config` would answer 403 forever on a correctly
    configured installation. An unconfigured token refuses rather than accepting `""` — otherwise the
    verification route is open on every fresh deployment, and "Meta says it subscribed" becomes unfalsifiable.
    """
    return registry.resolved("whatsapp").secret("verify_token")


async def _log_rejected(session: Any, provider: str, raw: bytes, *, detail: str = "signature mismatch") -> None:
    """A rejected delivery is the one event the operator most needs to see — logged before the 403."""
    await integrations_settings.record_event(
        session,
        provider=provider,
        event_type="rejected",
        status="error",
        detail=f"{detail}; body {len(raw)} bytes",
    )
    await session.commit()


@router.get("/webhooks/whatsapp", summary="Meta verification handshake")
async def whatsapp_verify(
    session: DbSession,
    # Meta sends `hub.mode` / `hub.challenge` / `hub.verify_token`; without the aliases FastAPI looks for
    # `hub_mode`, so a real verification request from Meta would arrive and be refused as unconfigured.
    hub_mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    hub_challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
    hub_verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
) -> Any:
    expected = _expected_verify_token()
    ok = bool(expected) and hub_mode == "subscribe" and hub_verify_token == expected
    # The outcome is worth a row: "Meta says it subscribed but nothing arrives" is answered from settings.
    await integrations_settings.record_event(
        session,
        provider="whatsapp",
        event_type="verification",
        status="ok" if ok else "rejected",
        detail=None
        if ok
        else (
            "لا يوجد Verify Token محفوظ — أضِفه من /settings ثم أعِد المحاولة"
            if not expected
            else "hub.verify_token لم يطابق القيمة المحفوظة"
        ),
    )
    await session.commit()
    if not ok:
        # the two reasons are different work for the operator, so the answer names which one it is
        raise Forbidden("Verification failed: اضبط Verify Token في /settings أولًا" if not expected else "Verification failed")
    return PlainTextResponse(hub_challenge or "")


@router.post("/webhooks/whatsapp", summary="Inbound WhatsApp (signature-checked, idempotent)")
async def whatsapp_webhook(request: Request, session: DbSession, background: BackgroundTasks) -> dict[str, Any]:
    raw = await request.body()
    provider = whatsapp_provider()
    if not provider.verify_signature(raw, request.headers.get("x-hub-signature-256")):
        await _log_rejected(session, "whatsapp", raw)
        raise Forbidden("Invalid webhook signature")
    from app.integrations.whatsapp import parse_inbound

    events = parse_inbound(raw)
    storage_backend = storage()
    # `receive_media` is a real switch, not decoration: media is the expensive, privacy-bearing part of
    # the inbound stream (Meta fetch + our bucket write). Text still lands when it is off (ADR-016 §8).
    receive_media = bool(registry.config_value("whatsapp", "receive_media", True))

    results = []
    for event in events:
        if event.get("status"):
            results.append({"external_id": event["external_id"], "status": "delivery_update"})
            await integrations_settings.record_event(
                session, provider="whatsapp", event_type="status", external_id=str(event["external_id"]), detail=event["status"]
            )
            continue
        # Inbound media is copied into our own bucket, so playback never depends on the
        # provider keeping the file (and the portal/rep URLs stay short-lived & signed).
        media_id = event.get("media_id") if receive_media else None
        media_key, media_mime, media_size = await stage_inbound_media(provider, storage_backend, str(event["external_id"]), media_id)
        results.append(
            await intake.ingest_whatsapp_message(
                session,
                external_id=str(event["external_id"]),
                mobile=event.get("mobile"),
                body=event.get("body"),
                sender_name=event.get("sender_name"),
                media_kind=event.get("media_kind"),
                media_object_key=media_key,
                media_mime=media_mime,
                media_size=media_size,
                media_filename=event.get("filename"),
                duration_s=event.get("duration_s"),
            )
        )
        await integrations_settings.record_event(
            session,
            provider="whatsapp",
            event_type="message",
            external_id=str(event["external_id"]),
            status="ok",
            detail=f"media={'yes' if media_key else 'no'}" if event.get("media_id") else None,
        )
    await session.commit()
    _ = background
    return {"received": len(events), "results": results}


@router.post("/webhooks/mobile-calls", summary="Inbound mobile-call events (signature-checked, idempotent)")
@router.post("/webhooks/telephony", include_in_schema=False, deprecated=True)
async def mobile_calls_webhook(
    request: Request,
    session: DbSession,
    x_call_signature: str | None = Header(default=None),
) -> dict[str, Any]:
    """A call to the platform's mobile number: `call.started` opens/links the request, `call.ended` closes
    the timing, `recording_ready` attaches the recording we pulled and stored ourselves."""
    raw = await request.body()
    driver = mobile_calls_provider()
    if not driver.verify_signature(raw, x_call_signature):
        await _log_rejected(session, "telephony", raw)
        raise Forbidden("Invalid webhook signature")
    try:
        body = parse_call_event(raw)
    except ValidationFailed as exc:
        await _log_rejected(session, "telephony", raw, detail="body must be JSON")
        raise Forbidden(str(exc.message)) from exc

    # A real mobile-calls provider sends a `recording_url` that expires in minutes, not an object key. We
    # pull it while it is alive and keep our own copy, so playback never depends on the provider (ADR-023 §2).
    recording_key = body.get("recording_key")
    recording_url = body.get("recording_url")
    if recording_url and not recording_key:
        try:
            payload = await driver.fetch_recording(str(recording_url))
        except Exception as exc:  # a provider/storage failure must never 500 the webhook and lose the call
            await integrations_settings.record_event(
                session,
                provider="telephony",
                event_type="recording_fetch_failed",
                external_id=str(body.get("provider_call_id") or "") or None,
                status="error",
                detail=f"{type(exc).__name__}: {str(exc)[:200]}",
            )
            await session.commit()
            payload = None
        if payload:
            safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(body.get("provider_call_id") or "call"))[:80]
            recording_key = f"calls/{safe_id}.wav"
            storage().put_object(recording_key, payload, "audio/wav")
            body["recording_size"] = len(payload)

    result = await intake.ingest_call_event(
        session,
        provider_call_id=str(body.get("provider_call_id") or ""),
        event=str(body.get("event") or "call.started"),
        direction=str(body.get("direction") or "inbound"),
        from_number=body.get("from_number"),
        to_number=body.get("to_number"),
        caller_name=body.get("caller_name"),
        started_at=_ts(body.get("started_at")),
        ended_at=_ts(body.get("ended_at")),
        duration_s=body.get("duration_s"),
        recording_object_key=recording_key,
        recording_consent=bool(body.get("recording_consent", False)),
        recording_size=body.get("recording_size"),
        provider_name=getattr(driver, "provider_name", None) or "mobile-calls",
    )
    await integrations_settings.record_event(
        session,
        provider="telephony",
        event_type=str(body.get("event") or "call.started"),
        external_id=str(body.get("provider_call_id") or "") or None,
        status=str(result.get("status") or "ok"),
        detail=None if result.get("status") != "ignored" else "duplicate/unknown — no state change",
    )
    await session.commit()
    return result


_EMAIL_MIME = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "text/plain",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "audio/ogg",
    "audio/mpeg",
}
_EMAIL_MAX_BYTES = 12 * 1024 * 1024


@router.post("/webhooks/email", summary="Inbound email (HMAC-signed provider webhook, idempotent)")
async def email_webhook(request: Request, session: DbSession) -> dict[str, Any]:
    """Email intake (R12 §D, ADR-032): the provider-neutral signed JSON contract documented in the runbook.

    Signature: `x-email-signature: sha256=<hmac(secret, raw body)>` with the `inbound_secret` credential.
    With no secret configured the route refuses (403) outside dev/test — an unsigned mail firehose into
    the inbox is not a default anyone should inherit.
    """
    import base64
    import binascii
    import hashlib
    import hmac as hmac_mod
    import json as json_mod

    raw = await request.body()
    secret = registry.resolved("email").secret("inbound_secret")
    if secret:
        expected = "sha256=" + hmac_mod.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        provided = request.headers.get("x-email-signature") or ""
        if not hmac_mod.compare_digest(expected, provided):
            await _log_rejected(session, "email", raw)
            raise Forbidden("Invalid webhook signature")
    elif not get_settings().simulation_allowed:
        await _log_rejected(session, "email", raw, detail="inbound_secret not configured")
        raise Forbidden("Email inbound is not configured")
    try:
        body = json_mod.loads(raw or b"{}")
    except json_mod.JSONDecodeError:
        raise ValidationFailed("Body must be JSON") from None
    external_id = str(body.get("external_id") or body.get("message_id") or "")
    from_email = str(body.get("from") or "")
    if not external_id or "@" not in from_email:
        raise ValidationFailed("external_id and a valid from address are required")

    staged: list[dict[str, Any]] = []
    storage_backend = storage()
    for att in body.get("attachments") or []:
        mime = str(att.get("mime") or "application/octet-stream")
        if mime not in _EMAIL_MIME:
            raise ValidationFailed("Unsupported attachment type", details={"mime": mime})
        try:
            content = base64.b64decode(str(att.get("content_base64") or ""), validate=True)
        except (binascii.Error, ValueError):
            raise ValidationFailed("Attachment content_base64 is invalid") from None
        if len(content) > _EMAIL_MAX_BYTES:
            raise ValidationFailed("Attachment too large", details={"max_bytes": _EMAIL_MAX_BYTES})
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", str(att.get("filename") or "attachment"))[:120]
        safe_ext = re.sub(r"[^A-Za-z0-9_.-]", "_", external_id)[:60]
        key = f"email/{safe_ext}/{safe_name}"
        storage_backend.put_object(key, content, mime)
        staged.append({"filename": att.get("filename") or safe_name, "mime": mime, "size": len(content), "object_key": key})

    result = await intake.ingest_email_message(
        session,
        external_id=external_id,
        from_email=from_email,
        from_name=body.get("from_name"),
        subject=body.get("subject"),
        body=body.get("body_text") or body.get("body"),
        attachments=staged,
    )
    await integrations_settings.record_event(
        session,
        provider="email",
        event_type="message",
        external_id=external_id,
        status="ok",
        detail=f"attachments={len(staged)}",
    )
    await session.commit()
    return result


def _ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
