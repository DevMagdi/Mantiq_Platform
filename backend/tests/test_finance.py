"""R10 — the money layer: ledger, AR aging, commission payouts, cashbox, executive dashboard (ADR-030).

Each test owns one promise the round was asked for, and each one is checked against the *database*, not
against the endpoint's own arithmetic — the point of the round is that a screen never invents a number.

The seven names below are the acceptance list; anything after them is the same promise from another side
(a void is audited, a commission needs approval first, a foreign client's AR is a 404).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

import pytest
from app.core.db import get_sessionmaker
from app.models.finance import LedgerEntry
from app.models.phase2 import Commission, Installment, Policy
from sqlalchemy import func, select

from tests.helpers import login

pytestmark = pytest.mark.integration

ADMIN = "admin@brokerhub.test"
MANAGER = "manager@brokerhub.test"
REP = "rep_a@brokerhub.test"


async def _enable_phase2(admin: Any, client: Any) -> None:
    from app.models.masters import FeatureFlag

    async with get_sessionmaker()() as session:
        existing = (await session.execute(select(FeatureFlag).where(FeatureFlag.key == "phase2.enabled"))).scalar_one_or_none()
        if existing is None:
            session.add(FeatureFlag(key="phase2.enabled", enabled=False, description="Phase 2 screens"))
            await session.commit()
    response = await client.patch("/api/v1/feature-flags/phase2.enabled", headers=admin, json={"enabled": True})
    assert response.status_code == 200, response.text


async def _issued_policy(client: Any, headers: dict[str, str], client_id: str, **overrides: Any) -> dict[str, Any]:
    """A draft, then the real issuance step — installments and the commission accrual come with it."""
    payload: dict[str, Any] = {
        "client_id": client_id,
        "start_date": date.today().isoformat(),
        "end_date": (date.today() + timedelta(days=365)).isoformat(),
        "premium_amount": 48000,
        "payment_plan": "quarterly",
        "commission_rate": 5,
    }
    payload.update(overrides)
    draft = await client.post("/api/v1/policies", headers=headers, json=payload)
    assert draft.status_code == 201, draft.text
    issued = await client.post(f"/api/v1/policies/{draft.json()['id']}/issue", headers=headers)
    assert issued.status_code == 200, issued.text
    return issued.json()


async def _plan(client: Any, headers: dict[str, str], policy_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/v1/policies/{policy_id}/installments", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def _cashbox(client: Any, headers: dict[str, str]) -> dict[str, Any]:
    response = await client.get("/api/v1/finance/cashbox", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def test_paying_an_installment_creates_ledger_entry(client, ids) -> None:
    """The round's first promise: a collection is a ledger row, in the same transaction as `paid_at`."""
    async with login(client, ADMIN) as admin, login(client, MANAGER) as mgr:
        await _enable_phase2(admin, client)
        policy = await _issued_policy(client, mgr, ids["clients"]["ألف"])
        plan = await _plan(client, mgr, policy["id"])
        installment = plan["items"][0]

        before = await _cashbox(client, mgr)
        paid = await client.post(
            f"/api/v1/policies/{policy['id']}/installments/{installment['id']}/pay",
            headers=mgr,
            json={"method": "cash"},
        )
        assert paid.status_code == 200, paid.text
        body = paid.json()
        assert body["installment"]["status"] == "paid"
        entry = body["ledger_entry"]
        assert entry["entry_type"] == "premium_receivable"
        assert entry["direction"] == "in"
        assert entry["amount"] == pytest.approx(installment["amount"])
        assert entry["method"] == "cash"

        # the row exists for every other reader, not only in the response that created it
        listing = await client.get("/api/v1/finance/ledger?page_size=50", headers=mgr)
        assert listing.status_code == 200, listing.text
        rows = [row for row in listing.json()["items"] if row["installment_id"] == installment["id"]]
        assert len(rows) == 1
        assert rows[0]["status"] == "posted"
        assert rows[0]["client_name"] == "شركة ألف"

        after = await _cashbox(client, mgr)
        assert after["balance"] == pytest.approx(before["balance"] + installment["amount"])
        assert after["cash_in_total"] == pytest.approx(before["cash_in_total"] + installment["amount"])

        # and paying twice is still a 409 — no second row either
        twice = await client.post(
            f"/api/v1/policies/{policy['id']}/installments/{installment['id']}/pay", headers=mgr, json={"method": "cash"}
        )
        assert twice.status_code == 409, twice.text
        async with get_sessionmaker()() as session:
            count = (
                await session.execute(
                    select(func.count()).select_from(LedgerEntry).where(LedgerEntry.installment_id == UUID(installment["id"]))
                )
            ).scalar_one()
        assert count == 1


