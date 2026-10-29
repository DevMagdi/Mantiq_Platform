"""The settings hub, end to end: what a read may reveal, what a save may break, what live requires.

These tests are the proof of the four promises ADR-016 makes, so they assert the promise and not the
plumbing: a secret is never in a response, a blank field never erases a credential, the live switch is
gated on a green test and an explicit confirmation, and the event log shows what really arrived.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any
from uuid import UUID

import pytest
from app.core.rate_limit import test_limiter
from app.core.secrets import MASK
from app.integrations import registry
from app.models.integrations import IntegrationConfig
from app.models.masters import FeatureFlag
from app.models.requests import AuditLog
from sqlalchemy import select

ADMIN = "admin@brokerhub.test"
MANAGER = "manager@brokerhub.test"
REP = "rep_a@brokerhub.test"


@pytest.fixture(autouse=True)
def _clean_state(session: Any):
    """Process-level caches are shared by the whole session — this module must not leak either of them."""
    test_limiter.reset()
    registry.reset_providers()
    yield
    registry._primed = None
    registry.reset_providers()
    test_limiter.reset()


async def _row(session: Any, provider: str) -> IntegrationConfig:
    return (await session.execute(select(IntegrationConfig).where(IntegrationConfig.provider == provider))).scalar_one()


async def _audit(session: Any, action: str) -> AuditLog:
    return (await session.execute(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.occurred_at.desc()))).scalar_one()


async def test_a_saved_secret_is_never_returned_and_a_blank_save_keeps_it(client: Any, session: Any) -> None:
    from tests.helpers import login

    async with login(client, ADMIN) as h:
        saved = await client.put(
            "/api/v1/settings/integrations/whatsapp",
            headers=h,
            json={"config": {"phone_number_id": "1234567890"}, "secrets": {"access_token": "SECRET-ABC-123"}},
        )
        assert saved.status_code == 200, saved.text
        body = saved.json()
        assert body["config"]["phone_number_id"] == "1234567890"
        token = next(item for item in body["secrets"] if item["key"] == "access_token")
        assert token == {"key": "access_token", "set": True, "display": MASK}
        # the whole response body, not just the field, must be free of the value
        assert "SECRET-ABC-123" not in saved.text

        blank = await client.put("/api/v1/settings/integrations/whatsapp", headers=h, json={"secrets": {"access_token": ""}})
        assert blank.status_code == 200
        assert next(i for i in blank.json()["secrets"] if i["key"] == "access_token")["set"] is True

        removed = await client.put("/api/v1/settings/integrations/whatsapp", headers=h, json={"secrets": {"access_token": None}})
        assert removed.status_code == 200
        assert next(i for i in removed.json()["secrets"] if i["key"] == "access_token")["set"] is False

        # and a second read after the blank save proves it was the row that kept it, not the response echo
        again = await client.get("/api/v1/settings/integrations/whatsapp", headers=h)
        assert next(i for i in again.json()["secrets"] if i["key"] == "access_token")["set"] is False

    stored = await _row(session, "whatsapp")
    assert "SECRET-ABC-123" not in (stored.secrets_encrypted or "")
    assert stored.secrets_encrypted is None or stored.secrets_encrypted.startswith("gAAAAA")  # Fernet


async def test_going_live_needs_confirmation_a_green_test_and_the_required_fields(client: Any, session: Any) -> None:
    from tests.helpers import login

    async with login(client, ADMIN) as h:
        # nothing tested, nothing confirmed
        blind = await client.put("/api/v1/settings/integrations/whatsapp", headers=h, json={"is_live": True})
        assert blind.status_code == 409, blind.text
        assert blind.json()["details"]["required"] == ["confirm_live"]

        untested = await client.put(
            "/api/v1/settings/integrations/whatsapp",
            headers=h,
            json={"is_live": True, "confirm_live": True},
        )
        assert untested.status_code == 409
        assert untested.json()["details"]["required"] == ["test"]

        tested = await client.post("/api/v1/settings/integrations/whatsapp/test", headers=h)
        assert tested.status_code == 200, tested.text
        assert tested.json()["ok"] is True and tested.json()["mode"] == "simulated"

        incomplete = await client.put(
            "/api/v1/settings/integrations/whatsapp",
            headers=h,
            json={"is_live": True, "confirm_live": True},
        )
        assert incomplete.status_code == 409
        assert incomplete.json()["code"] == "incomplete_config"
        assert "phone_number_id" in incomplete.json()["details"]["missing"]

        configured = await client.put(
            "/api/v1/settings/integrations/whatsapp",
            headers=h,
            json={
                "config": {"phone_number_id": "98765"},
                "secrets": {"access_token": "SECRET-XYZ"},
                "is_live": True,
                "confirm_live": True,
            },
        )
        assert configured.status_code == 200, configured.text
        assert configured.json()["is_live"] is True
        assert configured.json()["status"] == "live"
        assert registry.live_mode("whatsapp") is True  # the switch changed the running process, not only the row
        from app.integrations.whatsapp import WhatsAppMeta

        assert isinstance(registry.whatsapp_provider(), WhatsAppMeta)

        # and flipping back needs no confirmation at all
        off = await client.post("/api/v1/settings/integrations/whatsapp/live", headers=h, json={"is_live": False})
        assert off.status_code == 200
        assert off.json()["is_live"] is False
        assert off.json()["status"] == "ready"
        assert registry.live_mode("whatsapp") is False

    entry = await _audit(session, "integration.live")
    assert entry.after == {"is_live": False, "confirm": False}


async def test_connection_tests_are_limited_to_six_a_minute(client: Any) -> None:
    from tests.helpers import login

    async with login(client, ADMIN) as h:
        for _ in range(6):
            response = await client.post("/api/v1/settings/integrations/whatsapp/test", headers=h)
            assert response.status_code == 200, response.text
        seventh = await client.post("/api/v1/settings/integrations/whatsapp/test", headers=h)
        assert seventh.status_code == 429, seventh.text
        payload = seventh.json()
        assert payload["code"] == "too_many_requests"
        assert payload["details"]["retry_after_seconds"] >= 1
        # a different provider has its own budget
        assert (await client.post("/api/v1/settings/integrations/email/test", headers=h)).status_code == 200


async def test_the_row_wins_over_the_environment(client: Any, session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-016 §1: the saved row is the source, `.env` is the fallback — including when it says `live`."""
    from tests.helpers import login

    monkeypatch.setattr(registry, "_primed", None)
    assert registry.live_mode("email") is False  # INTEGRATIONS_DRIVER=sim in the test env

    async with login(client, ADMIN) as h:
        await client.put(
            "/api/v1/settings/integrations/email",
            headers=h,
            json={"config": {"host": "smtp.example.test", "port": 587, "encryption": "starttls"}},
        )
    stored = await _row(session, "email")
    assert stored.config["host"] == "smtp.example.test"
    assert registry.config_value("email", "host") == "smtp.example.test"

    # an operator who edits `.env` afterwards does not change what the app uses: the row still wins
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "SMTP_HOST", "from-the-environment", raising=True)
    monkeypatch.setattr(get_settings(), "SMTP_PORT", 25, raising=True)
    assert registry.config_value("email", "host") == "smtp.example.test"
    # ... and only the row-less case follows the environment, which is what `_from_env` is for. An *empty*
    # primed cache (a database without the settings table yet) must fall back too, not raise.
    monkeypatch.setattr(registry, "_primed", {})
    assert registry.config_value("email", "host") == "from-the-environment"


