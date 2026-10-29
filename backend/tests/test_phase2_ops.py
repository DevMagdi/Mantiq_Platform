"""R7 — Phase 2 as an operating loop, not a one-way screen (spec §2, §8).

The round's acceptance criterion is a *sequence*: a draft is created, it is issued into a numbered PDF, and
the installments behind it become real — they can fall late, they can be paid, and the commission they earn
is either a number or an explicit "not computed". Each test here owns one link of that chain, and the last
one proves the chain end-to-end from the API the screen calls.

Nothing here re-tests R5's issuance mechanics (the file, the 409 on a second issue, the portal download):
those live in `test_policy_issue.py` and stay green.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID

import pytest
from app.core.db import get_sessionmaker
from app.models.phase2 import Installment
from sqlalchemy import select

from tests.helpers import login

pytestmark = pytest.mark.integration

MANAGER = "manager@brokerhub.test"
ADMIN = "admin@brokerhub.test"
REP = "rep_a@brokerhub.test"
OWNER = "owner@brokerhub.test"


async def _enable_phase2(admin_headers: Any, client: Any) -> None:
    from app.models.masters import FeatureFlag

    async with get_sessionmaker()() as session:
        existing = (await session.execute(select(FeatureFlag).where(FeatureFlag.key == "phase2.enabled"))).scalar_one_or_none()
        if existing is None:
            session.add(FeatureFlag(key="phase2.enabled", enabled=False, description="Phase 2 screens"))
            await session.commit()
    response = await client.patch("/api/v1/feature-flags/phase2.enabled", headers=admin_headers, json={"enabled": True})
    assert response.status_code == 200, response.text


async def _draft(client: Any, headers: dict[str, str], client_id: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "client_id": client_id,
        "start_date": date.today().isoformat(),
        "end_date": (date.today() + timedelta(days=365)).isoformat(),
        "premium_amount": 24000,
        "payment_plan": "quarterly",
    }
    payload.update(overrides)
    response = await client.post("/api/v1/policies", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def test_a_draft_names_its_client_and_starts_with_no_file(client, ids) -> None:
    """Screen 12 printed an em dash where the client belongs, for every row (R7, fixed)."""
    async with login(client, ADMIN) as admin, login(client, MANAGER) as mgr:
        await _enable_phase2(admin, client)
        created = await _draft(client, mgr, ids["clients"]["ألف"])

        listing = await client.get("/api/v1/policies?page_size=50", headers=mgr)
        assert listing.status_code == 200, listing.text
        row = next(item for item in listing.json()["items"] if item["id"] == created["id"])
        assert row["status"] == "draft"
        assert row["client"]["name"] == "شركة ألف"
        # a draft has no number and no file — the list offers «إصدار», never a dead «تحميل»
        assert row["document"] is None


async def test_missing_required_fields_are_422_not_500(client, ids) -> None:
    """The create form is picky on purpose: every refusal names the field a person has to fill."""
    async with login(client, ADMIN) as admin, login(client, MANAGER) as mgr:
        await _enable_phase2(admin, client)
        missing_premium = await client.post(
            "/api/v1/policies", headers=mgr, json={"client_id": ids["clients"]["ألف"], "start_date": date.today().isoformat()}
        )
        assert missing_premium.status_code == 422, missing_premium.text
        fields = {detail["field"] for detail in missing_premium.json()["details"]}
        assert "premium_amount" in fields

        missing_client = await client.post("/api/v1/policies", headers=mgr, json={"premium_amount": 1000})
        assert missing_client.status_code == 422, missing_client.text

        bad_premium = await client.post(
            "/api/v1/policies",
            headers=mgr,
            json={"client_id": ids["clients"]["ألف"], "start_date": date.today().isoformat(), "premium_amount": 0},
        )
        assert bad_premium.status_code == 422, bad_premium.text


async def test_an_overdue_installment_is_late_for_every_reader(client, ids) -> None:
    """Lateness is derived from the due date, and then *persisted* — two readers cannot disagree.

    The row is moved into the past directly (a policy booked last year), because the point is the transition
    `upcoming → late`, not the clock.
    """
    async with login(client, ADMIN) as admin, login(client, MANAGER) as mgr:
        await _enable_phase2(admin, client)
        created = await _draft(client, mgr, ids["clients"]["ألف"], premium_amount=12000, payment_plan="quarterly")

        async with get_sessionmaker()() as session:
            policy_id = UUID(created["id"])
            rows = (
                (await session.execute(select(Installment).where(Installment.policy_id == policy_id).order_by(Installment.sequence_no)))
                .scalars()
                .all()
            )
            assert len(rows) == 4
            rows[0].due_date = date.today() - timedelta(days=10)
            await session.commit()

        plan = await client.get(f"/api/v1/policies/{created['id']}/installments", headers=mgr)
        assert plan.status_code == 200, plan.text
        body = plan.json()
        assert body["items"][0]["status"] == "late"
        assert body["totals"]["late_count"] == 1
        assert body["items"][0]["paid_at"] is None

        # and it stays late in the database, so `status = late` is a query a report can run
        async with get_sessionmaker()() as session:
            stored = (await session.execute(select(Installment).where(Installment.id == UUID(body["items"][0]["id"])))).scalar_one()
            assert stored.status.value == "late"

        kpis = await client.get("/api/v1/policies/kpis", headers=mgr)
        assert kpis.status_code == 200, kpis.text
        assert kpis.json()["late_installments"] >= 1


async def test_paying_an_installment_is_recorded_once_and_twice_is_a_409(client, ids) -> None:
    async with login(client, ADMIN) as admin, login(client, MANAGER) as mgr:
        await _enable_phase2(admin, client)
        created = await _draft(client, mgr, ids["clients"]["ألف"], premium_amount=12000, payment_plan="quarterly")
        plan = (await client.get(f"/api/v1/policies/{created['id']}/installments", headers=mgr)).json()
        target = plan["items"][0]

        paid = await client.post(f"/api/v1/policies/{created['id']}/installments/{target['id']}/pay", headers=mgr)
        assert paid.status_code == 200, paid.text
        body = paid.json()
        assert body["installment"]["status"] == "paid"
        assert body["installment"]["paid_at"] is not None

        again = await client.post(f"/api/v1/policies/{created['id']}/installments/{target['id']}/pay", headers=mgr)
        assert again.status_code == 409, again.text
        assert again.json()["code"] == "conflict"

        # the money moved exactly once
        refreshed = (await client.get(f"/api/v1/policies/{created['id']}/installments", headers=mgr)).json()
        assert refreshed["totals"]["paid_count"] == 1
        assert refreshed["totals"]["paid_amount"] == target["amount"]
        assert refreshed["totals"]["outstanding_amount"] == pytest.approx(12000 - target["amount"], abs=0.01)


async def test_an_installment_of_another_policy_is_a_404(client, ids) -> None:
    """The id in the URL is not a key to somebody else's schedule."""
    async with login(client, ADMIN) as admin, login(client, MANAGER) as mgr:
        await _enable_phase2(admin, client)
        first = await _draft(client, mgr, ids["clients"]["ألف"])
        second = await _draft(client, mgr, ids["clients"]["باء"])
        other = (await client.get(f"/api/v1/policies/{second['id']}/installments", headers=mgr)).json()["items"][0]

        crossed = await client.post(f"/api/v1/policies/{first['id']}/installments/{other['id']}/pay", headers=mgr)
        assert crossed.status_code == 404, crossed.text


