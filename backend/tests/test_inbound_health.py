"""`inbound_health` and the non-fatal WhatsApp findings (item 3).

«متصل» used to mean one thing only: the credentials we hold work when *we* call out. It said nothing about
whether Meta calls *us*, so a channel with a wrong Callback URL showed a green badge and an empty inbox — two
different problems (token vs. subscription) that looked identical from the screen.

`inbound_health` answers the second question from the event log the system already keeps; the two warnings
answer it before the first message is lost.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from app.integrations import whatsapp as whatsapp_mod
from app.integrations.whatsapp import graph_base
from app.models.integrations import IntegrationConfig, WebhookEvent
from app.services import integrations_settings as cfg
from sqlalchemy import delete, select

ADMIN = "admin@mantiq.test"
TOKEN = "EAAG-token-for-inbound-health-tests"
PHONE_ID = "1031129959988895"
APP_ID = "1031129959988895"
APP_SECRET = "0f9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c"
WABA = "5566778899001122"


def _resolved(business_account_id: str = WABA) -> cfg.ResolvedIntegration:
    return cfg.ResolvedIntegration(
        provider="whatsapp",
        is_live=True,
        config={"phone_number_id": PHONE_ID, "business_account_id": business_account_id, "app_id": APP_ID, "receive_media": True},
        secrets={"access_token": TOKEN, "app_secret": APP_SECRET, "verify_token": "verify-me"},
    )


def _routes(*, expires_at: int = 0, subscribed: list[dict[str, Any]] | None = None, waba_status: int = 200) -> dict[str, Any]:
    return {
        "debug_token": httpx.Response(200, json={"data": {"is_valid": True, "app_id": APP_ID, "expires_at": expires_at}}),
        "subscribed_apps": httpx.Response(waba_status, json={"data": subscribed if subscribed is not None else []}),
        f"/{PHONE_ID}": httpx.Response(200, json={"display_phone_number": "+20 100 123 4567", "verified_name": "Mantiq"}),
    }


def _run(routes: dict[str, Any], resolved: cfg.ResolvedIntegration | None = None) -> tuple[cfg.TestOutcome, list[str]]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        for fragment, response in routes.items():
            if fragment in str(request.url):
                return response
        return httpx.Response(404, json={"error": {"message": "no route"}})

    whatsapp_mod.use_transport(httpx.MockTransport(handler))
    try:
        return asyncio.run(cfg._test_whatsapp(resolved or _resolved())), seen
    finally:
        whatsapp_mod.use_transport(None)


# ------------------------------------------------------------------ the extra subscription probe


def test_an_app_that_is_not_subscribed_is_reported_as_a_warning() -> None:
    outcome, seen = _run(_routes(subscribed=[]))
    assert outcome.ok is True, "the credentials are fine — a silent inbox is a warning, not a failed test"
    assert any("غير مشترك" in w or "لا توجد تطبيقات" in w for w in outcome.warnings), outcome.warnings
    assert any("subscribed_apps" in url for url in seen)


def test_a_subscribed_app_produces_no_subscription_warning() -> None:
    outcome, _ = _run(_routes(subscribed=[{"whatsapp_business_api_data": {"id": APP_ID, "name": "Mantiq"}}]))
    assert outcome.ok is True
    assert not any("WABA" in w for w in outcome.warnings), outcome.warnings


def test_a_failing_subscription_probe_is_a_warning_not_a_failure() -> None:
    """An optional probe must not turn a working channel red — Meta may refuse `subscribed_apps` alone."""
    outcome, _ = _run(_routes(waba_status=403))
    assert outcome.ok is True
    assert any("403" in w for w in outcome.warnings), outcome.warnings


def test_a_missing_waba_id_is_named_instead_of_probed() -> None:
    outcome, seen = _run(_routes(), _resolved(business_account_id=""))
    assert outcome.ok is True
    assert any("WABA ID" in w for w in outcome.warnings), outcome.warnings
    assert not any("subscribed_apps" in url for url in seen)


# ------------------------------------------------------------------ temporary tokens


def test_a_token_expiring_within_48_hours_is_an_explicit_warning() -> None:
    expires = int(time.time()) + 20 * 3600
    outcome, _ = _run(_routes(expires_at=expires, subscribed=[{"id": APP_ID}]))
    assert outcome.ok is True
    assert any(" ساعت" in w or "ساعة" in w for w in outcome.warnings), outcome.warnings
    assert any("System User" in w or "دائم" in w for w in outcome.warnings), outcome.warnings
    assert "System User" in outcome.detail or "مؤقت" in outcome.detail


def test_a_permanent_token_carries_no_warning() -> None:
    outcome, _ = _run(_routes(expires_at=0, subscribed=[{"id": APP_ID}]))
    assert outcome.ok is True
    assert outcome.warnings == (), outcome.warnings


# ------------------------------------------------------------------ inbound_health


async def _event(session: Any, *, status: str, age: timedelta, detail: str = "x") -> None:
    session.add(
        WebhookEvent(
            provider="whatsapp",
            event_type="verification" if status != "ok" else "message",
            status=status,
            detail=detail,
            occurred_at=datetime.now(UTC) - age,
        )
    )
    await session.commit()


@pytest.fixture(autouse=True)
async def _clean_events(session: Any) -> Any:
    """Undo everything this module writes: the WhatsApp row, its events, and the two process-level caches.

    The suite shares one process and one database. A saved live WhatsApp config is exactly what
    `tests/test_intake.py` must *not* find behind it (it 403s the handshake and fails for reasons that look
    like a product bug), so the row is removed rather than left behind in a "passing" state.
    """
    from app.core.rate_limit import test_limiter
    from app.integrations import registry

    async def _wipe() -> None:
        await session.execute(delete(WebhookEvent).where(WebhookEvent.provider == "whatsapp"))
        await session.execute(delete(IntegrationConfig).where(IntegrationConfig.provider == "whatsapp"))
        await session.commit()

    await _wipe()
    test_limiter.reset()
    registry.reset_providers()
    yield
    await _wipe()
    registry._primed = None
    registry.reset_providers()
    test_limiter.reset()


async def test_never_when_nothing_ever_arrived(session: Any) -> None:
    state = await cfg.state(session, "whatsapp")
    assert state.inbound_health == "never"
    assert state.as_dict()["inbound_health"] == "never"


async def test_receiving_when_something_arrived_today(session: Any) -> None:
    await _event(session, status="ok", age=timedelta(hours=2))
    assert (await cfg.state(session, "whatsapp")).inbound_health == "receiving"


async def test_stale_after_a_day_of_silence(session: Any) -> None:
    await _event(session, status="ok", age=timedelta(hours=30))
    assert (await cfg.state(session, "whatsapp")).inbound_health == "stale"


async def test_failing_when_the_newest_event_is_an_error(session: Any) -> None:
    await _event(session, status="ok", age=timedelta(hours=5))
    await _event(session, status="rejected", age=timedelta(hours=1), detail="hub.verify_token לא תואם")
    state = await cfg.state(session, "whatsapp")
    assert state.inbound_health == "failing"
    assert state.last_error, "a current failure is still reported"


async def test_a_later_success_turns_health_back_to_receiving(session: Any) -> None:
    await _event(session, status="rejected", age=timedelta(hours=3), detail="old failure")
    await _event(session, status="ok", age=timedelta(minutes=5))
    state = await cfg.state(session, "whatsapp")
    assert state.inbound_health == "receiving"
    assert state.last_error is None


async def test_the_connection_test_persists_its_warnings_for_the_next_page_load(client: Any, session: Any) -> None:
    """A warning shown once and lost on refresh is a warning nobody acts on."""
    from tests.helpers import login

    routes = _routes(subscribed=[{"id": APP_ID}], expires_at=int(time.time()) + 30 * 3600)
    whatsapp_mod.use_transport(
        httpx.MockTransport(lambda request: next((r for f, r in routes.items() if f in str(request.url)), httpx.Response(404)))
    )
    try:
        async with login(client, ADMIN) as h:
            saved = await client.put(
                "/api/v1/settings/integrations/whatsapp",
                headers=h,
                json={
                    "config": {"phone_number_id": PHONE_ID, "business_account_id": WABA, "app_id": APP_ID},
                    "secrets": {"access_token": TOKEN, "app_secret": APP_SECRET},
                },
            )
            assert saved.status_code == 200, saved.text
            # the connection test only leaves the process when the provider is live (a simulated provider
            # answers "no network was used"); flip the row directly — the switch's own gate is tested elsewhere
            row = (await session.execute(select(IntegrationConfig).where(IntegrationConfig.provider == "whatsapp"))).scalar_one()
            row.is_live = True
            await session.commit()
            tested = await client.post("/api/v1/settings/integrations/whatsapp/test", headers=h)
            assert tested.status_code == 200, tested.text
            payload = tested.json()
            assert payload["warnings"], "a temporary token must come back as a warning"
            assert "System User" in " ".join(payload["warnings"]) or "دائم" in " ".join(payload["warnings"])

            # a fresh read (the page reload) must still carry it
            state = (await client.get("/api/v1/settings/integrations/whatsapp", headers=h)).json()
            assert state["last_test_warnings"] == payload["warnings"]
            assert state["inbound_health"] in {"never", "receiving", "stale", "failing"}
            # and the stored marker must not leak into the editable form
            assert "_test_warnings" not in state["config"]
    finally:
        whatsapp_mod.use_transport(None)


def test_the_graph_version_is_still_one_setting() -> None:
    assert graph_base().endswith(cfg.get_settings().WHATSAPP_GRAPH_VERSION)