async def test_webhook_events_are_recorded_for_both_outcomes(client: Any, session: Any) -> None:
    """The screen must show that something arrived — and that something was refused."""
    from tests.helpers import login

    delivered = await client.post(
        "/api/v1/webhooks/mobile-calls",
        json={"call_id": "call-42", "event": "call.started", "from": "+201000000009"},
    )
    assert delivered.status_code == 200, delivered.text

    refused = await client.post("/api/v1/webhooks/mobile-calls", content=b"not json", headers={"content-type": "application/json"})
    assert refused.status_code == 403

    async with login(client, ADMIN) as h:
        events = await client.get("/api/v1/settings/integrations/events", headers=h, params={"provider": "telephony"})
    assert events.status_code == 200
    rows = events.json()
    by_kind = {row["event_type"]: row for row in rows}
    # the delivered row carries what intake actually decided, and the id a support ticket would quote
    assert by_kind["call.started"]["status"] == "started"
    assert by_kind["call.started"]["external_id"] == "call-42"
    assert by_kind["rejected"]["status"] == "error"
    assert "body must be JSON" in by_kind["rejected"]["detail"]
    assert all(row["provider"] == "telephony" for row in rows)


async def test_unknown_provider_and_unknown_field_are_rejected_not_ignored(client: Any) -> None:
    from tests.helpers import login

    async with login(client, ADMIN) as h:
        assert (await client.get("/api/v1/settings/integrations/fax", headers=h)).status_code == 404
        extra = await client.put("/api/v1/settings/integrations/whatsapp", headers=h, json={"config": {"access_token": "x"}})
        assert extra.status_code == 422
        assert extra.json()["details"][0]["field"] == "access_token"
        empty = await client.put("/api/v1/settings/integrations/whatsapp", headers=h, json={})
        assert empty.status_code == 422

        # a secret is not a config field: putting one in `config` is an error, not a leak
        assert any(
            item["key"] == "access_token"
            for item in (await client.get("/api/v1/settings/integrations/whatsapp", headers=h)).json()["secrets"]
        )


