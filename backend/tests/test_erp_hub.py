"""ERP hub (ADR-031): spend-by-period, void↔installment, cashbox transparency, rep isolation."""

from __future__ import annotations

from datetime import date
from uuid import UUID

import pytest
from app.core.db import get_sessionmaker
from app.models.phase2 import Installment
from sqlalchemy import select

from tests.helpers import login
from tests.test_finance import ADMIN, MANAGER, REP, _cashbox, _enable_phase2, _issued_policy, _plan

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("period", ["month", "quarter", "semiannual", "year"])
async def test_spend_by_period_all_periods(client, ids, period) -> None:
    async with login(client, MANAGER) as mgr:
        r = await client.get(f"/api/v1/finance/spend-by-period?period={period}", headers=mgr)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["period"] == period and body["label"] and body["start"] <= body["end"]
        assert body["net"] == pytest.approx(body["total_in"] - body["total_out"])
        assert {row["entry_type"] for row in body["by_type"]} == {
            "premium_receivable",
            "commission_receivable",
            "commission_payable",
            "expense",
            "adjustment",
        }
        for row in body["by_type"]:
            assert row["net"] == pytest.approx(row["in_total"] - row["out_total"])


async def test_spend_by_period_counts_posted_and_rejects_bad_period(client, ids) -> None:
    async with login(client, MANAGER) as mgr:
        bad = await client.get("/api/v1/finance/spend-by-period?period=weekly", headers=mgr)
        assert bad.status_code == 422, bad.text
        before = (await client.get("/api/v1/finance/spend-by-period?period=month", headers=mgr)).json()
        posted = await client.post(
            "/api/v1/finance/ledger",
            headers=mgr,
            json={"entry_type": "expense", "direction": "out", "amount": 700, "method": "cash", "note": "erp load probe"},
        )
        assert posted.status_code == 201, posted.text
        after = (await client.get("/api/v1/finance/spend-by-period?period=month", headers=mgr)).json()
        assert after["total_out"] == pytest.approx(before["total_out"] + 700)
        expense = next(r for r in after["by_type"] if r["entry_type"] == "expense")
        assert expense["out_total"] == pytest.approx(next(r for r in before["by_type"] if r["entry_type"] == "expense")["out_total"] + 700)
        # voided rows leave the period totals
        entry = posted.json()
        voided = await client.post(f"/api/v1/finance/ledger/{entry['id']}/void", headers=mgr, json={"reason": "تراجع"})
        assert voided.status_code == 200, voided.text
        final = (await client.get("/api/v1/finance/spend-by-period?period=month", headers=mgr)).json()
        assert final["total_out"] == pytest.approx(before["total_out"])


async def test_period_range_unit() -> None:
    from app.services.finance import period_range

    assert period_range("month", today=date(2026, 10, 28)) == (date(2026, 10, 1), date(2026, 10, 31), "2026-10")
    assert period_range("quarter", today=date(2026, 10, 28))[:2] == (date(2026, 10, 1), date(2026, 12, 31))
    assert period_range("semiannual", today=date(2026, 10, 28))[:2] == (date(2026, 7, 1), date(2026, 12, 31))
    assert period_range("year", today=date(2026, 10, 28))[:2] == (date(2026, 1, 1), date(2026, 12, 31))
    with pytest.raises(Exception) as exc:
        period_range("weekly", today=date(2026, 10, 28))
    assert getattr(exc.value, "status_code", None) == 422


async def test_void_premium_receipt_reopens_installment_and_allows_repay(client, ids) -> None:
    async with login(client, ADMIN) as admin, login(client, MANAGER) as mgr:
        await _enable_phase2(admin, client)
        policy = await _issued_policy(client, mgr, ids["clients"]["ألف"])
        installment = (await _plan(client, mgr, policy["id"]))["items"][0]
        paid = await client.post(
            f"/api/v1/policies/{policy['id']}/installments/{installment['id']}/pay", headers=mgr, json={"method": "cash"}
        )
        assert paid.status_code == 200, paid.text
        entry = paid.json()["ledger_entry"]
        before = await _cashbox(client, mgr)

        voided = await client.post(f"/api/v1/finance/ledger/{entry['id']}/void", headers=mgr, json={"reason": "إيصال خاطئ"})
        assert voided.status_code == 200, voided.text
        async with get_sessionmaker()() as session:
            row = (await session.execute(select(Installment).where(Installment.id == UUID(installment["id"])))).scalar_one()
            assert row.paid_at is None
            expected = "late" if row.due_date < date.today() else "upcoming"
            assert row.status.value == expected
        assert (await _cashbox(client, mgr))["balance"] == pytest.approx(before["balance"] - installment["amount"])

        repaid = await client.post(
            f"/api/v1/policies/{policy['id']}/installments/{installment['id']}/pay", headers=mgr, json={"method": "cash"}
        )
        assert repaid.status_code == 200, repaid.text
        assert repaid.json()["installment"]["status"] == "paid"


async def test_cashbox_declares_net_movements_only(client, ids) -> None:
    async with login(client, MANAGER) as mgr:
        body = await _cashbox(client, mgr)
        assert body["is_net_movements_only"] is True
        assert body["balance_note"] == "net_movements_only"


async def test_rep_without_finance_view_gets_403_on_erp_api(client, ids) -> None:
    async with login(client, REP) as rep:
        for path in [
            "/api/v1/finance/spend-by-period?period=month",
            "/api/v1/finance/ledger",
            "/api/v1/finance/cashbox",
            "/api/v1/finance/ar/summary",
        ]:
            r = await client.get(path, headers=rep)
            assert r.status_code == 403, (path, r.text)