async def test_void_ledger_entry_reverses_cashbox_balance(client, ids) -> None:
    """A void is a state, not a delete: the balance goes back and the row stays with its reason."""
    async with login(client, MANAGER) as mgr:
        before = await _cashbox(client, mgr)
        posted = await client.post(
            "/api/v1/finance/ledger",
            headers=mgr,
            json={
                "entry_type": "adjustment",
                "direction": "in",
                "amount": 5000,
                "method": "cash",
                "note": "إيصال مستخلص يدوي",
            },
        )
        assert posted.status_code == 201, posted.text
        entry = posted.json()
        assert entry["entry_no"].startswith("LE-")

        after = await _cashbox(client, mgr)
        assert after["balance"] == pytest.approx(before["balance"] + 5000)

        voided = await client.post(f"/api/v1/finance/ledger/{entry['id']}/void", headers=mgr, json={"reason": "إيصال مكرر"})
        assert voided.status_code == 200, voided.text
        assert voided.json()["entry"]["status"] == "void"
        assert voided.json()["entry"]["void_reason"] == "إيصال مكرر"
        assert voided.json()["cashbox_balance"] == pytest.approx(before["balance"])

        again = await _cashbox(client, mgr)
        assert again["balance"] == pytest.approx(before["balance"])
        assert again["cash_in_total"] == pytest.approx(before["cash_in_total"])

        # the row is still there — history is readable, and a second void is a conflict, not a no-op
        listing = await client.get(f"/api/v1/finance/ledger?q={entry['entry_no']}", headers=mgr)
        assert listing.status_code == 200, listing.text
        assert listing.json()["total"] == 1
        assert listing.json()["items"][0]["status"] == "void"
        assert listing.json()["items"][0]["amount"] == pytest.approx(5000)

        twice = await client.post(f"/api/v1/finance/ledger/{entry['id']}/void", headers=mgr, json={"reason": "مرة أخرى"})
        assert twice.status_code == 409, twice.text

        # and the void itself is in the audit trail (spec §5)
        from app.models.requests import AuditLog

        async with get_sessionmaker()() as session:
            actions = (await session.execute(select(AuditLog.action).where(AuditLog.entity_id == UUID(entry["id"])))).scalars().all()
        assert "ledger.voided" in actions