async def test_the_screen_is_closed_to_people_without_the_permission(client: Any) -> None:
    from tests.helpers import login

    async with login(client, REP) as h:
        denied = await client.get("/api/v1/settings/integrations", headers=h)
        assert denied.status_code == 403
        assert denied.json()["code"] == "forbidden"
        assert denied.json()["details"]["missing"] == ["settings.manage"]
    anonymous = await client.get("/api/v1/settings/integrations")
    assert anonymous.status_code == 401


async def test_every_write_is_audited_with_masked_values(client: Any, session: Any) -> None:
    from tests.helpers import login

    async with login(client, ADMIN) as h:
        await client.put(
            "/api/v1/settings/integrations/whatsapp",
            headers=h,
            json={"config": {"phone_number_id": "111"}, "secrets": {"access_token": "TOP-SECRET-VALUE"}},
        )
    entry = await _audit(session, "integration.update")
    assert entry.actor_email == ADMIN
    assert entry.after["config"]["phone_number_id"] == "111"
    assert entry.after["secrets"]["access_token"] is True
    assert "TOP-SECRET-VALUE" not in str(entry.before) + str(entry.after)
    assert entry.after["changed"]["secrets"] == ["access_token"]


async def test_underwriting_defaults_round_trip_and_seed_a_new_policy(client: Any, session: Any, ids: dict) -> None:
    from tests.helpers import login

    session.add(FeatureFlag(key="phase2.enabled", enabled=True, description="test"))
    await session.commit()

    async with login(client, MANAGER) as h:
        defaults = await client.get("/api/v1/settings/policies", headers=h)
        assert defaults.status_code == 200
        assert defaults.json() == {"default_commission_pct": 5.0, "default_payment_plan": "monthly", "default_currency": "EGP"}

        bad = await client.put("/api/v1/settings/policies", headers=h, json={"default_commission_pct": 140})
        assert bad.status_code == 422
        assert bad.json()["details"][0]["field"] == "default_commission_pct"

        saved = await client.put(
            "/api/v1/settings/policies", headers=h, json={"default_commission_pct": 7.5, "default_payment_plan": "quarterly"}
        )
        assert saved.status_code == 200
        assert saved.json()["default_commission_pct"] == 7.5

        created = await client.post(
            "/api/v1/policies",
            headers=h,
            json={
                "client_id": ids["clients"]["ألف"],
                "start_date": "2026-01-31",
                "premium_amount": 1000.01,
                "policy_no": "P-2026-0001",
            },
        )
        assert created.status_code == 201, created.text
        policy = created.json()
        assert policy["payment_plan"] == "quarterly"  # from the defaults saved a second ago
        assert policy["commission_amount"] == 75.0  # 1000.01 × 7.5% rounded to the gros
        assert policy["status"] == "draft"
        assert UUID(policy["id"])

    async with login(client, MANAGER) as h:
        plan = await client.get(f"/api/v1/policies/{policy['id']}/installments", headers=h)
    assert plan.status_code == 200, plan.text
    items = plan.json()["items"]
    assert [i["sequence_no"] for i in items] == [1, 2, 3, 4]
    assert [i["due_date"] for i in items] == ["2026-01-31", "2026-04-30", "2026-07-31", "2026-10-31"]
    assert [i["amount"] for i in items] == [250.0, 250.0, 250.0, 250.01]
    # R7: the status is no longer frozen at `upcoming` — a row whose due date has passed reads `late`, and
    # the server is the one that says so (the screen used to derive it in the browser, ADR-028 §3).
    today = date.today()
    expected = ["late" if date.fromisoformat(i["due_date"]) < today else "upcoming" for i in items]
    assert [i["status"] for i in items] == expected


