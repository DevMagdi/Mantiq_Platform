"""Simulator endpoints — **mounted only when `ENV=dev|test`** (R9 §1.A, §4).

These three routes used to live next to the real webhooks, so a production build exposed
`/api/v1/dev/sim*` and relied on each handler to remember to check the driver. R9 moves them into their own
router, which `app/main.py` includes only when `settings.simulation_allowed` is true — in production the
paths do not exist (404 from the router, not a 403 from a handler) and `tests/test_production_live_only.py`
pins that.

Each handler *also* calls `require_simulation_allowed()`: two independent gates, because "the router was
included by mistake" must not be the only thing standing between production and a fake call.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.api.v1.deps import DbSession
from app.api.v1.endpoints.webhooks import stage_inbound_media
from app.integrations.registry import whatsapp_provider
from app.integrations.sims import require_simulation_allowed
from app.integrations.storage import storage
from app.services import intake, integrations_settings

router = APIRouter(tags=["simulator (dev/test only)"])

#: Media ids the panel makes up itself. They mean nothing to Meta, so they must never reach a provider call.
SIM_MEDIA_PREFIX = "sim-media-"
#: What a simulated voice note "contains" — enough bytes to prove the storage path ran end to end.
SIM_AUDIO_BYTES = b"SIM-AUDIO"
SIM_AUDIO_MIME = "audio/ogg"


def _safe(part: str, limit: int) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", part)[:limit]


def _stage_sim_media(external_id: str, media_id: str) -> tuple[str, str, int]:
    """Write the fake bytes ourselves instead of asking Meta about an id it never issued.

    The panel used to hand `sim-media-<ts>` to `stage_inbound_media`, which called Graph whenever the WhatsApp
    driver was live: Meta answered 400, `fetch_media` raised, and the simulator endpoint returned a 500 — the
    one screen a developer opens *because* the real channel is not working yet.
    """
    key = f"media/{_safe(external_id, 80)}/{_safe(media_id, 60)}"
    storage().put_object(key, SIM_AUDIO_BYTES, SIM_AUDIO_MIME)
    return key, SIM_AUDIO_MIME, len(SIM_AUDIO_BYTES)


_PANEL_HTML_PATH = Path(__file__).with_name("sim_panel.html")


#: Replaced with a JSON string literal (quotes and all) before the page is served — never with raw text.
_FRONTEND_SLOT = "__FRONTEND_URL__"


def _panel_html(frontend_url: str) -> str:
    """The markup sits in a sibling file so the CSS lines stay readable.

    The app's address is *injected*, not derived from the request: the panel is served by the API on :8000,
    so `location.origin + "/inbox"` is a 404 — the screen lives on the SPA's origin (:5173 in development,
    a different host entirely in production). `json.dumps` is used so a configured URL can never break out of
    the string literal it lands in.
    """
    return _PANEL_HTML_PATH.read_text(encoding="utf-8").replace(_FRONTEND_SLOT, json.dumps(frontend_url.rstrip("/")))


class _SimWhatsApp(BaseModel):
    mobile: str = Field(description="caller/employee mobile, any Egyptian format")
    body: str | None = None
    sender_name: str | None = None
    media_kind: str | None = None
    media_id: str | None = Field(default=None, description="set it to fetch (and store) a fake voice note")
    duration_s: int | None = None
    external_id: str | None = None


@router.post("/dev/sim/whatsapp", summary="Simulator: deliver an inbound WhatsApp message (dev/test only)")
async def sim_whatsapp(body: _SimWhatsApp, session: DbSession) -> dict[str, Any]:
    require_simulation_allowed("Simulator API")
    external_id = body.external_id or f"wamid.sim_{datetime.now(UTC).timestamp():.6f}"
    media_key: str | None = None
    media_mime: str | None = None
    media_size = 0
    if body.media_id:
        if body.media_id.startswith(SIM_MEDIA_PREFIX):
            media_key, media_mime, media_size = _stage_sim_media(external_id, body.media_id)
        else:
            # A real-looking id: go through the provider, but a provider/storage failure must not take the
            # message down with it — the same rule the live webhook follows (one unreadable file ≠ lost text).
            try:
                media_key, media_mime, media_size = await stage_inbound_media(whatsapp_provider(), storage(), external_id, body.media_id)
            except Exception as exc:
                await integrations_settings.record_event(
                    session,
                    provider="whatsapp",
                    event_type="media_fetch_failed",
                    external_id=external_id,
                    status="error",
                    detail=f"{type(exc).__name__}: {str(exc)[:200]}",
                )
                await session.commit()
    result = await intake.ingest_whatsapp_message(
        session,
        external_id=external_id,
        mobile=body.mobile,
        body=body.body,
        sender_name=body.sender_name,
        media_kind=body.media_kind,
        media_object_key=media_key,
        media_mime=media_mime,
        media_size=media_size,
        duration_s=body.duration_s,
    )
    await session.commit()
    return result


@router.get("/dev/sim", summary="Simulator panel (dev/test only)", include_in_schema=False)
async def sim_panel() -> HTMLResponse:
    require_simulation_allowed("Simulator panel")
    from app.core.config import get_settings

    return HTMLResponse(_panel_html(get_settings().frontend_url))


class _SimCall(BaseModel):
    provider_call_id: str | None = None
    from_number: str
    to_number: str | None = None
    caller_name: str | None = None
    duration_s: int = 45
    with_recording: bool = True
    recording_consent: bool = True


@router.post("/dev/sim/call", summary="Simulator: mobile call → request → recording in storage (dev/test only)")
async def sim_call(body: _SimCall, session: DbSession) -> dict[str, Any]:
    require_simulation_allowed("Simulator API")
    call_id = body.provider_call_id or f"simcall_{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
    started = datetime.now(UTC)
    started_result = await intake.ingest_call_event(
        session,
        provider_call_id=call_id,
        event="call.started",
        from_number=body.from_number,
        to_number=body.to_number,
        caller_name=body.caller_name,
        started_at=started,
        recording_consent=body.recording_consent,
        provider_name="sim",
    )
    await session.commit()
    ended = await intake.ingest_call_event(
        session,
        provider_call_id=call_id,
        event="call.ended",
        from_number=body.from_number,
        started_at=started,
        ended_at=datetime.now(UTC),
        duration_s=body.duration_s,
        provider_name="sim",
    )
    await session.commit()

    media_key = None
    if body.with_recording:
        from app.core.storage_bytes import silence_wav

        media_key = f"calls/{call_id}.wav"
        payload = silence_wav(seconds=max(1.0, min(float(body.duration_s), 120.0)), tone_hz=320.0)
        storage().put_object(media_key, payload, "audio/wav")
        attached = await intake.ingest_call_event(
            session,
            provider_call_id=call_id,
            event="recording_ready",
            from_number=body.from_number,
            recording_object_key=media_key,
            recording_consent=body.recording_consent,
            recording_size=len(payload),
            provider_name="sim",
        )
        await session.commit()
        ended["recording"] = attached.get("status")
    return {**started_result, **ended, "recording_object_key": media_key}