async def test_voiding_a_receipt_reopens_the_installment(client, ids) -> None:
    """R10.1 point 4 — a void reverses the *fact*, not only the sums.

    Before this, voiding an installment receipt left the installment `paid` and unpayable: the cashbox and
    the AR total both dropped the money, while the customer's plan still read "collected" and a second
    attempt answered `409 already marked paid`. The three assertions here are the three ways that showed up.
    """
    async with login(client, ADMIN) as admin, login(client, MANAGER) as mgr:
        await _enable_phase2(admin, client)
        policy = await _issued_policy(client, mgr, ids["clients"]["ألف"])
        plan = await _plan(client, mgr, policy["id"])
        installment = plan["items"][0]
        amount = float(installment["amount"])
        pay_url = f"/api/v1/policies/{policy['id']}/installments/{installment['id']}/pay"

        paid = await client.post(pay_url, headers=mgr, json={"method": "cash"})
        assert paid.status_code == 200, paid.text
        entry = paid.json()["ledger_entry"]

        # 1. an attempt to pay twice refuses with the receipt named, so there is a next step to take
        twice = await client.post(pay_url, headers=mgr, json={"method": "cash"})
        assert twice.status_code == 409, twice.text
        assert twice.json()["details"]["receipt_entry_id"] == entry["id"]
        assert twice.json()["details"]["receipt_entry_no"] == entry["entry_no"]

        before_void = await _cashbox(client, mgr)
        voided = await client.post(f"/api/v1/finance/ledger/{entry['id']}/void", headers=mgr, json={"reason": "شيك مرتجع"})
        assert voided.status_code == 200, voided.text
        body = voided.json()
        assert body["entry"]["status"] == "void"
        # 2. the void says what it put back, instead of leaving the operator to go and look
        reopened_info = body["reopened"]
        assert reopened_info["kind"] == "installment"
        assert reopened_info["id"] == installment["id"]
        assert reopened_info["status"] in {"upcoming", "late"}
        assert body["cashbox_balance"] == pytest.approx(before_void["balance"] - amount)

        # 3. the plan and the ledger agree again, and the money can be taken a second time as a real receipt
        plan_after = await _plan(client, mgr, policy["id"])
        reopened = next(row for row in plan_after["items"] if row["id"] == installment["id"])
        assert reopened["paid_at"] is None
        assert reopened["status"] in {"upcoming", "late"}

        again = await client.post(pay_url, headers=mgr, json={"method": "cash"})
        assert again.status_code == 200, again.text
        assert again.json()["ledger_entry"]["entry_no"] != entry["entry_no"]

        # the reopening is audited with its reason and the entry that caused it (spec §5)
        from app.models.requests import AuditLog

        async with get_sessionmaker()() as session:
            rows = (
                await session.execute(select(AuditLog.action, AuditLog.after).where(AuditLog.entity_id == UUID(installment["id"])))
            ).all()
        actions = [row.action for row in rows]
        assert "installment.paid" in actions
        assert "installment.reopened" in actions
        reopened_row = next(after for action, after in rows if action == "installment.reopened")
        assert reopened_row["reason"] == "شيك مرتجع"
        assert reopened_row["voided_entry_no"] == entry["entry_no"]


async def test_voiding_a_commission_payout_returns_it_to_approved(client, ids) -> None:
    """The same rule on the payout side: a voided transfer must not leave a settlement marked `paid`."""
    async with login(client, ADMIN) as admin, login(client, MANAGER) as mgr:
        await _enable_phase2(admin, client)
        policy = await _issued_policy(client, mgr, ids["clients"]["باء"])
        commission_id = policy["commission"]["id"] if policy.get("commission") else None
        if commission_id is None:
            listing = await client.get("/api/v1/finance/commissions?page_size=200", headers=mgr)
            assert listing.status_code == 200, listing.text
            commission_id = next(row["id"] for row in listing.json()["items"] if row["policy_id"] == policy["id"])

        approved = await client.post(f"/api/v1/finance/commissions/{commission_id}/approve", headers=mgr, json={})
        assert approved.status_code == 200, approved.text
        paid = await client.post(f"/api/v1/finance/commissions/{commission_id}/pay", headers=mgr, json={"method": "bank"})
        assert paid.status_code == 200, paid.text
        entry_id = paid.json()["ledger_entry_id"]
        assert entry_id

        voided = await client.post(f"/api/v1/finance/ledger/{entry_id}/void", headers=mgr, json={"reason": "تحويل مرتجع"})
        assert voided.status_code == 200, voided.text
        assert voided.json()["reopened"]["kind"] == "commission"

        # the payout is `approved` again — not `paid` behind a voided transfer — and can be paid again
        listing = await client.get("/api/v1/finance/commissions?page_size=200", headers=mgr)
        row = next(item for item in listing.json()["items"] if item["id"] == commission_id)
        assert row["status"] == "approved"
        assert row["paid_at"] is None
        assert row["ledger_entry_id"] is None

        again = await client.post(f"/api/v1/finance/commissions/{commission_id}/pay", headers=mgr, json={"method": "bank"})
        assert again.status_code == 200, again.text
        assert again.json()["ledger_entry_id"] != entry_id