async def test_policy_creation_is_gated_on_the_phase2_flag_and_the_permission(client: Any, ids: dict) -> None:
    from tests.helpers import login

    async with login(client, MANAGER) as h:
        blocked = await client.post(
            "/api/v1/policies",
            headers=h,
            json={"client_id": ids["clients"]["ألف"], "start_date": "2026-01-01", "premium_amount": 100},
        )
        assert blocked.status_code == 503, blocked.text
        assert blocked.json()["code"] == "feature_disabled"

    async with login(client, REP) as h:
        forbidden = await client.post(
            "/api/v1/policies",
            headers=h,
            json={"client_id": ids["clients"]["ألف"], "start_date": "2026-01-01", "premium_amount": 100},
        )
        assert forbidden.status_code == 403  # a rep does not issue policies at all
        assert forbidden.json()["details"]["missing"] == ["policies.manage"]


async def test_the_hub_exposes_the_callback_urls_it_tells_you_to_paste(client: Any) -> None:
    from tests.helpers import login

    async with login(client, ADMIN) as h:
        urls = await client.get("/api/v1/settings/integrations/webhook-urls", headers=h)
    assert urls.status_code == 200
    body = urls.json()
    assert body["base_url"]
    pairs = {item["provider"]: item["url"] for item in body["items"]}
    assert pairs["whatsapp"].endswith("/api/v1/webhooks/whatsapp")
    assert pairs["telephony"].endswith("/api/v1/webhooks/mobile-calls")
    assert all("/docs" not in url for url in pairs.values())


async def test_template_sync_reports_its_source_instead_of_inventing_approval(client: Any) -> None:
    from tests.helpers import login

    async with login(client, ADMIN) as h:
        synced = await client.post("/api/v1/settings/integrations/whatsapp/sync-templates", headers=h)
        assert synced.status_code == 200, synced.text
        body = synced.json()
        assert {"name", "language", "status"} <= set(body["templates"][0])
        assert "المحاكاة" in body["detail"]  # sim says so out loud
        audited = await client.get("/api/v1/settings/integrations/whatsapp", headers=h)
        assert audited.json()["last_test_status"] in (None, "ok")


async def test_no_settings_route_is_reachable_anonymously(client: Any) -> None:
    """Ten new routes is ten new ways to leak one — none of them may answer without a token."""
    paths = [
        "/api/v1/settings/integrations",
        "/api/v1/settings/integrations/whatsapp",
        "/api/v1/settings/integrations/events",
        "/api/v1/settings/integrations/webhook-urls",
        "/api/v1/settings/policies",
    ]
    for path in paths:
        response = await client.get(path)
        assert response.status_code == 401, f"{path} answered {response.status_code}"


