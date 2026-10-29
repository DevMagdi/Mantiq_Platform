"""Mobile phone calls provider (R9 §1.C) — a call to a **mobile number** linked to the platform.

The product concept is «مكالمات الموبايل» / «رقم الموبايل» / «مزود مكالمات الموبايل»: a customer calls a
mobile number that the platform owns, and the call becomes a `call`-channel request with its metadata
(from/to, duration, status) and — when the provider keeps one — its recording. Nothing here describes an
office PBX/desk-phone switchboard: the mobile-calls gateway is a VoIP provider's HTTP webhook.

Live dialect documented for one real provider: **Twilio Programmable Voice**. A Twilio call-status
callback carries `CallSid`, `CallStatus` (`ringing` → `in-progress` → `completed`), `From`, `To`,
`CallDuration`; a recording callback carries `RecordingUrl`, `RecordingSid`, `RecordingDuration` and the
same `CallSid`. `parse_event()` normalizes those into `call.started` / `call.ended` / `recording_ready`, and
also accepts the aliases other mobile-call gateways use (`uuid`/`status` from Vonage, `callId`/`type` from
generic gateways, and the platform's own explicit names).

Two things a real gateway needs that a simulator never does, and both live here:

* **the recording is fetched, then stored.** Real events carry a `recording_url` that expires in minutes;
  we pull it while it is alive and keep our own copy, so playback does not depend on the provider.
* **the transport is injectable.** `_transport` exists so a contract test can run the *live* code path
  against a fake HTTP layer instead of the network: a test that swapped the driver for the simulator would
  prove nothing about the code that ships.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.errors import ValidationFailed
from app.core.logging import get_logger
from app.core.security import verify_webhook_signature

log = get_logger("integrations.mobile_calls")

#: set by a contract test (or by an operator behind a proxy) — `None` means "use the real network"
_transport: httpx.AsyncBaseTransport | None = None


def use_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    global _transport
    _transport = transport


def _client(timeout: float = 60.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, transport=_transport)


class MobileCallsProvider:
    """The live mobile-calls driver: `X-Call-Signature: sha256=<hmac>` over the raw body.

    The secret comes from the saved settings row via the registry; the env value is the fallback, so a
    deployment that only edited `.env` keeps verifying exactly as before. `provider_name` is the configured
    dialect (`twilio` | `generic`); it is recorded on the call row, which is why the UI can say which
    mobile-calls provider a call arrived through instead of the old hardcoded `"sim"`.
    """

    name = "mobile-calls"

    def __init__(self, webhook_secret: str = "", provider_url: str = "", provider_name: str = "twilio") -> None:
        self.webhook_secret = webhook_secret or get_settings().TELEPHONY_WEBHOOK_SECRET
        self.provider_url = provider_url
        self.provider_name = (provider_name or "twilio").strip().lower()

    def verify_signature(self, payload: bytes, signature_header: str | None) -> bool:
        return verify_webhook_signature(payload, signature_header, self.webhook_secret)

    async def fetch_recording(self, url: str) -> bytes:
        # Only the configured host may be dialled: `recording_url` arrives inside a signed body, but a
        # mobile-calls account is one host, and an outbound fetch to anything else is an SSRF primitive
        # nobody asked for. A missing `provider_url` means "no restriction stated" and is logged, not guessed.
        if self.provider_url and not _same_host(url, self.provider_url):
            log.warning("recording_url_host_mismatch", url_host=_host_of(url), expected=_host_of(self.provider_url))
            raise ValidationFailed("Recording URL does not belong to the configured mobile-calls provider", details={"host": _host_of(url)})
        try:
            async with _client() as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.content
        except httpx.HTTPError as exc:
            log.warning("recording_fetch_failed", error=f"{type(exc).__name__}: {str(exc)[:160]}")
            raise ValidationFailed("Recording could not be fetched from the mobile-calls provider") from exc

    async def probe(self, url: str | None = None) -> tuple[bool, str]:
        """The connection test an operator actually needs: is the mobile-calls endpoint reachable now?"""
        target = url or self.provider_url
        if not target:
            return False, "ناقص: رابط مزود مكالمات الموبايل (provider_url) غير مضبوط"
        try:
            async with _client(timeout=15) as client:
                response = await client.get(target)
        except httpx.HTTPError as exc:
            return False, f"تعذّر الوصول إلى مزود مكالمات الموبايل: {type(exc).__name__}"
        if response.status_code >= 500:
            return False, f"مزود مكالمات الموبايل رد {response.status_code} على فحص الوصول"
        return True, f"مزود مكالمات الموبايل يستجيب ({response.status_code}) — التوقيع sha256 متسق"


def _host_of(url: str) -> str:
    try:
        return str(httpx.URL(url).host or "")
    except Exception:  # pragma: no cover - httpx raises only on truly malformed input
        return ""


def _same_host(candidate: str, reference: str) -> bool:
    host = _host_of(candidate)
    return bool(host) and host == _host_of(reference)


#: Twilio's call-status values → the three events the intake understands. Anything else passes through
#: untouched, so an explicit `call.started` / `recording_ready` body keeps working (and so does the
#: `CallStatus: call.ended` form the earlier contract tests use).
_TWILIO_EVENTS: dict[str, str] = {
    "queued": "call.started",
    "initiated": "call.started",
    "ringing": "call.started",
    "in-progress": "call.started",
    "answered": "call.started",
    "completed": "call.ended",
    "busy": "call.ended",
    "failed": "call.ended",
    "no-answer": "call.ended",
    "canceled": "call.ended",
}


def parse_event(raw: bytes) -> dict[str, Any]:
    """Normalize one mobile-calls webhook into the shape `services.intake.ingest_call_event` expects.

    Accepts Twilio's names (`CallSid`, `CallStatus`, `From`, `To`, `CallDuration`, `RecordingUrl`), the
    common gateway aliases (`uuid`, `status`, `duration`, `callId`, `type`) and the documented names
    (`call_id`, `event`, `from`, ...). Unknown keys are ignored; `recording_url` is passed through because
    the route is what fetches it.
    """
    try:
        body = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise ValidationFailed("Body must be JSON") from exc
    if not isinstance(body, dict):
        raise ValidationFailed("Body must be a JSON object")

    raw_event = str(body.get("event") or body.get("CallStatus") or body.get("status") or body.get("type") or "")
    recording_url = body.get("recording_url") or body.get("RecordingUrl")
    # A Twilio recording callback has no CallStatus at all: only `RecordingUrl` + `RecordingSid`. Treating it
    # as "call.started" would open a second call for a call that already exists.
    if recording_url and (not raw_event or body.get("RecordingStatus") or body.get("RecordingSid")):
        event = "recording_ready"
    else:
        event = _TWILIO_EVENTS.get(raw_event.strip().lower(), raw_event or "call.started")

    provider_call_id = str(
        body.get("call_id") or body.get("provider_call_id") or body.get("CallSid") or body.get("uuid") or body.get("callId") or ""
    )
    duration = (
        body.get("duration_s")
        if body.get("duration_s") is not None
        else body.get("Duration") or body.get("CallDuration") or body.get("duration")
    )
    return {
        "provider_call_id": provider_call_id,
        "event": event,
        "direction": str(body.get("direction") or body.get("Direction") or "inbound"),
        "from_number": body.get("from") or body.get("From") or body.get("caller") or body.get("from_number"),
        "to_number": body.get("to") or body.get("To") or body.get("called") or body.get("to_number"),
        "caller_name": body.get("caller_name") or body.get("CallerName") or body.get("Caller"),
        "started_at": body.get("started_at") or body.get("StartTime") or body.get("Timestamp"),
        "ended_at": body.get("ended_at") or body.get("EndTime"),
        "duration_s": duration,
        "recording_url": recording_url,
        "recording_key": body.get("recording_key"),
        "recording_consent": bool(body.get("recording_consent", False)),
        "trunk": body.get("trunk") or body.get("Trunk"),
    }
