"""The simulator must work while the live WhatsApp driver is configured (item 2).

Reported failure: `POST /api/v1/dev/sim/whatsapp` with `media_id="sim-media-<ts>"` returned **500** once
WhatsApp was configured live. `whatsapp_provider()` then hands back the real Meta driver, `stage_inbound_media`
asked Graph about an id Meta never issued, `fetch_media` raised `MediaFetchError`, and the endpoint — written
before that exception existed — let it escape.

The panel is the screen a developer opens *because* the real channel is not working yet; a 500 there is the
worst possible answer.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import UUID

import httpx
import pytest
from app.api.v1.endpoints import dev_sim
from app.integrations import whatsapp as whatsapp_mod
from app.integrations.whatsapp import WhatsAppMeta
from app.models.integrations import WebhookEvent
from app.models.requests import Attachment
from sqlalchemy import select

from tests.helpers import login

TOKEN = "EAAG-live-token-for-the-simulator-test"
PHONE_ID = "109999888777"
MANAGER = "manager@mantiq.test"


@pytest.fixture(autouse=True)
def provider_calls(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Pretend the operator configured WhatsApp live: the endpoint must still serve the simulator.

    The transport answers 400 to everything and records what was asked, so a test can prove the simulator
    either *did* consult Meta (and survived) or *never* did.
    """
    seen: list[str] = []

    def refuse(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(400, json={"error": {"message": "(#100) Unsupported get request"}})

    whatsapp_mod.use_transport(httpx.MockTransport(refuse))
    monkeypatch.setattr(dev_sim, "whatsapp_provider", lambda: WhatsAppMeta(token=TOKEN, phone_number_id=PHONE_ID, app_secret="s3cret"))
    try:
        yield seen
    finally:
        whatsapp_mod.use_transport(None)


async def _request_detail(client: Any, request_id: str) -> dict[str, Any]:
    async with login(client, MANAGER) as auth:
        detail = await client.get(f"/api/v1/requests/{request_id}", headers=auth)
    assert detail.status_code == 200, detail.text
    return detail.json()


async def test_a_simulated_voice_note_is_staged_without_touching_meta(client: Any, session: Any, provider_calls: list[str]) -> None:
    inbound = await client.post(
        "/api/v1/dev/sim/whatsapp",
        json={
            "mobile": "+201119997700",
            "sender_name": "منى — شركة ألف",
            "body": None,
            "media_kind": "audio/ogg",
            "media_id": "sim-media-1759999999",
            "duration_s": 11,
            "external_id": "wamid.sim_media_ok_1",
        },
    )
    assert inbound.status_code == 200, inbound.text
    assert provider_calls == [], "a simulated media id must never be sent to the provider"

    detail = await _request_detail(client, inbound.json()["request_id"])
    message = detail["messages"][0]
    assert message["media_kind"] == "audio/ogg"

    attachments = (await session.execute(select(Attachment).where(Attachment.request_id == UUID(detail["id"])))).scalars().all()
    assert attachments, "the voice note must exist as a real attachment row, not only as a message field"
    attachment = attachments[0]
    assert attachment.mime == dev_sim.SIM_AUDIO_MIME
    assert attachment.size == len(dev_sim.SIM_AUDIO_BYTES)
    assert attachment.object_key.startswith("media/wamid.sim_media_ok_1/")


async def test_a_real_looking_media_id_fails_soft_and_records_the_event(client: Any, session: Any, provider_calls: list[str]) -> None:
    """Meta answers 400 for the fake id: the text still lands, and the failure is visible in the event log."""
    inbound = await client.post(
        "/api/v1/dev/sim/whatsapp",
        json={
            "mobile": "+201119997701",
            "sender_name": "عميل",
            "body": "المرفق مش بيوصل",
            "media_kind": "image/jpeg",
            "media_id": "1234567890123456",  # looks real, is not
            "external_id": "wamid.sim_media_fail_1",
        },
    )
    assert inbound.status_code == 200, inbound.text
    assert provider_calls, "the provider should have been asked about a real-looking id"

    detail = await _request_detail(client, inbound.json()["request_id"])
    assert detail["messages"][0]["body"] == "المرفق مش بيوصل", "the text must survive the media failure"
    assert detail["messages"][0]["attachments"] == [], "no attachment can exist for a file we could not read"

    events = (await session.execute(select(WebhookEvent).where(WebhookEvent.external_id == "wamid.sim_media_fail_1"))).scalars().all()
    failed = [event for event in events if event.event_type == "media_fetch_failed"]
    assert failed, f"expected a media_fetch_failed event, got {[e.event_type for e in events]}"
    assert failed[0].status == "error"
    assert failed[0].provider == "whatsapp"
    assert "MediaFetchError" in (failed[0].detail or "")