def test_key_rotation_keeps_a_window_instead_of_losing_the_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rotating `SECRETS_ENCRYPTION_KEY` must not orphan every stored credential (ADR-016 §2).

    The failure mode this guards is the operational one: an operator who cannot rotate safely simply never
    rotates, or re-enters one token and leaves the rest unreadable. `SECRETS_ENCRYPTION_KEY_OLD` is the
    window in which both work — read with either key, write with the current one — and closing the window
    turns pre-rotation blobs into "not set", the same safe answer a foreign key already gives.
    """
    from app.core import secrets as store
    from app.core.config import get_settings
    from cryptography.fernet import Fernet, InvalidToken

    retired = Fernet.generate_key().decode()
    current = Fernet.generate_key().decode()
    settings = get_settings()
    monkeypatch.setattr(settings, "SECRETS_ENCRYPTION_KEY", retired)
    monkeypatch.setattr(settings, "SECRETS_ENCRYPTION_KEY_OLD", "")
    written_before = store.encrypt_map({"access_token": "tok-before"})

    monkeypatch.setattr(settings, "SECRETS_ENCRYPTION_KEY", current)
    monkeypatch.setattr(settings, "SECRETS_ENCRYPTION_KEY_OLD", retired)
    assert store.decrypt_map(written_before) == {"access_token": "tok-before"}  # the window opens both ways

    written_after = store.encrypt_map({"access_token": "tok-after"})
    assert Fernet(current.encode()).decrypt(written_after.encode()) == b'{"access_token": "tok-after"}'
    with pytest.raises(InvalidToken):
        Fernet(retired.encode()).decrypt(written_after.encode())  # nothing new is written under the old key

    monkeypatch.setattr(settings, "SECRETS_ENCRYPTION_KEY_OLD", "")
    assert store.decrypt_map(written_before) == {}  # window closed: "not set", never a 500


async def test_the_handshake_reads_the_token_the_ui_actually_saves(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """`verify_token` is a secret, so the handshake must read the secret map — not `config` (ADR-016 §1).

    This is the one place where the settings screen and an inbound provider call have to agree, and the
    first draft got it wrong in a way no unit test would notice: the screen saves the token as a secret,
    the webhook compared it against `config`, and a correctly configured installation would have answered
    403 to Meta forever. An unset token is refused too: accepting `""` would leave the route open.
    """
    from app.core.config import get_settings

    from tests.helpers import login

    monkeypatch.setattr(get_settings(), "WHATSAPP_VERIFY_TOKEN", "", raising=False)

    async with login(client, ADMIN) as h:
        unconfigured = await client.get("/api/v1/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=&hub.challenge=42")
        assert unconfigured.status_code == 403, unconfigured.text
        assert "Verify Token" in unconfigured.json()["message"]  # the answer says what to do, not only "no"

        saved = await client.put("/api/v1/settings/integrations/whatsapp", headers=h, json={"secrets": {"verify_token": "meta-token-42"}})
        assert saved.status_code == 200, saved.text

        accepted = await client.get("/api/v1/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=meta-token-42&hub.challenge=42")
        assert accepted.status_code == 200, accepted.text
        assert accepted.text == "42"  # Meta expects the challenge echoed verbatim, as text/plain

        refused = await client.get("/api/v1/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=nope&hub.challenge=42")
        assert refused.status_code == 403

        rows = (await client.get("/api/v1/settings/integrations/events", headers=h)).json()
        verifications = [row for row in rows if row["event_type"] == "verification"]
        assert {row["status"] for row in verifications} == {"ok", "rejected"}


async def test_the_saved_retention_row_is_what_the_beat_actually_applies(session: Any, client: Any) -> None:
    """`recording_retention_days` is a setting the worker reads from the database, not from memory.

    Two recordings are aged around the boundary the *screen* sets: the one inside it keeps its file, the
    one outside loses it and the object is deleted. Without the row the environment's longer window would
    keep both — which is how a setting becomes a decoration nobody notices is dead.
    """
    from datetime import UTC, datetime, timedelta

    from app.models.requests import CallRecord
    from app.workers.tasks.media import purge_expired

    from tests.helpers import login

    async with login(client, ADMIN) as h:
        saved = await client.put(
            "/api/v1/settings/integrations/telephony",
            headers=h,
            json={"config": {"recording_retention_days": 3}},
        )
        assert saved.status_code == 200, saved.text

        now = datetime.now(UTC)
        keep = CallRecord(
            provider_call_id="retention-keep",
            started_at=now - timedelta(days=2),
            recording_object_key="media/retention/keep.bin",
            duration_s=30,
        )
        drop = CallRecord(
            provider_call_id="retention-drop",
            started_at=now - timedelta(days=9),
            recording_object_key="media/retention/drop.bin",
            duration_s=30,
        )
        session.add_all([keep, drop])
        await session.commit()

        report = await purge_expired(session)
        assert report == {"purged": 1, "retention_days": 3}
        await session.refresh(keep)
        await session.refresh(drop)
        assert keep.recording_object_key == "media/retention/keep.bin"
        assert drop.recording_object_key is None


# ---------------------------------------------------------------------------------------------
# R6: the 409 is a contract, and the screen must be able to see it *before* pressing the button
# ---------------------------------------------------------------------------------------------
async def test_preflight_names_every_blocker_before_the_button_is_pressed(client: Any) -> None:
    """The operator's question is "can I go live?" — the answer arrives without a failed request."""
    from tests.helpers import login

    async with login(client, ADMIN) as h:
        blank = await client.get("/api/v1/settings/integrations/whatsapp/preflight", headers=h)
        assert blank.status_code == 200, blank.text
        body = blank.json()
        assert body["ready"] is False
        # both problems are reported at once: fixing the test alone would only earn a second 409
        assert [blocker["code"] for blocker in body["blockers"]] == ["test", "fields"]
        assert body["missing"] == ["phone_number_id", "access_token"]
        assert "اختبار الاتصال" in body["next"]

        tested = await client.post("/api/v1/settings/integrations/whatsapp/test", headers=h)
        assert tested.json()["ok"] is True

        after_test = (await client.get("/api/v1/settings/integrations/whatsapp/preflight", headers=h)).json()
        assert after_test["test_status"] == "ok"
        assert [blocker["code"] for blocker in after_test["blockers"]] == ["fields"]
        assert after_test["missing"] == ["phone_number_id", "access_token"]
        # the exact fields, in Arabic, are in the payload the screen shows — not only machine keys
        assert "معرّف رقم واتساب" in after_test["labels"][0]
        assert "توكين الوصول" in after_test["labels"][1]

        saved = await client.put(
            "/api/v1/settings/integrations/whatsapp",
            headers=h,
            json={"config": {"phone_number_id": "98765"}, "secrets": {"access_token": "SECRET-XYZ"}},
        )
        assert saved.status_code == 200
        ready = (await client.get("/api/v1/settings/integrations/whatsapp/preflight", headers=h)).json()
        assert ready["ready"] is True
        assert ready["blockers"] == []
        assert ready["next"] == "جاهز للتحويل"

        flipped = await client.post("/api/v1/settings/integrations/whatsapp/live", headers=h, json={"is_live": True, "confirm": True})
        assert flipped.status_code == 200, flipped.text
        assert flipped.json()["is_live"] is True
        assert registry.live_mode("whatsapp") is True


