"""R9 — production is live only, and calls are **mobile phone calls** (no simulator anywhere in prod).

The four tests named in the round's acceptance list are here, plus the boot-gate proof that makes them
mean something:

* `test_production_does_not_boot_sim_drivers` — `ENV=prod` + real credentials builds `WhatsAppMeta` and
  `MobileCallsProvider`, and the simulated classes refuse to be constructed at all.
* `test_production_boot_without_secrets_fails_closed` — no token / no webhook secret ⇒ `RuntimeError`
  from the boot gate and `503 integration_not_configured` from the factory; never a silent simulator.
* `test_whatsapp_live_rejects_empty_secret` — HMAC with the empty key is computable by anybody, so an
  unset App Secret refuses the delivery (and production blanks the published dev placeholder).
* `test_mobile_call_webhook_opens_request_by_mobile` — a signed Twilio-shaped call opens a `call`-channel
  request whose `contact_mobile` is the caller's mobile number.
* `test_dev_sim_routes_hidden_in_production` — the simulator router is not mounted in a production app.

These tests talk to the same objects the process uses; none of them swaps a live driver for a fake.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import pytest
from app.core.config import get_settings
from app.core.errors import IntegrationNotConfigured
from app.core.security import verify_webhook_signature
from app.integrations import registry
from app.integrations.mobile_calls import MobileCallsProvider
from app.integrations.sims import MobileCallsSim, SimulationDisabled, WhatsAppSim
from app.integrations.whatsapp import WhatsAppMeta

LIVE_TOKEN = "EAAG-r9-production-token"
LIVE_PHONE_ID = "109999888777"
LIVE_APP_SECRET = "r9-app-secret"
LIVE_CALLS_SECRET = "r9-mobile-calls-secret"


@contextmanager
def _env(**overrides: str) -> Iterator[None]:
    """Set env vars for the duration of the block and drop pydantic's cached `Settings` (and the registry's).

    `get_settings` is `lru_cache`d and every module calls it lazily, so clearing the cache is what makes a
    second "process mode" observable inside one pytest session.
    """
    import os

    import app.core.config as config_module

    saved = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    config_module.get_settings.cache_clear()
    registry.reset_providers()
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        config_module.get_settings.cache_clear()
        registry.reset_providers()


def _production(**extra: str) -> Any:
    """A production environment with real-looking credentials unless the caller removes one."""
    values = {
        "ENV": "prod",
        "INTEGRATIONS_DRIVER": "live",
        "JWT_SECRET": "r9-production-jwt-secret-that-is-long-enough",
        "SECRETS_ENCRYPTION_KEY": "r9-production-secrets-key-that-is-long-enough",
        "WHATSAPP_ACCESS_TOKEN": LIVE_TOKEN,
        "WHATSAPP_PHONE_NUMBER_ID": LIVE_PHONE_ID,
        "WHATSAPP_APP_SECRET": LIVE_APP_SECRET,
        "WHATSAPP_VERIFY_TOKEN": "r9-verify-token",
        "TELEPHONY_PROVIDER": "twilio",
        "TELEPHONY_WEBHOOK_SECRET": LIVE_CALLS_SECRET,
        "TELEPHONY_WEBHOOK_URL": "https://voice.example.com",
    }
    values.update(extra)
    return _env(**values)


def test_production_settings_force_the_live_driver() -> None:
    with _production():
        settings = get_settings()
        assert settings.is_production is True
        assert settings.simulation_allowed is False
        # `ENV=production` is the same mode as `prod`, and both force `live`
        assert settings.INTEGRATIONS_DRIVER == "live"
    with _env(ENV="production", INTEGRATIONS_DRIVER="live", JWT_SECRET="r9-production-jwt-secret-that-is-long-enough"):
        settings = get_settings()
        assert settings.ENV == "prod"
        assert settings.is_production is True
        assert settings.INTEGRATIONS_DRIVER == "live"


def test_an_explicit_simulation_driver_is_refused_in_production() -> None:
    from app.core.config import Settings
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(ENV="prod", INTEGRATIONS_DRIVER="sim", JWT_SECRET="r9-production-jwt-secret-that-is-long-enough")


def test_production_does_not_boot_sim_drivers() -> None:
    """The proof the round asks for: production builds the live drivers, and `*Sim` cannot even exist."""
    with _production():
        whatsapp = registry.whatsapp_provider()
        calls = registry.mobile_calls_provider()

        assert isinstance(whatsapp, WhatsAppMeta), f"production must use the Meta live driver, got {type(whatsapp).__name__}"
        assert not isinstance(whatsapp, WhatsAppSim)
        assert isinstance(calls, MobileCallsProvider), f"production must use the live mobile-calls driver, got {type(calls).__name__}"
        assert not isinstance(calls, MobileCallsSim)

        # two independent gates: no factory path, and no constructor either
        for simulator in (WhatsAppSim, MobileCallsSim):
            with pytest.raises(SimulationDisabled):
                simulator()

        report = registry.readiness()
        assert report["ready"] is True, report
        registry.assert_production_ready()  # the boot gate accepts a fully configured production process


def test_production_boot_without_secrets_fails_closed() -> None:
    """No token / no webhook secret ⇒ named failure. R8 would have answered with a simulator and a 200."""
    with _production(WHATSAPP_ACCESS_TOKEN="", TELEPHONY_WEBHOOK_SECRET=""):
        with pytest.raises(IntegrationNotConfigured) as whatsapp_error:
            registry.whatsapp_provider()
        assert "WHATSAPP_ACCESS_TOKEN" in whatsapp_error.value.message

        with pytest.raises(IntegrationNotConfigured) as calls_error:
            registry.mobile_calls_provider()
        assert "TELEPHONY_WEBHOOK_SECRET" in calls_error.value.message

        # …and the boot refuses, with a message that lists both channels instead of starting on simulation
        with pytest.raises(RuntimeError) as boot:
            registry.assert_production_ready()
        message = str(boot.value)
        assert "ENV=prod" in message
        assert "whatsapp" in message and "mobile_calls" in message
        assert "WHATSAPP_ACCESS_TOKEN" in message and "TELEPHONY_WEBHOOK_SECRET" in message


def test_whatsapp_live_needs_the_app_secret_too() -> None:
    """A live WhatsApp row without the App Secret is a *deaf* channel — every delivery would be a 403."""
    with _production(WHATSAPP_APP_SECRET=""):
        with pytest.raises(IntegrationNotConfigured) as refused:
            registry.whatsapp_provider()
        assert "WHATSAPP_APP_SECRET" in refused.value.message
        with pytest.raises(RuntimeError):
            registry.assert_production_ready()


def test_production_blanks_the_published_dev_placeholders() -> None:
    """`dev-app-secret` is in this repository: a production process must not verify with it."""
    with _env(
        ENV="prod",
        INTEGRATIONS_DRIVER="live",
        JWT_SECRET="r9-production-jwt-secret-that-is-long-enough",
        # the development defaults, exactly as they arrive from a checked-in `.env`
        WHATSAPP_APP_SECRET="dev-app-secret",
        WHATSAPP_VERIFY_TOKEN="dev-verify-token",
        TELEPHONY_WEBHOOK_SECRET="dev-telephony-secret",
    ):
        settings = get_settings()
        assert settings.WHATSAPP_APP_SECRET == ""
        assert settings.WHATSAPP_VERIFY_TOKEN == ""
        assert settings.TELEPHONY_WEBHOOK_SECRET == ""
        with pytest.raises(IntegrationNotConfigured):
            registry.mobile_calls_provider()


def test_whatsapp_live_rejects_empty_secret() -> None:
    """An empty App Secret must refuse the webhook rather than sign with the empty key."""
    raw = b'{"entry": []}'
    empty_key_signature = "sha256=" + hmac.new(b"", raw, hashlib.sha256).hexdigest()

    assert verify_webhook_signature(raw, empty_key_signature, "") is False
    with _env(WHATSAPP_APP_SECRET="", WHATSAPP_ACCESS_TOKEN=LIVE_TOKEN, WHATSAPP_PHONE_NUMBER_ID=LIVE_PHONE_ID):
        driver = WhatsAppMeta(token=LIVE_TOKEN, phone_number_id=LIVE_PHONE_ID, app_secret="")
        assert driver.app_secret == ""
        assert driver.verify_signature(raw, empty_key_signature) is False
        assert driver.verify_signature(raw, None) is False


def _signed(raw: bytes, secret: str = LIVE_CALLS_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def test_production_ignores_a_saved_row_that_says_not_live() -> None:
    """A `is_live=false` row (or a snapshot primed before the row existed) cannot select a simulator.

    The row used to be the final word on the mode. In production the *process* is the final word: the
    settings screen can save whatever it likes, and `registry.resolved()` still answers "live" — which is
    what makes the boot gate and the "no simulator" rule hold across a stale cache.
    """
    from app.services.integrations_settings import ResolvedIntegration

    with _production():
        # simulate exactly the stale/dev snapshot: provider rows, but marked not live
        registry._primed = {
            "whatsapp": ResolvedIntegration(provider="whatsapp", is_live=False, config={}, secrets={}),
            "telephony": ResolvedIntegration(provider="telephony", is_live=False, config={}, secrets={}),
        }
        try:
            assert registry.resolved("whatsapp").is_live is True
            assert registry.live_mode("telephony") is True
            # the row says "not live", yet the process still builds the live driver (credentials come from
            # the environment, which is what a production deployment uses) — never a simulator
            assert isinstance(registry.whatsapp_provider(), WhatsAppMeta)
        finally:
            registry._primed = None
            registry.reset_providers()


async def test_mobile_call_webhook_opens_request_by_mobile(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A Twilio-shaped mobile call arrives signed, and becomes a `call` request by the caller's mobile."""
    from app.api.v1.endpoints import webhooks
    from app.core.db import get_sessionmaker
    from app.models.requests import CallRecord, ServiceRequest
    from sqlalchemy import select

    driver = MobileCallsProvider(webhook_secret=LIVE_CALLS_SECRET, provider_url="https://voice.example.com", provider_name="twilio")
    monkeypatch.setattr(webhooks, "mobile_calls_provider", lambda: driver)

    mobile = "+201277700011"
    started = {"CallSid": "CA-r9-mobile-1", "CallStatus": "ringing", "From": mobile, "To": "+20220000000"}
    raw = json.dumps(started).encode()

    forged = await client.post(
        "/api/v1/webhooks/mobile-calls",
        content=raw,
        headers={"content-type": "application/json", "x-call-signature": _signed(raw, "wrong-secret")},
    )
    assert forged.status_code == 403

    accepted = await client.post(
        "/api/v1/webhooks/mobile-calls",
        content=raw,
        headers={"content-type": "application/json", "x-call-signature": _signed(raw)},
    )
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["status"] == "started", body
    assert body["request_id"], "the call must open (or link) a request"

    async with get_sessionmaker()() as session:
        opened = (await session.execute(select(ServiceRequest).where(ServiceRequest.id == UUID(body["request_id"])))).scalar_one()
        call = (await session.execute(select(CallRecord).where(CallRecord.provider_call_id == "CA-r9-mobile-1"))).scalar_one()

    assert opened.channel.value == "call"
    assert opened.contact_mobile == mobile, "the request is keyed by the caller's mobile number"
    assert call.provider == "twilio", "the call row names the mobile-calls provider, never a hardcoded `sim`"
    assert call.from_number == mobile
    assert call.request_id == opened.id

    # idempotency: the same CallSid again changes nothing
    replay = await client.post(
        "/api/v1/webhooks/mobile-calls",
        content=raw,
        headers={"content-type": "application/json", "x-call-signature": _signed(raw)},
    )
    assert replay.json()["status"] == "duplicate"

    # the pre-R9 path still answers, so a provider console pasted months ago keeps delivering
    legacy = await client.post(
        "/api/v1/webhooks/telephony",
        content=raw,
        headers={"content-type": "application/json", "x-call-signature": _signed(raw)},
    )
    assert legacy.status_code == 200, legacy.text