async def test_commission_cannot_be_paid_twice(client, ids) -> None:
    """One accrual, one payout — the second `pay` is a 409 and writes no second outgoing row."""
    async with login(client, ADMIN) as admin, login(client, MANAGER) as mgr:
        await _enable_phase2(admin, client)
        policy = await _issued_policy(client, mgr, ids["clients"]["ألف"], premium_amount=100000, commission_rate=5)

        listing = await client.get("/api/v1/finance/commissions?page_size=200", headers=mgr)
        assert listing.status_code == 200, listing.text
        row = next(item for item in listing.json()["items"] if item["policy_id"] == policy["id"])
        assert row["amount"] == pytest.approx(5000)
        assert row["status"] == "accrued"

        # a payout without approval is refused — the sequence is the control
        early = await client.post(f"/api/v1/finance/commissions/{row['id']}/pay", headers=mgr, json={})
        assert early.status_code == 409, early.text

        approved = await client.post(f"/api/v1/finance/commissions/{row['id']}/approve", headers=mgr, json={})
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "approved"

        paid = await client.post(f"/api/v1/finance/commissions/{row['id']}/pay", headers=mgr, json={"method": "bank"})
        assert paid.status_code == 200, paid.text
        paid_body = paid.json()
        assert paid_body["status"] == "paid"
        assert paid_body["ledger_entry_no"]

        twice = await client.post(f"/api/v1/finance/commissions/{row['id']}/pay", headers=mgr, json={"method": "bank"})
        assert twice.status_code == 409, twice.text

        async with get_sessionmaker()() as session:
            outgoing = (
                await session.execute(
                    select(func.count())
                    .select_from(LedgerEntry)
                    .where(
                        LedgerEntry.policy_id == UUID(policy["id"]),
                        LedgerEntry.entry_type == "commission_payable",
                        LedgerEntry.status == "posted",
                    )
                )
            ).scalar_one()
        assert outgoing == 1

        # the accrual row the older screen reads agrees that the commission is settled
        async with get_sessionmaker()() as session:
            commission = (await session.execute(select(Commission).where(Commission.policy_id == UUID(policy["id"])))).scalar_one()
            assert commission.status.value == "settled"


async def test_ar_aging_buckets(client, ids) -> None:
    """Late money lands in the bucket its *due date* earns, and not-yet-due money is never "overdue"."""
    async with login(client, ADMIN) as admin, login(client, MANAGER) as mgr:
        await _enable_phase2(admin, client)
        policy = await _issued_policy(client, mgr, ids["clients"]["باء"], premium_amount=80000, payment_plan="quarterly")
        plan = await _plan(client, mgr, policy["id"])
        rows = plan["items"]
        assert len(rows) >= 4

        today = date.today()
        wanted = {
            rows[0]["id"]: (today - timedelta(days=10), "d0_30"),
            rows[1]["id"]: (today - timedelta(days=45), "d31_60"),
            rows[2]["id"]: (today - timedelta(days=75), "d61_90"),
            rows[3]["id"]: (today - timedelta(days=120), "d90_plus"),
        }
        async with get_sessionmaker()() as session:
            for installment_id, (due, _bucket) in wanted.items():
                row = (await session.execute(select(Installment).where(Installment.id == UUID(installment_id)))).scalar_one()
                row.due_date = due
            await session.commit()

        detail = await client.get(f"/api/v1/finance/ar/clients/{ids['clients']['باء']}", headers=mgr)
        assert detail.status_code == 200, detail.text
        body = detail.json()
        by_id = {item["installment_id"]: item for item in body["installments"]}
        for installment_id, (_due, bucket) in wanted.items():
            assert by_id[installment_id]["bucket"] == bucket, by_id[installment_id]

        buckets = {item["bucket"]: item for item in body["buckets"]}
        assert buckets["d0_30"]["amount"] == pytest.approx(rows[0]["amount"])
        assert buckets["d31_60"]["amount"] == pytest.approx(rows[1]["amount"])
        assert buckets["d61_90"]["amount"] == pytest.approx(rows[2]["amount"])
        assert buckets["d90_plus"]["amount"] == pytest.approx(rows[3]["amount"])
        assert body["outstanding_total"] == pytest.approx(sum(row["amount"] for row in rows))
        assert body["overdue_total"] == pytest.approx(sum(row["amount"] for row in rows))
        assert body["oldest_due_date"] == (today - timedelta(days=120)).isoformat()

        # the office-wide summary is the same rows summed: buckets + totals must agree with the detail
        summary = await client.get("/api/v1/finance/ar/summary", headers=mgr)
        assert summary.status_code == 200, summary.text
        summary_buckets = {item["bucket"]: item for item in summary.json()["buckets"]}
        assert set(summary_buckets) == {"current", "d0_30", "d31_60", "d61_90", "d90_plus"}
        assert summary.json()["outstanding_total"] == pytest.approx(sum(item["amount"] for item in summary_buckets.values()))
        assert summary.json()["overdue_total"] == pytest.approx(summary.json()["outstanding_total"] - summary_buckets["current"]["amount"])


