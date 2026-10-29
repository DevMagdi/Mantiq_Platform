"""AI routes (spec M10): flag gate, consent gate, human review, and no client-visible writes.

These tests never touch the network: `INTEGRATIONS_DRIVER=sim` is forced in conftest, so the transcript
and the summary are deterministic and the assertions below are about *policy*, not model quality.
"""

from __future__ import annotations

import datetime as dt
import uuid

from tests.helpers import login

MANAGER = "manager@brokerhub.test"
REP_A = "rep_a@brokerhub.test"

FLAG = "ai.enabled"


async def _enable_ai() -> None:
    """The flag row exists in the seed; tests toggle it directly to stay independent of the admin route."""
    from app.core.db import get_sessionmaker
    from app.models.masters import FeatureFlag
    from sqlalchemy import select

    maker = get_sessionmaker()
    async with maker() as session:
        found = (await session.execute(select(FeatureFlag).where(FeatureFlag.key == FLAG))).scalar_one_or_none()
        if found is None:
            session.add(FeatureFlag(key=FLAG, enabled=True, description="AI provider"))
        else:
            found.enabled = True
        await session.commit()


async def _new_request(client, auth, ids, subject: str) -> str:
    created = await client.post("/api/v1/requests", json={"subject": subject, "client_id": ids["clients"]["ألف"]}, headers=auth)
    assert created.status_code in (200, 201), created.text
    return str(created.json()["id"])


async def _add_call(client, auth, ids, request_id: str, *, recording: bool, consent: bool) -> str:
    """Call rows normally arrive from the telephony webhook; inserting one keeps the test honest."""
    from app.core.db import get_sessionmaker
    from app.models.requests import CallRecord

    call_id = uuid.uuid4()
    maker = get_sessionmaker()
    async with maker() as session:
        session.add(
            CallRecord(
                id=call_id,
                request_id=uuid.UUID(request_id),
                provider_call_id=f"sim-{call_id}",
                provider="sim",
                direction="inbound",
                from_number="+201000000001",
                started_at=dt.datetime.now(dt.UTC),
                duration_s=95,
                recording_object_key="calls/sim/recording.bin" if recording else None,
                recording_size=2048 if recording else None,
                recording_consent=consent,
            )
        )
        await session.commit()
    return str(call_id)


async def test_status_answers_even_while_the_flag_is_off(client) -> None:
    """The UI needs to know the feature is off, and that answer must not need the flag."""
    async with login(client, MANAGER) as auth:
        body = (await client.get("/api/v1/ai/status", headers=auth)).json()
    assert body["driver"] == "sim"
    assert "transcribe" not in body["capabilities"] or not body["enabled"]


async def test_ai_endpoints_refuse_without_the_flag(client) -> None:
    async with login(client, MANAGER) as auth:
        refused = await client.post("/api/v1/ai/classify", headers=auth, json={"text": "شكوى من التأخير في التعويض"})
    assert refused.status_code == 503
    assert refused.json()["code"] == "feature_disabled"
    assert refused.json()["details"]["flag"] == FLAG


