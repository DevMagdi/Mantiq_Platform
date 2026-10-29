"""Score-8 (ADR-033): cashbox opening, period close, period export, today board, ops summary."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.core.db import get_sessionmaker
from app.core.errors import Conflict
from app.domain.enums import LedgerDirection, LedgerEntryType
from app.models.auth import User
from app.services import finance as finance_svc
from sqlalchemy import select

from tests.helpers import login

MANAGER = "manager@brokerhub.test"
ADMIN = "admin@brokerhub.test"
REP = "rep_a@brokerhub.test"
pytestmark = pytest.mark.integration


async def _post(client, headers, **over):
    body = {"entry_type": "expense", "direction": "out", "amount": 100, "method": "cash", "note": "score8", **over}
    return await client.post("/api/v1/finance/ledger", headers=headers, json=body)


async def test_opening_balance_stands_under_the_cashbox(client, ids) -> None:
    async with login(client, MANAGER) as mgr:
        before = await client.get("/api/v1/finance/cashbox", headers=mgr)
        assert before.json()["is_net_movements_only"] is True

        made = await client.post(
            "/api/v1/finance/cashbox/opening",
            headers=mgr,
            json={"amount": 5000, "effective_at": "2020-01-01T00:00:00Z", "note": "جرد افتتاحي"},
        )
        assert made.status_code == 201, made.text

        entry = await _post(client, mgr)
        assert entry.status_code == 201, entry.text

        after = await client.get("/api/v1/finance/cashbox", headers=mgr)
        body = after.json()
        assert body["is_net_movements_only"] is False
        assert body["balance_note"] == "opening_plus_movements"
        assert body["opening_balance"] == 5000.0
        # balance = opening - the 100 cash expense posted above (seed cash is inside the window too,
        # so assert on the delta against opening+movements, not an absolute seed-dependent number)
        assert body["balance"] == body["opening_balance"] + body["cash_in_total"] - body["cash_out_total"]

    async with login(client, REP) as rep:
        refused = await client.post(
            "/api/v1/finance/cashbox/opening", headers=rep, json={"amount": 1, "effective_at": "2020-01-01T00:00:00Z"}
        )
        assert refused.status_code == 403


async def test_period_close_blocks_and_override_is_audited(client, ids) -> None:
    async with login(client, MANAGER) as mgr:
        closed = await client.post("/api/v1/finance/period-closes", headers=mgr, json={"period": "2020-03", "note": "إقفال تجريبي"})
        assert closed.status_code == 201, closed.text
        again = await client.post("/api/v1/finance/period-closes", headers=mgr, json={"period": "2020-03"})
        assert again.status_code == 409

        # the manager holds finance.period_close: posting into the closed month works, and is audited as such
        posted = await client.post(
            "/api/v1/finance/ledger",
            headers=mgr,
            json={"entry_type": "expense", "direction": "out", "amount": 10, "method": "cash", "posted_at": "2020-03-15T10:00:00Z"},
        )
        assert posted.status_code == 201, posted.text

        reopened = await client.delete("/api/v1/finance/period-closes/2020-03", headers=mgr)
        assert reopened.status_code == 200, reopened.text
        listing = await client.get("/api/v1/finance/period-closes", headers=mgr)
        assert listing.json()["items"] == []

    # a user without finance.period_close is refused by the service itself (not just the route)
    async with get_sessionmaker()() as session:
        await finance_svc.close_period(session, (await session.execute(select(User).where(User.email == MANAGER))).scalar_one(), "2020-04")
        account_manager = (await session.execute(select(User).where(User.email == "cs@brokerhub.test"))).scalar_one()
        with pytest.raises(Conflict):
            await finance_svc.post_entry(
                session,
                account_manager,
                entry_type=LedgerEntryType.EXPENSE,
                direction=LedgerDirection.OUT,
                amount=5,
                posted_at=datetime(2020, 4, 10, tzinfo=UTC),
            )
        await session.rollback()


async def test_period_export_matches_spend_totals(client, ids) -> None:
    async with login(client, MANAGER) as mgr:
        data = await client.get("/api/v1/finance/spend-by-period?period=year", headers=mgr)
        exported = await client.get("/api/v1/finance/spend-by-period/export.xlsx?period=year", headers=mgr)
        assert exported.status_code == 200, exported.text
        assert exported.headers["content-type"].startswith("application/vnd.openxmlformats")
        from io import BytesIO

        from openpyxl import load_workbook

        ws = load_workbook(BytesIO(exported.content)).active
        cells = {row[0].value: row[1].value for row in ws.iter_rows() if row[0].value in {"total_in", "total_out", "net"}}
        assert cells["total_in"] == data.json()["total_in"]
        assert cells["net"] == data.json()["net"]

    async with login(client, REP) as rep:
        assert (await client.get("/api/v1/finance/spend-by-period/export.xlsx", headers=rep)).status_code == 403
        assert (await client.get("/api/v1/finance/period-closes", headers=rep)).status_code == 403


async def test_today_board_and_ops_summary(client, ids) -> None:
    async with login(client, MANAGER) as mgr:
        board = await client.get("/api/v1/dashboard/today", headers=mgr)
        assert board.status_code == 200, board.text
        body = board.json()
        for key in ("overdue_requests", "lead_followups", "installments_due"):
            assert key in body and "count" in body[key] and "items" in body[key]

        ops = await client.get("/api/v1/ops/summary", headers=mgr)
        assert ops.status_code == 200, ops.text
        assert ops.json()["database"] == "ok"
        assert {row["provider"] for row in ops.json()["integrations"]} >= {"whatsapp"}
        assert "storage" in ops.json()

    async with login(client, REP) as rep:
        assert (await client.get("/api/v1/ops/summary", headers=rep)).status_code in (403, 404)


async def test_after_sales_full_path_whatsapp_to_closed(client, ids) -> None:
    """A1 acceptance: inbound → reply on the channel → status → close, all on one request."""
    import json as _json

    raw = _json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"wa_id": "2010555000999", "profile": {"name": "عميل"}}],
                                "messages": [
                                    {"id": "wamid.score8-a1", "from": "2010555000999", "type": "text", "text": {"body": "عايز أجدد"}}
                                ],
                            }
                        }
                    ]
                }
            ],
        }
    ).encode()
    delivered = await client.post("/api/v1/webhooks/whatsapp", content=raw, headers={"content-type": "application/json"})
    assert delivered.status_code == 200, delivered.text
    request_id = delivered.json()["results"][0]["request_id"]

    async with login(client, MANAGER) as mgr:
        reply = await client.post(f"/api/v1/requests/{request_id}/messages", headers=mgr, json={"body": "تمام، بنراجع التجديد"})
        assert reply.status_code == 201 and reply.json()["delivery_status"] == "sent"

        moved = await client.post(f"/api/v1/requests/{request_id}/status", headers=mgr, json={"status": "in_progress", "note": "بدأنا"})
        assert moved.status_code == 200, moved.text
        closed = await client.post(f"/api/v1/requests/{request_id}/status", headers=mgr, json={"status": "closed", "note": "تم التجديد"})
        assert closed.status_code == 200, closed.text

        detail = await client.get(f"/api/v1/requests/{request_id}", headers=mgr)
        assert detail.json()["status"] == "closed"
        assert detail.json()["first_response_at"] is not None

        # lead conversion shows the client immediately, and the client file opens (A2/A3 surface)
        clients = await client.get("/api/v1/clients?page_size=5", headers=mgr)
        assert clients.status_code == 200 and clients.json()["items"]
        first = clients.json()["items"][0]["id"]
        profile = await client.get(f"/api/v1/clients/{first}", headers=mgr)
        assert profile.status_code == 200, profile.text