async def test_executive_dashboard_numbers_match_source_queries(client, ids) -> None:
    """Every KPI is compared with its own SQL. A dashboard that disagrees with its table is a P0."""
    async with login(client, ADMIN) as admin, login(client, MANAGER) as mgr:
        await _enable_phase2(admin, client)
        policy = await _issued_policy(client, mgr, ids["clients"]["ألف"], premium_amount=60000, commission_rate=5)
        plan = await _plan(client, mgr, policy["id"])
        await client.post(
            f"/api/v1/policies/{policy['id']}/installments/{plan['items'][0]['id']}/pay", headers=mgr, json={"method": "bank"}
        )

        response = await client.get("/api/v1/executive/dashboard", headers=mgr)
        assert response.status_code == 200, response.text
        body = response.json()
        kpis = body["kpis"]

        month_start = date.today().replace(day=1)
        month_start_dt = datetime.combine(month_start, time.min, tzinfo=UTC)
        async with get_sessionmaker()() as session:
            collections = (
                await session.execute(
                    select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
                        LedgerEntry.status == "posted",
                        LedgerEntry.direction == "in",
                        LedgerEntry.posted_at >= month_start_dt,
                    )
                )
            ).scalar_one()
            outstanding = (
                await session.execute(
                    select(func.coalesce(func.sum(Installment.amount), 0))
                    .select_from(Installment)
                    .join(Policy, Installment.policy_id == Policy.id)
                    .where(Policy.status == "issued", Installment.paid_at.is_(None))
                )
            ).scalar_one()
            overdue = (
                await session.execute(
                    select(func.coalesce(func.sum(Installment.amount), 0))
                    .select_from(Installment)
                    .join(Policy, Installment.policy_id == Policy.id)
                    .where(
                        Policy.status == "issued",
                        Installment.paid_at.is_(None),
                        Installment.due_date < date.today(),
                    )
                )
            ).scalar_one()
            cash = (
                await session.execute(
                    select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
                        LedgerEntry.status == "posted",
                        LedgerEntry.method == "cash",
                        LedgerEntry.direction == "in",
                    )
                )
            ).scalar_one() - (
                await session.execute(
                    select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
                        LedgerEntry.status == "posted",
                        LedgerEntry.method == "cash",
                        LedgerEntry.direction == "out",
                    )
                )
            ).scalar_one()
            issued_mtd = (
                await session.execute(
                    select(func.count())
                    .select_from(Policy)
                    .where(Policy.status == "issued", func.coalesce(Policy.issued_at, Policy.created_at) >= month_start_dt)
                )
            ).scalar_one()

        assert kpis["collections_mtd"] == pytest.approx(float(collections))
        assert kpis["outstanding_ar"] == pytest.approx(float(outstanding))
        assert kpis["overdue_ar"] == pytest.approx(float(overdue))
        assert kpis["cashbox_balance"] == pytest.approx(float(cash))
        assert kpis["policies_issued_mtd"] == issued_mtd
        # the target is honestly absent, not guessed
        assert kpis["collections_target"] is None

        # the fixed shape the screen binds to
        assert set(body) == {
            "period",
            "kpis",
            "sales_performance",
            "ar_aging",
            "channel_mix",
            "renewals_next_30d",
            "alerts",
        }
        assert {item["bucket"] for item in body["ar_aging"]} == {"current", "d0_30", "d31_60", "d61_90", "d90_plus"}
        assert body["period"]["from_date"] == month_start.isoformat()
        assert {item["channel"] for item in body["channel_mix"]} == {"whatsapp", "call", "email", "manual"}
        assert isinstance(body["renewals_next_30d"], list)
        assert all({"code", "severity", "message", "count"} <= set(alert) for alert in body["alerts"])
        # the sales board is per rep and its collections are a subset of the office's own total
        assert body["sales_performance"], "the seeded book has owners, so the board cannot be empty"
        assert sum(row["collected_mtd"] for row in body["sales_performance"]) == pytest.approx(kpis["collections_mtd"])


