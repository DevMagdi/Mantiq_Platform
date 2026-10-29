"""Contract tests for the *live* channel drivers — the paths a simulator cannot prove (ADR-023).

The rule this file enforces: nothing here swaps a live driver for `WhatsAppSim`/`MobileCallsSim`. The live
code runs against an injected HTTP transport, so what is asserted is the URL, the bearer token, the JSON
shape, the HMAC over the raw bytes, the SSRF guard and the error mapping that ship in production. A test
that replaced the driver with a fake would pass on a driver nobody ever uses.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from app.core.errors import ValidationFailed
from app.integrations import mobile_calls as mobile_calls_mod
from app.integrations import whatsapp as whatsapp_mod
from app.integrations.mobile_calls import MobileCallsProvider, parse_event
from app.integrations.whatsapp import WhatsAppMeta
from sqlalchemy import select

from tests.helpers import login

MANAGER = "manager@brokerhub.test"
TOKEN = "EAAG-live-token-that-must-never-be-logged"
PHONE_ID = "109999888777"


def _signed(raw: bytes, secret: str = "s3cret-app") -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _inbound(external_id: str, mobile: str = "+201000000123", text: str = "عايز أعرف تغطية الأسنان") -> bytes:
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"wa_id": mobile, "profile": {"name": "عميل حقيقي"}}],
                                "messages": [{"id": external_id, "from": mobile, "type": "text", "text": {"body": text}}],
                            }
                        }
                    ]
                }
            ],
        }
    ).encode()


# ----------------------------------------------------------------------------------------------- WhatsApp


def test_meta_driver_posts_to_the_cloud_api_with_the_token_and_normalized_number() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.HBreal123"}]})

    whatsapp_mod.use_transport(httpx.MockTransport(handler))
    try:
        driver = WhatsAppMeta(token=TOKEN, phone_number_id=PHONE_ID, app_secret="s3cret-app")
        result = __import__("asyncio").run(driver.send_text(to="+201012345678", body="تم استلام طلبكم"))
    finally:
        whatsapp_mod.use_transport(None)

    assert result["ok"] is True
    assert result["message_id"] == "wamid.HBreal123"
    assert len(seen) == 1
    request = seen[0]
    assert str(request.url) == f"https://graph.facebook.com/v21.0/{PHONE_ID}/messages"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    payload = json.loads(request.content)
    assert payload["messaging_product"] == "whatsapp"
    assert payload["to"] == "201012345678", "Meta wants digits only — the plus is our dialect, not theirs"
    assert payload["type"] == "text"
    assert payload["text"]["body"] == "تم استلام طلبكم"


def test_a_rejected_send_reports_the_error_without_leaking_the_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "(#190) Invalid OAuth access token"}})

    whatsapp_mod.use_transport(httpx.MockTransport(handler))
    try:
        driver = WhatsAppMeta(token=TOKEN, phone_number_id=PHONE_ID, app_secret="s3cret-app")
        result = __import__("asyncio").run(driver.send_text(to="201012345678", body="x"))
    finally:
        whatsapp_mod.use_transport(None)
    assert result["ok"] is False
    assert "Invalid OAuth" in result["detail"]
    assert TOKEN not in json.dumps(result)


def test_signature_is_hmac_over_the_raw_bytes_not_the_parsed_json() -> None:
    driver = WhatsAppMeta(token="t", phone_number_id="p", app_secret="s3cret-app")
    raw = _inbound("wamid.sig")
    assert driver.verify_signature(raw, _signed(raw)) is True
    # one byte of whitespace is enough: re-serializing the JSON before checking would accept this
    assert driver.verify_signature(raw + b" ", _signed(raw)) is False
    assert driver.verify_signature(raw, _signed(raw, "another-secret")) is False
    assert driver.verify_signature(raw, None) is False


async def test_live_signed_webhook_creates_a_request_and_a_duplicate_does_not(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """Wrong signature → 403 (and a recorded rejection); right signature → intake; same wamid → no second row."""
    from app.api.v1.endpoints import webhooks
    from app.core.db import get_sessionmaker
    from app.models.integrations import WebhookEvent
    from sqlalchemy import select

    monkeypatch.setattr(
        webhooks,
        "whatsapp_provider",
        lambda: WhatsAppMeta(token=TOKEN, phone_number_id=PHONE_ID, app_secret="s3cret-app"),
    )
    raw = _inbound("wamid.live_001")

    forged = await client.post(
        "/api/v1/webhooks/whatsapp", content=raw, headers={"content-type": "application/json", "x-hub-signature-256": "sha256=forged"}
    )
    assert forged.status_code == 403, forged.text

    accepted = await client.post(
        "/api/v1/webhooks/whatsapp",
        content=raw,
        headers={"content-type": "application/json", "x-hub-signature-256": _signed(raw)},
    )
    assert accepted.status_code == 200, accepted.text
    first = accepted.json()["results"][0]
    assert first["status"] in {"created", "linked", "activity"}, first
    request_id = first.get("request_id")
    assert request_id

    duplicate = await client.post(
        "/api/v1/webhooks/whatsapp",
        content=raw,
        headers={"content-type": "application/json", "x-hub-signature-256": _signed(raw)},
    )
    assert duplicate.json()["results"][0]["status"] == "duplicate"

    async with login(client, MANAGER) as auth:
        messages = await client.get(f"/api/v1/requests/{request_id}/messages", headers=auth)
    assert len(messages.json()) == 1

    async with get_sessionmaker()() as session:
        rows = (
            (
                await session.execute(
                    select(WebhookEvent).where(WebhookEvent.provider == "whatsapp").order_by(WebhookEvent.occurred_at.desc())
                )
            )
            .scalars()
            .all()
        )
    assert any(row.event_type == "rejected" and row.status == "error" for row in rows), (
        "a forged delivery must leave a trace for the operator"
    )
    assert all(TOKEN not in json.dumps({"d": row.detail, "e": row.external_id}) for row in rows)


# ------------------------------------------------------------------------------------------ Mobile calls


def test_parse_event_accepts_the_aliases_real_mobile_call_providers_send() -> None:
    twilio_like = json.dumps(
        {
            "CallSid": "CA-live-1",
            "CallStatus": "call.ended",
            "From": "+201001112223",
            "To": "+2022345678",
            "Duration": "47",
            "RecordingUrl": "https://pbx.example.com/rec/1.wav",
        }
    ).encode()
    event = parse_event(twilio_like)
    assert event["provider_call_id"] == "CA-live-1"
    assert event["event"] == "call.ended"
    assert event["from_number"] == "+201001112223"
    assert event["duration_s"] == "47"
    assert event["recording_url"] == "https://pbx.example.com/rec/1.wav"

    with pytest.raises(ValidationFailed):
        parse_event(b"not json")
    with pytest.raises(ValidationFailed):
        parse_event(b"[1,2,3]")


def test_recording_is_fetched_from_the_provider_host_and_anywhere_else_is_refused() -> None:

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "pbx.example.com"
        return httpx.Response(200, content=b"RIFFfake-wav-bytes", headers={"content-type": "audio/wav"})

    mobile_calls_mod.use_transport(httpx.MockTransport(handler))
    try:
        driver = MobileCallsProvider(webhook_secret="s3cret", provider_url="https://pbx.example.com/api")
        assert asyncio.run(driver.fetch_recording("https://pbx.example.com/rec/1.wav")) == b"RIFFfake-wav-bytes"
        # an SSRF guard, not politeness: the same signed body could carry any URL
        with pytest.raises(ValidationFailed):
            asyncio.run(driver.fetch_recording("http://169.254.169.254/latest/meta-data/"))
        with pytest.raises(ValidationFailed):
            asyncio.run(driver.fetch_recording("https://evil.example.net/rec.wav"))
    finally:
        mobile_calls_mod.use_transport(None)


def test_probe_tells_the_operator_whether_the_mobile_calls_provider_is_reachable() -> None:

    mobile_calls_mod.use_transport(httpx.MockTransport(lambda request: httpx.Response(200, text="ok")))
    try:
        ok, detail = asyncio.run(MobileCallsProvider(webhook_secret="s", provider_url="https://pbx.example.com").probe())
    finally:
        mobile_calls_mod.use_transport(None)
    assert ok is True and "200" in detail

    mobile_calls_mod.use_transport(httpx.MockTransport(lambda request: httpx.Response(503)))
    try:
        down, down_detail = asyncio.run(MobileCallsProvider(webhook_secret="s", provider_url="https://pbx.example.com").probe())
    finally:
        mobile_calls_mod.use_transport(None)
    assert down is False and "503" in down_detail

    unreachable, message = asyncio.run(MobileCallsProvider(webhook_secret="s", provider_url="").probe())
    assert unreachable is False and "provider_url" in message


async def test_live_call_webhook_stores_the_recording_and_is_idempotent(client, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.v1.endpoints import webhooks
    from app.core.db import get_sessionmaker
    from app.integrations.storage import storage
    from app.models.requests import CallRecord
    from sqlalchemy import select

    driver = MobileCallsProvider(webhook_secret="s3cret-app", provider_url="https://pbx.example.com")
    monkeypatch.setattr(webhooks, "mobile_calls_provider", lambda: driver)

    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetched.append(str(request.url))
        return httpx.Response(200, content=b"RIFF-live-recording", headers={"content-type": "audio/wav"})

    mobile_calls_mod.use_transport(httpx.MockTransport(handler))
    try:
        started = {"CallSid": "CA-live-900", "CallStatus": "call.started", "From": "+201000000001"}
        raw = json.dumps(started).encode()
        response = await client.post(
            "/api/v1/webhooks/mobile-calls", content=raw, headers={"content-type": "application/json", "x-call-signature": _signed(raw)}
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] in {"started", "created", "linked"}

        ended = {
            "CallSid": "CA-live-900",
            "CallStatus": "recording_ready",
            "From": "+201000000001",
            "Duration": "31",
            "RecordingUrl": "https://pbx.example.com/rec/900.wav",
        }
        raw_end = json.dumps(ended).encode()
        closed = await client.post(
            "/api/v1/webhooks/mobile-calls",
            content=raw_end,
            headers={"content-type": "application/json", "x-call-signature": _signed(raw_end)},
        )
        assert closed.status_code == 200, closed.text
    finally:
        mobile_calls_mod.use_transport(None)

    assert fetched == ["https://pbx.example.com/rec/900.wav"], "the recording is pulled while the URL is alive"

    async with get_sessionmaker()() as session:
        records = (await session.execute(select(CallRecord).where(CallRecord.provider_call_id == "CA-live-900"))).scalars().all()
    assert len(records) == 1, "two events, one call — never a second row"
    record = records[0]
    assert record.recording_object_key, "the file must be ours, not a link that expires"
    assert storage().get_object(record.recording_object_key) == b"RIFF-live-recording"

    # a replay of the same started event changes nothing
    replay = json.dumps(started).encode()
    again = await client.post(
        "/api/v1/webhooks/mobile-calls",
        content=replay,
        headers={"content-type": "application/json", "x-call-signature": _signed(replay)},
    )
    assert again.json()["status"] == "duplicate"


async def test_telephony_webhook_refuses_a_bad_signature(client, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.v1.endpoints import webhooks

    monkeypatch.setattr(webhooks, "mobile_calls_provider", lambda: MobileCallsProvider(webhook_secret="s3cret-app"))
    raw = json.dumps({"CallSid": "CA-bad", "CallStatus": "call.started", "From": "+201000000009"}).encode()
    response = await client.post(
        "/api/v1/webhooks/mobile-calls",
        content=raw,
        headers={"content-type": "application/json", "x-call-signature": _signed(raw, "wrong")},
    )
    assert response.status_code == 403


async def test_a_live_webhook_with_no_app_secret_is_refused_not_forged_through(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """The empty-secret case, end to end: live driver, no App Secret saved, a body signed with HMAC(b"", …).

    Signing with an empty key is computable by anyone, so accepting it would turn "App Secret not configured
    yet" into an open intake endpoint. It must be a 403 with a recorded rejection (R7).
    """
    from app.api.v1.endpoints import webhooks

    monkeypatch.setattr(webhooks, "whatsapp_provider", lambda: WhatsAppMeta(token=TOKEN, phone_number_id=PHONE_ID, app_secret=""))
    raw = _inbound("wamid.no_secret_001")
    empty_key_signature = "sha256=" + hmac.new(b"", raw, hashlib.sha256).hexdigest()
    response = await client.post(
        "/api/v1/webhooks/whatsapp",
        content=raw,
        headers={"content-type": "application/json", "x-hub-signature-256": empty_key_signature},
    )
    assert response.status_code == 403, response.text


def test_settings_connection_test_reports_the_missing_url_instead_of_a_green_light() -> None:
    """`_test_mobile_calls` must not answer "signature ok" for a provider nobody could reach."""
    import inspect

    from app.services import integrations_settings as cfg

    assert inspect.iscoroutinefunction(cfg._test_mobile_calls)
    outcome = asyncio.run(
        cfg._test_mobile_calls(
            cfg.ResolvedIntegration(provider="telephony", is_live=True, config={"provider_url": ""}, secrets={"webhook_secret": "s3cret"})
        )
    )
    assert outcome.ok is False
    assert "provider_url" in outcome.detail


def _unused(*_: Any) -> None:  # keeps `Any` imported for the annotations above
    return None


async def test_a_staff_reply_leaves_the_building_through_the_live_driver(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """The outbound half of pillar 1: `POST /requests/{id}/messages` must reach Meta, not a recorder."""
    import httpx as _httpx
    from app.api.v1.endpoints import webhooks  # noqa: F401 - imported so the module cache is warm
    from app.integrations import registry

    calls: list[dict[str, Any]] = []

    def handler(request: _httpx.Request) -> _httpx.Response:
        calls.append({"url": str(request.url), "auth": request.headers.get("authorization"), "body": json.loads(request.content)})
        return _httpx.Response(200, json={"messages": [{"id": "wamid.OUT_1"}]})

    whatsapp_mod.use_transport(_httpx.MockTransport(handler))
    monkeypatch.setattr(registry, "whatsapp_provider", lambda: WhatsAppMeta(token=TOKEN, phone_number_id=PHONE_ID, app_secret="s3cret-app"))
    try:
        raw = _inbound("wamid.live_out_001", mobile="+201055554444", text="محتاج عرض سعر")
        accepted = await client.post(
            "/api/v1/webhooks/whatsapp",
            content=raw,
            headers={"content-type": "application/json", "x-hub-signature-256": _signed(raw)},
        )
        assert accepted.status_code == 200, accepted.text
        request_id = accepted.json()["results"][0]["request_id"]

        async with login(client, MANAGER) as auth:
            reply = await client.post(
                f"/api/v1/requests/{request_id}/messages",
                json={"body": "أهلاً بك، العرض وصلك على واتساب", "channel": "whatsapp"},
                headers=auth,
            )
            assert reply.status_code in (200, 201), reply.text
            thread = await client.get(f"/api/v1/requests/{request_id}/messages", headers=auth)
    finally:
        whatsapp_mod.use_transport(None)

    assert len(calls) == 1, "exactly one outbound call — no duplicate send on the reply path"
    assert calls[0]["url"].startswith("https://graph.facebook.com/v21.0/")
    assert calls[0]["auth"] == f"Bearer {TOKEN}"
    assert calls[0]["body"]["to"] == "201055554444"
    assert calls[0]["body"]["text"]["body"] == "أهلاً بك، العرض وصلك على واتساب"

    outbound = [row for row in thread.json() if row["direction"] == "out"]
    assert outbound and outbound[-1]["delivery_status"] == "sent"
    assert outbound[-1]["external_id"] == "wamid.OUT_1"
    assert TOKEN not in json.dumps(thread.json()), "the token must never surface in any response"


def test_webhook_event_status_column_fits_every_status_the_intake_writes() -> None:
    """CI found this, the suite could not: SQLite ignores `VARCHAR(n)`, PostgreSQL does not.

    A real `recording_ready` webhook answered **500** on PostgreSQL — after the call had been ingested and
    the recording stored — because `event.status` is the intake's own result and `recording_attached` is 18
    characters against a `VARCHAR(16)`. The literals are read out of the source, so a new one is covered the
    day it is added rather than the day it 500s.
    """
    from app.models.integrations import WebhookEvent

    source = (Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "endpoints" / "webhooks.py").read_text()
    intake = (Path(__file__).resolve().parents[1] / "app" / "services" / "intake.py").read_text()
    literals = set(re.findall(r'status="([a-z_]+)"', source)) | set(re.findall(r'"status": "([a-z_]+)"', intake))
    literals |= {"ok", "ignored", "error", "rejected"}

    width = WebhookEvent.__table__.c.status.type.length
    assert width is not None
    too_long = sorted(status for status in literals if len(status) > width)
    assert not too_long, f"statuses wider than webhook_events.status ({width}): {too_long}"


async def test_record_event_clamps_rather_than_breaking_the_webhook(session: Any) -> None:
    """The clamp is the second line of defence: a provider-shaped string must never abort a saved call."""
    from app.models.integrations import WebhookEvent
    from app.services import integrations_settings

    await integrations_settings.record_event(session, provider="telephony", event_type="call.ended", external_id="x", status="s" * 80)
    await session.commit()
    row = (
        (await session.execute(select(WebhookEvent).where(WebhookEvent.external_id == "x").order_by(WebhookEvent.occurred_at.desc())))
        .scalars()
        .first()
    )
    assert row is not None
    assert row.status == "s" * 32