async def test_classify_suggests_and_persists_nothing(client) -> None:
    await _enable_ai()
    async with login(client, MANAGER) as auth:
        response = await client.post("/api/v1/ai/classify", headers=auth, json={"text": "عندي شكوى من التأخير في التعويض"})
        status = (await client.get("/api/v1/ai/status", headers=auth)).json()
        junk = await client.post("/api/v1/ai/classify", headers=auth, json={"text": "شكوى من التأخير في التعويض", "status": "done"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["intent"] == "complaint"
    assert payload["provider"] == "sim"
    assert payload["urgency"] == "high"
    assert set(status["capabilities"]) == {"transcribe", "summarize", "classify"}
    # an unknown key is rejected rather than silently ignored: no hidden knobs on an AI route
    assert junk.status_code == 422


async def test_transcription_needs_a_recording_then_consent(client, ids) -> None:
    await _enable_ai()
    async with login(client, MANAGER) as auth:
        request_id = await _new_request(client, auth, ids, "طلب بلا تسجيل")
        plain = await _add_call(client, auth, ids, request_id, recording=False, consent=False)
        no_recording = await client.post(f"/api/v1/ai/calls/{plain}/transcribe", headers=auth)
        hidden = await _add_call(client, auth, ids, request_id, recording=True, consent=False)
        without_consent = await client.post(f"/api/v1/ai/calls/{hidden}/transcribe", headers=auth)

        allowed = await _add_call(client, auth, ids, request_id, recording=True, consent=True)
        done = await client.post(f"/api/v1/ai/calls/{allowed}/transcribe", headers=auth)
        again = await client.post(f"/api/v1/ai/calls/{allowed}/transcribe", headers=auth)
        forced = await client.post(f"/api/v1/ai/calls/{allowed}/transcribe?force=true", headers=auth)
        calls = (await client.get(f"/api/v1/requests/{request_id}/calls", headers=auth)).json()

    assert no_recording.status_code == 404
    assert without_consent.status_code == 403
    assert without_consent.json()["code"] == "forbidden"
    assert done.status_code == 200, done.text
    assert done.json()["requires_review"] is True
    assert "العميل" in done.json()["transcript"]
    assert again.status_code == 409
    assert forced.status_code == 200
    # the transcript is stored on the call, and the serializer keeps it staff-only
    assert any(c["id"] == allowed and c.get("transcript") for c in calls)


async def test_transcribe_requires_the_recording_permission(client, ids) -> None:
    await _enable_ai()
    async with login(client, MANAGER) as auth:
        request_id = await _new_request(client, auth, ids, "طلب تسجيل غير مسموح")
        call_id = await _add_call(client, auth, ids, request_id, recording=True, consent=True)
    async with login(client, REP_A) as rep:
        refused = await client.post(f"/api/v1/ai/calls/{call_id}/transcribe", headers=rep)
    assert refused.status_code == 403


async def test_summary_lands_in_internal_notes_only(client, ids) -> None:
    await _enable_ai()
    async with login(client, MANAGER) as auth:
        request_id = await _new_request(client, auth, ids, "طلب تحديث بيانات البطاقة رقم 12345")
        await client.post(f"/api/v1/requests/{request_id}/messages", headers=auth, json={"body": "نحتاج صورة الرخصة لتحديث الملف."})
        response = await client.post(f"/api/v1/ai/requests/{request_id}/summarize", headers=auth)
        notes = (await client.get(f"/api/v1/requests/{request_id}/notes", headers=auth)).json()
        thread = (await client.get(f"/api/v1/requests/{request_id}/messages", headers=auth)).json()
        audit_rows = (await client.get("/api/v1/auth/audit/mine", headers=auth)).json()

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["requires_review"] is True
    assert payload["note_id"]
    assert any(payload["note_id"] == str(n["id"]) for n in notes)
    assert "ملخّص آلي" in next(n["body"] for n in notes if str(n["id"]) == payload["note_id"])
    # the machine text is never injected into the client-facing thread
    assert all("ملخّص آلي" not in (m.get("body") or "") for m in thread)
    actions = [row["action"] for row in (audit_rows if isinstance(audit_rows, list) else audit_rows["items"])]
    assert "ai.summarize" in actions


async def test_summary_cannot_be_used_by_a_role_without_the_manage_permission(client, ids) -> None:
    """An AI route inherits the permission of the human action it mirrors — it is not a second door."""
    await _enable_ai()
    async with login(client, MANAGER) as auth:
        request_id = await _new_request(client, auth, ids, "طلب لا يحق لغير الخدمة تلخيصه")
    async with login(client, REP_A) as rep:
        refused = await client.post(f"/api/v1/ai/requests/{request_id}/summarize", headers=rep)
        ghost = await client.post(f"/api/v1/ai/requests/{uuid.uuid4()}/summarize", headers=auth)
    assert refused.status_code == 403
    assert ghost.status_code == 404
