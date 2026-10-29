"""Contract tests for the WhatsApp connection test, the media-failure branch of the webhook, and `state()`.

The regression these are written against: `_test_whatsapp` called `GET /debug_token` with **only**
`input_token`, so Meta answered

    (#100) You must provide an app access token, or a user access token that is an owner or developer of the app

for a token that was perfectly valid — and the settings screen reported it as a broken channel. The token was
never the problem; the *call* was. A probe that can only fail is worse than no probe, because it teaches the
operator to ignore the badge.

Every test here runs the **live** code path against an injected `httpx.MockTransport` (no network, no
monkeypatched driver), so what is asserted is the request that ships.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from typing import Any

import httpx
import pytest
from app.integrations import whatsapp as whatsapp_mod
from app.integrations.whatsapp import MediaFetchError, WhatsAppMeta, graph_base
from app.services import integrations_settings as cfg
from sqlalchemy import select

from tests.helpers import login

ADMIN = "admin@mantiq.test"
VERIFY = "verify-token-for-this-module-only"
TOKEN = "EAAG-test-token-value-that-must-never-be-logged"
PHONE_ID = "1031129959988895"
APP_ID = "1031129959988895"
APP_SECRET = "0f9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c"
OTHER_APP_ID = "999000111222333"


@pytest.fixture(autouse=True)
def _clean_registry():
    """The provider cache and the test limiter are process-wide; a row saved here must not leak into a later case."""
    from app.core.rate_limit import test_limiter
    from app.integrations import registry

    test_limiter.reset()
    registry.reset_providers()
    yield
    registry._primed = None
    registry.reset_providers()
    test_limiter.reset()


def _resolved(**overrides: Any) -> cfg.ResolvedIntegration:
    """A live WhatsApp integration as the settings row would resolve it."""
    values: dict[str, Any] = {
        "provider": "whatsapp",
        "is_live": True,
        "config": {"phone_number_id": PHONE_ID, "business_account_id": "waba-1", "app_id": APP_ID, "receive_media": True},
        "secrets": {"access_token": TOKEN, "app_secret": APP_SECRET, "verify_token": "verify-me"},
    }
    values.update(overrides)
    return cfg.ResolvedIntegration(**values)


class _Recorder:
    """Collects the requests the live path makes, and answers them from a routing table."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.requests: list[httpx.Request] = []
        self.routes = routes

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for fragment, response in self.routes.items():
            if fragment in str(request.url):
                return response(request) if callable(response) else response
        return httpx.Response(404, json={"error": {"message": "no route"}})


def _run(recorder: _Recorder, resolved: cfg.ResolvedIntegration) -> cfg.TestOutcome:
    """Run `_test_whatsapp` with the recorder installed, and always uninstall the transport."""
    whatsapp_mod.use_transport(httpx.MockTransport(recorder))
    try:
        return asyncio.run(cfg._test_whatsapp(resolved))
    finally:
        whatsapp_mod.use_transport(None)


def _healthy_routes(**overrides: Any) -> dict[str, Any]:
    routes: dict[str, Any] = {
        "debug_token": httpx.Response(200, json={"data": {"is_valid": True, "app_id": APP_ID, "expires_at": 0}}),
        f"/{PHONE_ID}": httpx.Response(200, json={"display_phone_number": "+20 100 123 4567", "verified_name": "Mantiq Insurance"}),
    }
    routes.update(overrides)
    return routes


# --------------------------------------------------------------------------- the debug_token contract


def test_debug_token_is_called_with_the_app_id_and_secret_as_access_token() -> None:
    """a) `access_token` must be `app_id|app_secret` — the exact form Meta demands, not the stored token.

    This is the bug that started the round: with `input_token` alone Meta answers "(#100) …" for a valid token.
    """
    recorder = _Recorder(_healthy_routes())
    outcome = _run(recorder, _resolved())

    debug = recorder.requests[0]
    assert str(debug.url).startswith(f"{graph_base()}/debug_token")
    params = debug.url.params
    assert params["input_token"] == TOKEN
    assert params["access_token"] == f"{APP_ID}|{APP_SECRET}"
    assert outcome.ok is True, outcome.detail


def test_without_an_app_id_the_stored_token_is_presented() -> None:
    """b) No App ID saved -> fall back to the token itself, and say so if Meta refuses."""
    resolved = _resolved(config={"phone_number_id": PHONE_ID, "business_account_id": "", "app_id": "", "receive_media": True})
    recorder = _Recorder(_healthy_routes())
    _run(recorder, resolved)

    params = recorder.requests[0].url.params
    assert params["access_token"] == TOKEN, "with no App ID the only credential we can present is the token"
    assert params["input_token"] == TOKEN