async def test_executive_does_not_guess_an_unknown_issue_date(client, ids) -> None:
    """R10.1 point 5: `policies_issued_mtd` counts recorded issue dates, and says how many it cannot.

    `coalesce(issued_at, created_at)` counted a policy booked this month as issued this month even when the
    office never recorded an issue — the same wrong number the column was added to remove. The count is now
    `issued_at` only, and the unknown rows are reported as their own alert instead of being folded in.
    """
    async with login(client, ADMIN) as admin, login(client, MANAGER) as mgr:
        await _enable_phase2(admin, client)

        def unknown_count(payload: dict[str, Any]) -> int:
            return next(alert["count"] for alert in payload["alerts"] if alert["code"] == "issue_date_unknown")

        before = (await client.get("/api/v1/executive/dashboard", headers=mgr)).json()
        policy = await _issued_policy(client, mgr, ids["clients"]["ألف"])

        issued = (await client.get("/api/v1/executive/dashboard", headers=mgr)).json()
        assert issued["kpis"]["policies_issued_mtd"] == before["kpis"]["policies_issued_mtd"] + 1
        assert unknown_count(issued) == unknown_count(before)

        # the state a policy issued before the audit trail was recorded ended up in: no issue date at all
        async with get_sessionmaker()() as session:
            row = await session.get(Policy, UUID(policy["id"]))
            assert row is not None
            row.issued_at = None
            await session.commit()

        forgotten = (await client.get("/api/v1/executive/dashboard", headers=mgr)).json()
        # `created_at` is this month — counting it would make the screen print a month nobody recorded
        assert forgotten["kpis"]["policies_issued_mtd"] == before["kpis"]["policies_issued_mtd"]
        assert unknown_count(forgotten) == unknown_count(before) + 1


async def test_rep_without_finance_view_gets_403(client, ids) -> None:
    """A rep's screen has no cashbox and no office totals — the API says 403, not an empty table."""
    async with login(client, ADMIN) as admin, login(client, REP) as rep:
        await _enable_phase2(admin, client)
        for path in (
            "/api/v1/finance/ledger",
            "/api/v1/finance/cashbox",
            "/api/v1/finance/ar/summary",
            "/api/v1/finance/ar/clients",
            "/api/v1/finance/commissions",
            "/api/v1/executive/dashboard",
        ):
            response = await client.get(path, headers=rep)
            assert response.status_code == 403, f"{path} -> {response.status_code} {response.text[:120]}"

        posted = await client.post("/api/v1/finance/ledger", headers=rep, json={"entry_type": "expense", "direction": "out", "amount": 100})
        assert posted.status_code == 403, posted.text
        approved = await client.post(f"/api/v1/finance/commissions/{ids['clients']['ألف']}/approve", headers=rep, json={})
        assert approved.status_code == 403, approved.text


