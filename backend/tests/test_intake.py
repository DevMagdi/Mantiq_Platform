"""Inbound intake: webhooks (signature, idempotency) and the mandatory simulator (spec §8)."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from uuid import UUID

import pytest
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.domain.enums import ScopeType
from app.integrations.sims import WhatsAppSim
from app.models.auth import User, UserScope
from app.models.requests import AuditLog
from sqlalchemy import select

from tests.helpers import login

MANAGER = "manager@brokerhub.test"
REP = "rep_a@brokerhub.test"
CS = "cs@brokerhub.test"


def _payload(external_id: str, mobile: str = "01099887766", body: str = "عايز أزيد عدد الموظفين") -> bytes:
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"wa_id": mobile, "profile": {"name": "موظف"}}],
                                "messages": [{"id": external_id, "from": mobile, "type": "text", "text": {"body": body}}],
                            }
                        }
                    ]
                }
            ],
        }
    ).encode()


def _signature(raw: bytes) -> str:
    digest = hmac.new(get_settings().WHATSAPP_APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_webhook_signature_helpers_accept_correct_and_reject_tampered() -> None:
    from app.core.security import verify_webhook_signature

    raw = _payload("wamid.unit")
    secret = get_settings().WHATSAPP_APP_SECRET
    assert verify_webhook_signature(raw, _signature(raw), secret) is True
    assert verify_webhook_signature(raw + b" ", _signature(raw), secret) is False
    assert verify_webhook_signature(raw, None, secret) is False
    assert verify_webhook_signature(raw, "sha256=deadbeef", secret) is False
    # R7: an unset secret must refuse, not sign with the empty key — otherwise a live deployment that lost
    # its App Secret accepts any body signed with HMAC("", body), which anyone can compute.
    import hashlib
    import hmac

    forgeable = "sha256=" + hmac.new(b"", raw, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(raw, forgeable, "") is False


@pytest.mark.parametrize("mobile", ["01099887766", "+201099887766", "00201099887766"])
async def test_inbound_whatsapp_is_idempotent_per_external_id(client, ids: dict[str, dict[str, str]], mobile: str) -> None:
    """Meta retries the same `wamid` — one message row, one request (spec §8.2)."""
    WhatsAppSim.reset()
    async with login(client, MANAGER) as auth:
        raw = _payload("wamid.dup_001", mobile=mobile)
        first = await client.post("/api/v1/webhooks/whatsapp", content=raw, headers={"content-type": "application/json"})
        assert first.status_code == 200, first.text
        request_id = first.json()["results"][0]["request_id"]
        assert request_id

        second = await client.post("/api/v1/webhooks/whatsapp", content=raw, headers={"content-type": "application/json"})
        assert second.json()["results"][0]["status"] == "duplicate"

        messages = await client.get(f"/api/v1/requests/{request_id}/messages", headers=auth)
        assert len(messages.json()) == 1

        inbox = await client.get("/api/v1/requests?q=01099887766", headers=auth)
        matched = [row for row in inbox.json()["items"] if row["id"] == request_id]
        assert matched and matched[0]["channel"] == "whatsapp"
        assert matched[0]["number"] >= 1000


async def test_inbound_whatsapp_rejects_invalid_signature(client, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Strict:
        name = "strict"

        def verify_signature(self, _payload: bytes, _signature_header: str | None) -> bool:
            return False

    from app.api.v1.endpoints import webhooks

    monkeypatch.setattr(webhooks, "whatsapp_provider", lambda: _Strict())
    response = await client.post(
        "/api/v1/webhooks/whatsapp",
        content=_payload("wamid.bad_sig"),
        headers={"content-type": "application/json", "x-hub-signature-256": "sha256=nope"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


async def test_simulator_call_from_unknown_number_lands_in_staff_queue(client, ids: dict[str, dict[str, str]]) -> None:
    """§8.4: an unmatched call must still be triageable — it opens a client-less request (§6: reps
    only see requests tied to their own clients, so managers/admins triage these)."""
    body: dict[str, Any] = {"from_number": "01199998888", "duration_s": 40, "caller_name": "متصل غير مسجل"}
    async with login(client, MANAGER) as auth:
        started = await client.post("/api/v1/dev/sim/call", json=body, headers=auth)
        assert started.status_code == 200, started.text
        payload = started.json()
        assert payload["status"] == "ended"
        request_id = payload["request_id"]
        assert request_id, "unknown callers must not be dropped"

        row = await client.get(f"/api/v1/requests/{request_id}", headers=auth)
        assert row.status_code == 200
        assert row.json()["channel"] == "call"
        assert row.json()["client_id"] is None

        calls = await client.get(f"/api/v1/requests/{request_id}/calls", headers=auth)
        record = calls.json()[0]
        assert record["has_recording"] is True
        assert record["listen_allowed"] is True

    # A rep has no `requests.view` at all: the permission layer answers, before scope (§6.1).
    async with login(client, REP) as rep_auth:
        denied = await client.get(f"/api/v1/requests/{request_id}", headers=rep_auth)
        assert denied.status_code == 403

    # A service agent pinned to one client must get 404 for a request outside that pin.
    maker = get_sessionmaker()
    async with maker() as session:
        cs = (await session.execute(select(User).where(User.email == CS))).scalar_one()
        session.add(UserScope(user_id=cs.id, scope_type=ScopeType.CLIENT, scope_id=UUID(ids["clients"]["باء"])))
        await session.commit()

    async with login(client, CS) as cs_auth:
        hidden = await client.get(f"/api/v1/requests/{request_id}", headers=cs_auth)
        assert hidden.status_code == 404  # out of scope => 404, never 403 (§6)
        listed = await client.get("/api/v1/requests", headers=cs_auth)
        assert request_id not in {row["id"] for row in listed.json()["items"]}


async def test_recording_url_is_permissioned_signed_and_audited(client, ids: dict[str, dict[str, str]]) -> None:
    async with login(client, MANAGER) as auth:
        started = await client.post("/api/v1/dev/sim/call", json={"from_number": "01122223333", "duration_s": 12}, headers=auth)
        request_id = started.json()["request_id"]
        assert request_id
        calls = await client.get(f"/api/v1/requests/{request_id}/calls", headers=auth)
        call_id = calls.json()[0]["id"]

        issued = await client.get(f"/api/v1/calls/{call_id}/recording-url", headers=auth)
        assert issued.status_code == 200, issued.text
        url = issued.json()["url"]
        assert "sig=" in url and "expires=" in url

        media = await client.get(url)
        assert media.status_code == 200
        assert media.content[:4] == b"RIFF"
        assert media.headers.get("content-type", "").startswith("audio/")

    async with login(client, REP) as rep_auth:
        denied = await client.get(f"/api/v1/calls/{call_id}/recording-url", headers=rep_auth)
        assert denied.status_code == 403  # reps have no `calls.listen` (§6.4)

    maker = get_sessionmaker()
    async with maker() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "call.recording_listened", AuditLog.entity_id == UUID(call_id))
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1, "each listen writes exactly one audit row"
    assert rows[0].after["ttl_seconds"] == get_settings().RECORDING_SIGNED_URL_TTL_SECONDS


async def test_voice_note_media_is_playable_for_staff_and_hidden_from_the_portal(client, ids: dict[str, dict[str, str]]) -> None:
    """§9.2: a WhatsApp voice note carries a real, signed media URL — for staff only."""
    owner_mobile = "01000000001"  # conftest lead_a's mobile → matched as a lead, so use a client contact instead
    assert owner_mobile
    raw = json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [],
                                "messages": [
                                    {
                                        "id": "wamid.voice_note_1",
                                        "from": "201055566778",
                                        "type": "audio",
                                        "audio": {"id": "media_1", "duration": 7},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ],
        }
    ).encode()
    delivered = await client.post("/api/v1/webhooks/whatsapp", content=raw, headers={"content-type": "application/json"})
    assert delivered.status_code == 200, delivered.text
    request_id = delivered.json()["results"][0]["request_id"]

    async with login(client, MANAGER) as auth:
        detail = await client.get(f"/api/v1/requests/{request_id}", headers=auth)
        body = detail.json()
        voice = next(message for message in body["messages"] if message["media_kind"] == "audio")
        assert voice["duration_s"] == 7
        assert body["attachments"], "the media object must be attached to the request"
        url = body["attachments"][0]["url"]
        played = await client.get(url)
        assert played.status_code == 200
        assert len(played.content) > 0

    async with login(client, "owner@brokerhub.test") as auth:
        portal = await client.get("/api/v1/portal/requests", headers=auth)
    assert all("url" not in row for row in portal.json()["items"])


async def test_simulator_panel_voice_note_is_stored_and_playable(client, ids: dict[str, dict[str, str]]) -> None:
    """/dev/sim is the only way to demo inbound media without credentials — it must be real, not cosmetic."""
    async with login(client, "manager@brokerhub.test") as auth:
        panel = await client.get("/api/v1/dev/sim")
        assert panel.status_code == 200
        assert "محاكي القنوات الواردة" in panel.text

        mobile = "+201119998877"
        inbound = await client.post(
            "/api/v1/dev/sim/whatsapp",
            json={
                "mobile": mobile,
                "sender_name": "منى — شركة ألف",
                "body": None,
                "media_kind": "audio/ogg",
                "media_id": "sim-media-panel-1",
                "duration_s": 9,
            },
        )
        assert inbound.status_code == 200, inbound.text
        request_id = inbound.json()["request_id"]
        # an unrecognised number still lands in the unsorted queue — that is the point of the panel
        assert inbound.json()["matched_kind"] in {"client", "unknown", None}

        detail = await client.get(f"/api/v1/requests/{request_id}", headers=auth)
        assert detail.status_code == 200
        message = detail.json()["messages"][0]
        assert message["media_kind"] == "audio/ogg"
        assert message["duration_s"] == 9
        attachment = message["attachments"][0]
        assert attachment["mime"].startswith("audio/")

        played = await client.get(attachment["url"])
        assert played.status_code == 200
        assert played.content