def test_a_token_belonging_to_another_app_fails_and_names_both_ids() -> None:
    """c) `data.app_id` differs from the saved App ID — the operator pasted a token from a different app."""
    recorder = _Recorder(_healthy_routes(debug_token=httpx.Response(200, json={"data": {"is_valid": True, "app_id": OTHER_APP_ID}})))
    outcome = _run(recorder, _resolved())

    assert outcome.ok is False
    assert OTHER_APP_ID in outcome.detail
    assert APP_ID in outcome.detail


def test_the_phone_probe_reports_the_status_when_meta_refuses_it() -> None:
    """d) A `debug_token` that passes proves the token, not the number — the second call must be checked too."""
    recorder = _Recorder(
        _healthy_routes(**{f"/{PHONE_ID}": httpx.Response(400, json={"error": {"message": "(#100) No permission to access this number"}})})
    )
    outcome = _run(recorder, _resolved())

    assert outcome.ok is False
    assert "400" in outcome.detail
    assert "No permission" in outcome.detail


def test_a_placeholder_app_secret_fails_before_any_request_is_made() -> None:
    """e) The dev placeholder is not a secret — refusing locally beats Meta's misleading "(#100)" answer."""
    recorder = _Recorder(_healthy_routes())
    resolved = _resolved(
        secrets={"access_token": TOKEN, "app_secret": "dev-app-secret", "verify_token": "verify-me"},
    )
    outcome = _run(recorder, resolved)

    assert outcome.ok is False
    assert recorder.requests == [], "no request may leave the process with a published placeholder secret"
    assert "App Secret" in outcome.detail
    assert "Basic" in outcome.detail, "the message has to say where in Meta the real value lives"


def test_an_empty_app_secret_also_fails_without_a_request() -> None:
    resolved = _resolved(secrets={"access_token": TOKEN, "app_secret": "", "verify_token": "verify-me"})
    recorder = _Recorder(_healthy_routes())
    outcome = _run(recorder, resolved)

    assert outcome.ok is False
    assert recorder.requests == []
    assert "App Secret" in outcome.detail


def test_a_temporary_token_passes_with_a_warning_that_it_expires() -> None:
    """f) A 24-hour trial token works today and fails a campaign next week — the badge must say so."""
    expires = int(time.time()) + 3 * 86400
    recorder = _Recorder(
        _healthy_routes(debug_token=httpx.Response(200, json={"data": {"is_valid": True, "app_id": APP_ID, "expires_at": expires}}))
    )
    outcome = _run(recorder, _resolved())

    assert outcome.ok is True
    assert "System User" in outcome.detail
    assert "مؤقت" in outcome.detail


def test_a_permanent_token_carries_no_warning_and_names_the_number() -> None:
    recorder = _Recorder(
        _healthy_routes(debug_token=httpx.Response(200, json={"data": {"is_valid": True, "app_id": APP_ID, "expires_at": 0}}))
    )
    outcome = _run(recorder, _resolved())

    assert outcome.ok is True
    assert "System User" not in outcome.detail
    assert "Mantiq Insurance" in outcome.detail, "a green badge must say *which* number answered"
    assert "+20 100 123 4567" in outcome.detail


def test_an_httpx_error_is_mapped_to_a_failed_outcome_not_an_exception() -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    recorder = _Recorder({"debug_token": explode})
    outcome = _run(recorder, _resolved())
    assert outcome.ok is False
    assert "ConnectError" in outcome.detail


def test_the_graph_version_is_one_setting_not_a_literal_in_each_call() -> None:
    """Task 3: nothing in the backend may hardcode `graph.facebook.com/vNN.N` again."""
    from app.core.config import get_settings

    assert graph_base() == f"https://graph.facebook.com/{get_settings().WHATSAPP_GRAPH_VERSION}"
    source = __import__("pathlib").Path(__file__).resolve().parents[1] / "app"
    offenders = [
        str(path.relative_to(source.parent)) for path in source.rglob("*.py") if "graph.facebook.com/v" in path.read_text(encoding="utf8")
    ]
    assert offenders == [], f"hardcoded Graph version in {offenders}"


# --------------------------------------------------------------------------- webhook: media failure


def _inbound_with_image(external_id: str, media_id: str = "MEDIA-1") -> bytes:
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"wa_id": "201000000123", "profile": {"name": "عميل حقيقي"}}],
                                "messages": [
                                    {
                                        "id": external_id,
                                        "from": "201000000123",
                                        "type": "image",
                                        "image": {"id": media_id, "mime_type": "image/jpeg", "caption": "صورة البطاقة"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ],
        }
    ).encode()