async def test_ledger_rejects_non_positive_amount(client, ids) -> None:
    """A zero or a negative amount is not a movement — `direction` is what carries the sign."""
    async with login(client, MANAGER) as mgr:
        for amount in (0, -1, -250.5):
            response = await client.post(
                "/api/v1/finance/ledger",
                headers=mgr,
                json={"entry_type": "expense", "direction": "out", "amount": amount, "method": "cash"},
            )
            assert response.status_code == 422, f"{amount} -> {response.status_code} {response.text[:160]}"

        # the type decides the direction: a receipt cannot be money out, an expense cannot be money in
        wrong_way = await client.post(
            "/api/v1/finance/ledger",
            headers=mgr,
            json={"entry_type": "expense", "direction": "in", "amount": 100, "method": "cash"},
        )
        assert wrong_way.status_code == 422, wrong_way.text

        # and nothing was written by any of the refusals
        async with get_sessionmaker()() as session:
            rows = (await session.execute(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.amount <= 0))).scalar_one()
        assert rows == 0


async def test_ar_for_a_foreign_client_is_404_not_403(client, ids) -> None:
    """Isolation is a 404 for a client outside the book, exactly like policies and requests."""
    async with login(client, ADMIN) as admin, login(client, MANAGER) as mgr:
        await _enable_phase2(admin, client)
        missing = await client.get("/api/v1/finance/ar/clients/00000000-0000-0000-0000-000000000000", headers=mgr)
        assert missing.status_code == 404, missing.text

        # a rep holds no `finance.view`, so the same id is refused before it can be looked up
        async with login(client, REP) as rep:
            refused = await client.get(f"/api/v1/finance/ar/clients/{ids['clients']['ألف']}", headers=rep)
        assert refused.status_code == 403, refused.text


async def test_every_money_movement_is_audited(client, ids) -> None:
    """Posting, voiding and paying all leave an audit row with the amount and the actor (spec §5)."""
    from app.models.requests import AuditLog

    async with login(client, ADMIN) as admin, login(client, MANAGER) as mgr:
        await _enable_phase2(admin, client)
        policy = await _issued_policy(client, mgr, ids["clients"]["ألف"], premium_amount=30000, commission_rate=10)
        plan = await _plan(client, mgr, policy["id"])
        await client.post(
            f"/api/v1/policies/{policy['id']}/installments/{plan['items'][0]['id']}/pay", headers=mgr, json={"method": "cash"}
        )

        listing = await client.get("/api/v1/finance/commissions?page_size=200", headers=mgr)
        row = next(item for item in listing.json()["items"] if item["policy_id"] == policy["id"])
        approved = await client.post(f"/api/v1/finance/commissions/{row['id']}/approve", headers=mgr, json={})
        assert approved.status_code == 200, approved.text
        settlement_id = approved.json()["settlement_id"]
        paid = await client.post(f"/api/v1/finance/commissions/{row['id']}/pay", headers=mgr, json={"method": "cash"})
        assert paid.status_code == 200, paid.text

        # the audit row names the settlement (the payout), and carries the amount it moved
        async with get_sessionmaker()() as session:
            actions = set(
                (
                    await session.execute(
                        select(AuditLog.action).where(
                            AuditLog.entity_type == "commission_settlement", AuditLog.entity_id == UUID(settlement_id)
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert {"commission.paid", "commission.approved"} <= actions

        async with get_sessionmaker()() as session:
            ledger_actions = set(
                (await session.execute(select(AuditLog.action).where(AuditLog.entity_type == "ledger_entry"))).scalars().all()
            )
        assert "ledger.posted" in ledger_actions