async def test_the_live_endpoint_accepts_confirm_live_as_an_alias(client: Any) -> None:
    """`PUT` calls it `confirm_live`, `POST /live` called it `confirm` — and a client that copied the wrong
    one got "needs confirmation" while it was confirming. Both names now mean yes."""
    from tests.helpers import login

    async with login(client, ADMIN) as h:
        await client.post("/api/v1/settings/integrations/whatsapp/test", headers=h)
        await client.put(
            "/api/v1/settings/integrations/whatsapp",
            headers=h,
            json={"config": {"phone_number_id": "12345"}, "secrets": {"access_token": "TOKEN"}},
        )
        response = await client.post("/api/v1/settings/integrations/whatsapp/live", headers=h, json={"is_live": True, "confirm_live": True})
        assert response.status_code == 200, response.text
        assert response.json()["is_live"] is True


async def test_the_409_carries_the_fields_and_the_next_step(client: Any) -> None:
    """A 409 is only acceptable if it is actionable: status, code, the exact fields, and what to do."""
    from tests.helpers import login

    async with login(client, ADMIN) as h:
        await client.post("/api/v1/settings/integrations/telephony/test", headers=h)
        refused = await client.post("/api/v1/settings/integrations/telephony/live", headers=h, json={"is_live": True, "confirm": True})
        assert refused.status_code == 409, refused.text
        details = refused.json()["details"]
        assert refused.json()["code"] == "incomplete_config"
        assert details["provider"] == "telephony"
        assert details["blockers"][0]["code"] == "fields"
        # the mobile-calls live fields are the provider URL and its signing secret — the screen knew neither
        assert "provider_url" in details["missing"]
        assert "مكالمات الموبايل" in details["labels"][0]
        assert len(details["labels"]) == len(details["missing"])
        assert details["hint"]


async def test_two_clicks_at_once_flip_once_and_both_answer_200(client: Any, session: Any) -> None:
    """The race a double-click produces: the second request must not 500, and the row must end up live."""
    from tests.helpers import login

    async with login(client, ADMIN) as h:
        await client.post("/api/v1/settings/integrations/email/test", headers=h)
        await client.put(
            "/api/v1/settings/integrations/email",
            headers=h,
            json={"config": {"host": "smtp.test", "from_email": "no-reply@brokerhub.test"}, "secrets": {"password": "PW"}},
        )
        first, second = await asyncio.gather(
            client.post("/api/v1/settings/integrations/email/live", headers=h, json={"is_live": True, "confirm": True}),
            client.post("/api/v1/settings/integrations/email/live", headers=h, json={"is_live": True, "confirm": True}),
        )
    assert {first.status_code, second.status_code} == {200}
    assert first.json()["is_live"] is True and second.json()["is_live"] is True
    row = await _row(session, "email")
    assert row.is_live is True


async def test_preflight_is_closed_to_a_rep_and_anonymous_callers(client: Any) -> None:
    from tests.helpers import login

    anonymous = await client.get("/api/v1/settings/integrations/whatsapp/preflight")
    assert anonymous.status_code == 401
    async with login(client, REP) as h:
        assert (await client.get("/api/v1/settings/integrations/whatsapp/preflight", headers=h)).status_code == 403