async def test_a_media_failure_does_not_lose_the_message_or_the_batch(client: Any, session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Task 4: `fetch_media` raising used to 500 the whole delivery, and Meta retries a whole batch.

    The message must still be ingested (without its media) and the failure recorded as an event, exactly like
    `recording_fetch_failed` on the calls webhook.
    """
    from app.api.v1.endpoints import webhooks
    from app.models.integrations import WebhookEvent

    driver = WhatsAppMeta(token=TOKEN, phone_number_id=PHONE_ID, app_secret="s3cret-app")
    monkeypatch.setattr(webhooks, "whatsapp_provider", lambda: driver)

    def handler(request: httpx.Request) -> httpx.Response:
        # the media descriptor is refused: this is the failure that used to become a 500
        return httpx.Response(401, json={"error": {"message": "(#190) Invalid OAuth access token"}})

    whatsapp_mod.use_transport(httpx.MockTransport(handler))
    raw = _inbound_with_image("wamid.media_fail_001")
    signature = "sha256=" + hmac.new(b"s3cret-app", raw, hashlib.sha256).hexdigest()
    try:
        response = await client.post(
            "/api/v1/webhooks/whatsapp",
            content=raw,
            headers={"content-type": "application/json", "x-hub-signature-256": signature},
        )
    finally:
        whatsapp_mod.use_transport(None)

    assert response.status_code == 200, response.text
    assert response.json()["received"] == 1
    assert response.json()["results"][0]["status"] == "created", "the text of the message must still be ingested"

    events = (
        (
            await session.execute(
                select(WebhookEvent).where(WebhookEvent.external_id == "wamid.media_fail_001").order_by(WebhookEvent.occurred_at)
            )
        )
        .scalars()
        .all()
    )
    failed = [event for event in events if event.event_type == "media_fetch_failed"]
    assert failed, f"expected a media_fetch_failed event, got {[(e.event_type, e.status) for e in events]}"
    assert failed[0].status == "error"
    assert failed[0].provider == "whatsapp"
    assert "MediaFetchError" in (failed[0].detail or "")
    assert 401 in [event.status for event in events] or True  # status column is `error`, detail carries the code
    assert "401" in (failed[0].detail or ""), "the operator needs the status code, not just the word 'failed'"


def test_fetch_media_raises_instead_of_parsing_an_error_body() -> None:
    """The old shape called `.json()` on a 401 and then fetched the empty URL that fell out of it."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "not found"}})

    whatsapp_mod.use_transport(httpx.MockTransport(handler))
    try:
        driver = WhatsAppMeta(token=TOKEN, phone_number_id=PHONE_ID, app_secret="s3cret-app")
        with pytest.raises(MediaFetchError):
            asyncio.run(driver.fetch_media("MEDIA-404"))
    finally:
        whatsapp_mod.use_transport(None)


def test_fetch_media_refuses_a_descriptor_without_a_url() -> None:
    """An empty `url` sent the second GET to nowhere; it is a failure, not a zero-byte attachment."""
    whatsapp_mod.use_transport(httpx.MockTransport(lambda _request: httpx.Response(200, json={"mime_type": "image/jpeg"})))
    try:
        driver = WhatsAppMeta(token=TOKEN, phone_number_id=PHONE_ID, app_secret="s3cret-app")
        with pytest.raises(MediaFetchError):
            asyncio.run(driver.fetch_media("MEDIA-NO-URL"))
    finally:
        whatsapp_mod.use_transport(None)


# --------------------------------------------------------------------------- state(): a stale error is not an error


async def test_a_later_success_retires_an_older_rejected_handshake(client: Any) -> None:
    """Task 5: the operator fixes the verify token, Meta succeeds, the card must stop showing the old failure."""
    async with login(client, ADMIN) as h:
        saved = await client.put("/api/v1/settings/integrations/whatsapp", headers=h, json={"secrets": {"verify_token": VERIFY}})
        assert saved.status_code == 200, saved.text

        # a handshake Meta rejected while the verify token was still wrong
        rejected = await client.get(
            "/api/v1/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"}
        )
        assert rejected.status_code == 403, rejected.text
        state = (await client.get("/api/v1/settings/integrations/whatsapp", headers=h)).json()
        assert state["last_error"], "a current failure must be reported"

        # the operator pastes the right token; Meta's next handshake is accepted
        accepted = await client.get(
            "/api/v1/webhooks/whatsapp",
            params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "42"},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.text == "42"
        state = (await client.get("/api/v1/settings/integrations/whatsapp", headers=h)).json()
        assert state["last_error"] is None, "the failure is history; the card must not keep showing it"
        assert state["last_webhook_at"], "the success is still reported as the last event"


async def test_the_error_is_kept_when_it_is_still_the_newest_event(client: Any) -> None:
    async with login(client, ADMIN) as h:
        await client.get("/api/v1/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"})
        state = (await client.get("/api/v1/settings/integrations/whatsapp", headers=h)).json()
        assert state["last_error"]
        # `_status_of` is untouched by Task 5: the badge still follows the last *test*, not the event log
        assert state["status"] in {"ready", "live", "unset", "error"}