async def test_dev_sim_routes_hidden_in_production() -> None:
    """`/api/v1/dev/sim*` is not mounted in a production app — a 404 from the router, not a handler that
    remembers to check the driver."""
    import httpx

    with _production():
        from app.main import create_app

        app = create_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://prod") as prod_client:
            panel = await prod_client.get("/api/v1/dev/sim")
            send = await prod_client.post("/api/v1/dev/sim/whatsapp", json={"mobile": "01000000000"})
            call = await prod_client.post("/api/v1/dev/sim/call", json={"from_number": "01000000000"})
        for response, label in ((panel, "panel"), (send, "whatsapp"), (call, "call")):
            assert response.status_code == 404, f"{label} simulator route answered {response.status_code} in production"


async def test_dev_sim_routes_exist_in_a_dev_process() -> None:
    """The gate is a mode, not a deletion: dev/test still gets the simulator panel the E2E suite drives."""
    import httpx

    with _env(ENV="dev", INTEGRATIONS_DRIVER="sim"):
        from app.main import create_app

        app = create_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://dev") as dev_client:
            panel = await dev_client.get("/api/v1/dev/sim")
        assert panel.status_code == 200
        assert "sim" in panel.text.lower()


async def test_production_refuses_to_switch_a_channel_back_to_simulation(session: Any) -> None:
    """There is no "off" mode in production, so the live switch cannot pretend there is one."""
    from app.core.errors import Conflict
    from app.models.integrations import IntegrationConfig
    from app.services import integrations_settings as settings_service

    with _production():
        row = IntegrationConfig(provider="whatsapp", config={}, is_live=True, last_test_status="ok")
        with pytest.raises(Conflict) as refused:
            settings_service._assert_switch(row, "whatsapp", to_live=False, confirm=False)
        assert "ENV=prod" in refused.value.message


def _unused(_: AsyncIterator[Any]) -> None:  # keeps the typing import honest across pytest versions
    return None
