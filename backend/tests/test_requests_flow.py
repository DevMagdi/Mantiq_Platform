"""Service desk: request numbers, business-hours SLA, the sweep, replies and notes (spec §7/§8.3)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from app.core.db import get_sessionmaker
from app.integrations.sims import WhatsAppSim
from app.models.requests import ServiceRequest
from app.services import requests_svc

from tests.helpers import login

MANAGER = "manager@brokerhub.test"
REP = "rep_a@brokerhub.test"


async def _create(client: Any, auth: dict[str, str], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"subject": "إضافة موظف جديد للعقد", "channel": "manual", "client_id": overrides.pop("client_id")}
    body.update(overrides)
    response = await client.post("/api/v1/requests", json=body, headers=auth)
    assert response.status_code == 201, response.text
    return response.json()


async def test_request_numbers_start_at_1000_and_are_unique(client, ids: dict[str, dict[str, str]]) -> None:
    """`#2841` in the UI, always ≥ 1000 in the data (spec §5 requests.number)."""
    async with login(client, MANAGER) as auth:
        first = await _create(client, auth, client_id=ids["clients"]["ألف"])
        second = await _create(client, auth, client_id=ids["clients"]["ألف"])
    assert first["number"] >= 1000 and second["number"] >= 1000
    assert second["number"] > first["number"]
    assert first["sla_due_at"] is not None and first["is_overdue"] is False


async def test_priority_sets_a_business_hours_deadline(client, ids: dict[str, dict[str, str]]) -> None:
    async with login(client, MANAGER) as auth:
        urgent = await _create(client, auth, client_id=ids["clients"]["ألف"], priority="urgent")
        low = await _create(client, auth, client_id=ids["clients"]["ألف"], priority="low")
    due_urgent = datetime.fromisoformat(urgent["sla_due_at"])
    due_low = datetime.fromisoformat(low["sla_due_at"])
    # 30 min vs 600 min of working time, both inside 09:00–18:00 and never on Fri/Sat
    assert due_urgent > datetime.fromisoformat(urgent["created_at"])
    assert due_low > due_urgent


async def test_sweep_is_the_only_writer_of_is_overdue(client, ids: dict[str, dict[str, str]]) -> None:
    async with login(client, MANAGER) as auth:
        created = await _create(client, auth, client_id=ids["clients"]["ألف"], priority="urgent")
        request_id = created["id"]

        # past due, but the flag stays false until the beat task runs
        past = datetime.fromisoformat(created["sla_due_at"]) + timedelta(minutes=5)
        maker = get_sessionmaker()
        async with maker() as session:
            await session.execute(
                sa.update(ServiceRequest)
                .where(ServiceRequest.id == UUID(request_id))
                .values(sla_due_at=past - timedelta(minutes=5), created_at=past - timedelta(hours=2))
            )
            await session.commit()

        before = await client.get(f"/api/v1/requests/{request_id}", headers=auth)
        assert before.json()["is_overdue"] is False

        async with maker() as session:
            result = await requests_svc.sweep_overdue(session, now=past)
            await session.commit()
        assert result["flipped"] >= 1

        after = await client.get(f"/api/v1/requests/{request_id}", headers=auth)
        assert after.json()["is_overdue"] is True
        assert after.json()["status"] == "new"  # the sweep never changes status

        # same clock twice => no further writes (the flag is a derived, recomputed value)
        maker = get_sessionmaker()
        async with maker() as session:
            again = await requests_svc.sweep_overdue(session, now=past)
            await session.commit()
        assert again["flipped"] == 0

        # and with the real clock the row is not due yet: the sweep recomputes it back to false
        async with maker() as session:
            rewound = await requests_svc.sweep_overdue(session, now=datetime.now(UTC))
            await session.commit()
        assert rewound["flipped"] >= 1
        cleared = await client.get(f"/api/v1/requests/{request_id}", headers=auth)
        assert cleared.json()["is_overdue"] is False


async def test_reply_goes_out_through_the_channel_provider(client, ids: dict[str, dict[str, str]]) -> None:
    WhatsAppSim.reset()
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
                                    {"id": "wamid.reply_1", "from": "201099887766", "type": "text", "text": {"body": "ممكن تزيد التغطية؟"}}
                                ],
                            }
                        }
                    ]
                }
            ],
        }
    ).encode()
    inbound = await client.post("/api/v1/webhooks/whatsapp", content=raw, headers={"content-type": "application/json"})
    request_id = inbound.json()["results"][0]["request_id"]

    async with login(client, MANAGER) as auth:
        reply = await client.post(f"/api/v1/requests/{request_id}/messages", json={"body": "تم، هنبعت التحديث"}, headers=auth)
        assert reply.status_code == 201, reply.text
        assert reply.json()["direction"] == "out"
        assert reply.json()["delivery_status"] == "sent"
        thread = await client.get(f"/api/v1/requests/{request_id}/messages", headers=auth)
        bodies = [(row["direction"], row["body"]) for row in thread.json()]
        assert ("out", "تم، هنبعت التحديث") in bodies

    assert len(WhatsAppSim.sent) == 1
    assert WhatsAppSim.sent[0]["to"] == "+201099887766"
    assert WhatsAppSim.sent[0]["body"] == "تم، هنبعت التحديث"


async def test_empty_reply_is_rejected(client, ids: dict[str, dict[str, str]]) -> None:
    async with login(client, MANAGER) as auth:
        created = await _create(client, auth, client_id=ids["clients"]["ألف"])
        response = await client.post(f"/api/v1/requests/{created['id']}/messages", json={"body": ""}, headers=auth)
    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"
    assert response.json()["details"][0]["field"] == "body"


async def test_internal_notes_are_separate_from_the_thread(client, ids: dict[str, dict[str, str]]) -> None:
    async with login(client, MANAGER) as auth:
        created = await _create(client, auth, client_id=ids["clients"]["ألف"])
        note = await client.post(f"/api/v1/requests/{created['id']}/notes", json={"body": "الكلمة السرية للتأمين 1234"}, headers=auth)
        assert note.status_code in (200, 201), note.text
        notes = await client.get(f"/api/v1/requests/{created['id']}/notes", headers=auth)
        messages = await client.get(f"/api/v1/requests/{created['id']}/messages", headers=auth)
        detail = await client.get(f"/api/v1/requests/{created['id']}", headers=auth)
    assert any(row["body"] == "الكلمة السرية للتأمين 1234" for row in notes.json())
    assert all("الكلمة السرية" not in (row["body"] or "") for row in messages.json())
    assert any(n["body"] == "الكلمة السرية للتأمين 1234" for n in detail.json()["notes"])


async def test_status_change_does_not_fake_a_first_response(client, ids: dict[str, dict[str, str]]) -> None:
    """ "First response" is the first message the customer receives, not an internal status click."""
    async with login(client, MANAGER) as auth:
        created = await _create(client, auth, client_id=ids["clients"]["ألف"])
        changed = await client.post(
            f"/api/v1/requests/{created['id']}/status", json={"status": "in_progress", "note": "بدأنا"}, headers=auth
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["status"] == "in_progress"
        assert changed.json()["first_response_at"] is None

        replied = await client.post(f"/api/v1/requests/{created['id']}/messages", json={"body": "تم الاستلام"}, headers=auth)
        after = await client.get(f"/api/v1/requests/{created['id']}", headers=auth)
    assert replied.status_code == 201
    assert after.json()["first_response_at"] is not None
    assert after.json()["status"] in {"in_progress", "replied"}


async def test_rep_without_requests_permission_cannot_open_the_inbox(client, ids: dict[str, dict[str, str]]) -> None:
    async with login(client, REP) as auth:
        listed = await client.get("/api/v1/requests", headers=auth)
    assert listed.status_code == 403