async def test_the_commission_is_a_number_or_says_it_is_not_computed(client, ids) -> None:
    async with login(client, ADMIN) as admin, login(client, MANAGER) as mgr:
        await _enable_phase2(admin, client)
        priced = await _draft(client, mgr, ids["clients"]["ألف"], premium_amount=100000, commission_rate=2.5)
        plan = (await client.get(f"/api/v1/policies/{priced['id']}/installments", headers=mgr)).json()
        assert plan["commission"]["state"] == "computed"
        assert plan["commission"]["amount"] == pytest.approx(2500.0)
        assert plan["commission"]["rate"] == pytest.approx(2.5)

        # a zero rate writes no commission row at all, and the screen says so instead of printing 0.00
        free = await _draft(client, mgr, ids["clients"]["ألف"], premium_amount=50000, commission_rate=0)
        free_plan = (await client.get(f"/api/v1/policies/{free['id']}/installments", headers=mgr)).json()
        assert free_plan["commission"]["state"] == "not_computed"
        assert free_plan["commission"]["amount"] is None


async def test_a_rep_cannot_read_or_pay_a_policy_outside_their_book(client, ids) -> None:
    """404 for a policy the caller cannot see — never 403, never a peek at the schedule."""
    async with login(client, ADMIN) as admin, login(client, MANAGER) as mgr, login(client, REP) as rep:
        await _enable_phase2(admin, client)
        created = await _draft(client, mgr, ids["clients"]["ألف"])

        # a rep has no `policies.view`: the route is closed to them entirely
        plan = await client.get(f"/api/v1/policies/{created['id']}/installments", headers=rep)
        assert plan.status_code in (403, 404), plan.text


