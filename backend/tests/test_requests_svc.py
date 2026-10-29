"""Inbox service behaviours the UI leans on: SLA recompute, assignment, call rows, audited listens."""

from __future__ import annotations

from uuid import UUID

from app.core.db import get_sessionmaker
from app.models.requests import AuditLog, Notification
from sqlalchemy import select

from tests.helpers import login

ADMIN = "admin@brokerhub.test"
MANAGER = "manager@brokerhub.test"
REP_A = "rep_a@brokerhub.test"


async def _rows(action: str) -> list[dict]:
    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.occurred_at))).scalars().all()
        return [{"entity_id": str(r.entity_id), "after": r.after} for r in rows]


async def _create_request(client, headers, **overrides) -> dict:
    body = {"subject": "طلب اختبار", "channel": "manual", "priority": "normal", "description": "وصف"} | overrides
    response = await client.post("/api/v1/requests", headers=headers, json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def test_priority_change_recomputes_the_deadline(client, ids) -> None:
    async with login(client, MANAGER) as h:
        created = await _create_request(client, h, client_id=ids["clients"]["ألف"])
        assert created["sla_due_at"] is not None
        first_due = created["sla_due_at"]

        patched = await client.patch(
            f"/api/v1/requests/{created['id']}",
            headers=h,
            json={"priority": "urgent", "subject": "طلب اختبار — عاجل"},
        )
        assert patched.status_code == 200
        body = patched.json()
        assert body["priority"] == "urgent"
        assert body["subject"] == "طلب اختبار — عاجل"
        # urgent (30 min) is tighter than normal (4 h), so the clock moved backwards
        assert body["sla_due_at"] < first_due, (body["sla_due_at"], first_due)

    written = await _rows("request.updated")
    assert written and written[-1]["after"]["priority"] == "urgent"


async def test_assignment_notifies_the_assignee_and_is_audited(client, ids) -> None:
    async with login(client, MANAGER) as h:
        created = await _create_request(client, h, client_id=ids["clients"]["ألف"])
        assigned = await client.post(
            f"/api/v1/requests/{created['id']}/assign",
            headers=h,
            json={"assigned_to": ids["users"]["rep_a"]},
        )
        assert assigned.status_code == 200, assigned.text
        assert assigned.json()["assigned_to"] == ids["users"]["rep_a"]

        # an explicit null clears the queue owner (managers may un-assign; the banner then shows it)
        cleared = await client.post(f"/api/v1/requests/{created['id']}/assign", headers=h, json={"assigned_to": None})
        assert cleared.status_code == 200
        assert cleared.json()["assigned_to"] is None

    async with login(client, REP_A) as rh:
        bell = (await client.get("/api/v1/notifications", headers=rh)).json()
        titles = [n["title"] for n in bell]
        assert any(f"#{created['number']}" in title for title in titles), titles

    audit = await _rows("request.assigned")
    assert audit[-1]["after"]["assigned_to"] is None  # the last write is the clear


async def test_inbound_call_is_listed_and_every_listen_is_audited(client) -> None:
    async with login(client, MANAGER) as h:
        started = await client.post(
            "/api/v1/dev/sim/call",
            headers=h,
            json={"from_number": "+201112223344", "caller_name": "منى", "duration_s": 91, "with_recording": True},
        )
        assert started.status_code == 200, started.text
        payload = started.json()
        call_id, request_id = payload["call_id"], payload["request_id"]
        assert call_id and request_id

        calls = (await client.get(f"/api/v1/requests/{request_id}/calls", headers=h)).json()
        assert len(calls) == 1
        assert calls[0]["duration_s"] == 91
        assert calls[0]["has_recording"] is True

        url = await client.get(f"/api/v1/calls/{call_id}/recording-url", headers=h)
        assert url.status_code == 200, url.text
        body = url.json()
        assert body["url"].startswith("/api/v1/media/")
        assert "sig=" in body["url"] and "expires_at" in body
        assert body["recording_consent"] in (True, False)

        again = await client.get(f"/api/v1/calls/{call_id}/recording-url", headers=h)
        assert again.status_code == 200

    listens = await _rows("call.recording_listened")
    assert len(listens) == 2, listens  # one audit row per play, not one per call


async def test_listening_needs_calls_listen_and_the_recording_to_exist(client, ids) -> None:
    # a rep may work the inbox but may not hear recordings (spec §6)
    async with login(client, MANAGER) as h:
        started = await client.post(
            "/api/v1/dev/sim/call",
            headers=h,
            json={"from_number": "+201112223344", "caller_name": "منى", "duration_s": 12, "with_recording": False},
        )
        call_id = started.json()["call_id"]

    async with login(client, REP_A) as rh:
        refused = await client.get(f"/api/v1/calls/{call_id}/recording-url", headers=rh)
        assert refused.status_code == 403
        assert refused.json()["code"] == "forbidden"

    async with login(client, MANAGER) as h:
        missing = await client.get(f"/api/v1/calls/{call_id}/recording-url", headers=h)
        assert missing.status_code == 404  # the call exists, the recording does not (yet)
        assert (await client.get(f"/api/v1/calls/{UUID(int=0)}/recording-url", headers=h)).status_code == 404


async def test_internal_note_is_counted_not_stored_in_bulk(client, ids) -> None:
    async with login(client, MANAGER) as h:
        created = await _create_request(client, h, client_id=ids["clients"]["ألف"])
        note = await client.post(f"/api/v1/requests/{created['id']}/notes", headers=h, json={"body": "راجع المدير قبل الرد"})
        assert note.status_code == 201, note.text
        notes = (await client.get(f"/api/v1/requests/{created['id']}/notes", headers=h)).json()
        assert notes[0]["body"] == "راجع المدير قبل الرد"
        assert notes[0]["author_name"]

    audit = await _rows("request.note_added")
    assert audit[-1]["after"] == {"chars": len("راجع المدير قبل الرد")}


async def test_notifications_are_marked_read_per_user(client, ids) -> None:
    async with login(client, MANAGER) as h:
        created = await _create_request(client, h, client_id=ids["clients"]["ألف"], assigned_to=ids["users"]["rep_a"])
        number = created["number"]

    async with login(client, REP_A) as rh:
        bell = (await client.get("/api/v1/notifications", headers=rh, params={"unread_only": True})).json()
        target = next(n for n in bell if f"#{number}" in n["title"])
        read = await client.post(f"/api/v1/notifications/{target['id']}/read", headers=rh)
        assert read.status_code in (200, 204)
        remaining = (await client.get("/api/v1/notifications", headers=rh, params={"unread_only": True})).json()
        assert target["id"] not in [n["id"] for n in remaining]

        assert (await client.post("/api/v1/notifications/read-all", headers=rh)).status_code in (200, 204)
        assert (await client.get("/api/v1/notifications", headers=rh, params={"unread_only": True})).json() == []

    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(Notification).where(Notification.type == "request_assigned"))).scalars().all()
        assert rows and all(n.read_at is not None for n in rows)