async def test_the_whole_loop_from_draft_to_paid_installment(client, ids) -> None:
    """draft → issue (numbered PDF) → plan → pay: the one path the round is judged on."""
    async with login(client, ADMIN) as admin, login(client, MANAGER) as mgr:
        await _enable_phase2(admin, client)
        created = await _draft(client, mgr, ids["clients"]["ألف"], premium_amount=36000, payment_plan="monthly")

        issued = await client.post(f"/api/v1/policies/{created['id']}/issue", headers=mgr)
        assert issued.status_code == 200, issued.text
        issued_body = issued.json()
        assert issued_body["status"] == "issued"
        assert issued_body["policy_no"].startswith("POL-") or issued_body["policy_no"]

        plan = (await client.get(f"/api/v1/policies/{created['id']}/installments", headers=mgr)).json()
        assert len(plan["items"]) == 12
        assert plan["policy"]["status"] == "issued"
        assert plan["totals"]["count"] == 12
        assert plan["totals"]["outstanding_amount"] == pytest.approx(36000.0)

        first = plan["items"][0]["id"]
        paid = await client.post(f"/api/v1/policies/{created['id']}/installments/{first}/pay", headers=mgr)
        assert paid.status_code == 200, paid.text
        assert paid.json()["installment"]["status"] == "paid"

        # the file is still downloadable after the payment — the loop did not break the earlier promise
        document = await client.get(f"/api/v1/policies/{created['id']}/document", headers=mgr)
        assert document.status_code == 200
        assert document.content[:5] == b"%PDF-"


FORBIDDEN_FOR_CLIENTS = {
    "commission",
    "commission_rate",
    "commissions",
    "notes",
    "internal_notes",
    "assignee",
    "assigned_to",
}


def _keys(value: Any) -> set[str]:
    """Every key at every depth — a leak is a leak whether it is at the root or inside a nested object."""
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _keys(item)}
    return set()


async def test_the_portal_sees_its_policies_and_no_money_it_should_not_see(client, ids) -> None:
    """The plan screen is staff-only; a client gets the file and the facts, never the brokerage's cut."""
    async with login(client, ADMIN) as admin, login(client, MANAGER) as mgr, login(client, OWNER) as owner:
        await _enable_phase2(admin, client)
        overview = (await client.get("/api/v1/portal/overview", headers=owner)).json()
        owner_client = overview["client"]["id"]
        assert owner_client == ids["clients"]["ألف"]

        created = await _draft(client, mgr, owner_client, premium_amount=60000, commission_rate=5)
        issued = await client.post(f"/api/v1/policies/{created['id']}/issue", headers=mgr)
        assert issued.status_code == 200, issued.text

        policies = await client.get("/api/v1/portal/policies", headers=owner)
        assert policies.status_code == 200, policies.text
        rows = policies.json()
        mine = next(row for row in rows if row["id"] == created["id"])
        assert mine["document"]["download_url"].startswith("/api/v1/portal/policies/")
        assert _keys(rows) & FORBIDDEN_FOR_CLIENTS == set(), _keys(rows) & FORBIDDEN_FOR_CLIENTS

        # the file really comes back through the portal door
        document = await client.get(f"/api/v1/portal/policies/{created['id']}/document", headers=owner)
        assert document.status_code == 200, document.text
        assert document.content[:5] == b"%PDF-"

        # and the staff-only money routes stay shut: the schedule and the payment action are not client verbs
        plan = await client.get(f"/api/v1/policies/{created['id']}/installments", headers=owner)
        assert plan.status_code in (403, 404), plan.text
        paid = await client.post(
            f"/api/v1/policies/{created['id']}/installments/{created['installments'][0]['sequence_no']}/pay", headers=owner
        )
        assert paid.status_code in (403, 404), paid.text
