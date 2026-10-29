"""Client-file writes (screens 5-6): owner defaults, contact primacy, insured edits, audit trail."""

from __future__ import annotations

from uuid import UUID

from app.models.requests import AuditLog
from sqlalchemy import select

from tests.helpers import login

MANAGER = "manager@brokerhub.test"
REP_A = "rep_a@brokerhub.test"
REP_B = "rep_b@brokerhub.test"
CS = "cs@brokerhub.test"


async def _audit_rows(action: str) -> list[dict]:
    from app.core.db import get_sessionmaker

    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.occurred_at))).scalars().all()
        return [{"entity_id": str(r.entity_id), "before": r.before, "after": r.after} for r in rows]


async def test_create_client_defaults_the_owner_to_the_actor(client) -> None:
    async with login(client, REP_A) as h:
        created = await client.post("/api/v1/clients", headers=h, json={"name": "شركة جيم", "type": "company", "tax_number": "512345678"})
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["name"] == "شركة جيم"
        assert body["account_owner_id"]  # falls back to the acting rep, not to the manager

        detail = await client.get(f"/api/v1/clients/{body['id']}", headers=h)
        assert detail.status_code == 200
        overview = await client.get(f"/api/v1/clients/{body['id']}/overview", headers=h)
        assert overview.status_code == 200
        kpis = overview.json()["kpis"]
        # a brand new client file renders zeros, never blanks or errors
        assert kpis["insured"] == 0 and kpis["open_requests"] == 0
        assert overview.json()["policy"] is None
        assert overview.json()["kpis"]["renewal_in_days"] is None

    written = await _audit_rows("client.created")
    assert any(row["entity_id"] == body["id"] for row in written)


async def test_only_roles_with_clients_manage_may_create(client) -> None:
    async with login(client, CS) as h:
        refused = await client.post("/api/v1/clients", headers=h, json={"name": "شركة ممنوعة"})
        assert refused.status_code == 403
        assert refused.json()["code"] == "forbidden"


async def test_update_is_scoped_and_audited(client, ids) -> None:
    async with login(client, REP_B) as h:
        foreign = await client.patch(f"/api/v1/clients/{ids['clients']['ألف']}", headers=h, json={"name": "سرقة اسم"})
        assert foreign.status_code == 404  # another rep's client is absent, not forbidden

    async with login(client, REP_A) as h:
        patched = await client.patch(
            f"/api/v1/clients/{ids['clients']['ألف']}", headers=h, json={"name": "شركة ألف للتجارة", "status": "active"}
        )
        assert patched.status_code == 200
        assert patched.json()["name"] == "شركة ألف للتجارة"
    changed = await _audit_rows("client.updated")
    assert changed and changed[-1]["before"]["name"] == "شركة ألف"


async def test_contacts_primary_flag_moves_to_the_new_one(client, ids) -> None:
    client_id = ids["clients"]["ألف"]
    async with login(client, REP_A) as h:
        first = await client.post(
            f"/api/v1/clients/{client_id}/contacts",
            headers=h,
            json={"name": "منى فؤاد", "title": "HR", "mobile": "01001112222", "email": "mona@alfa.test", "is_primary": True},
        )
        assert first.status_code == 201, first.text
        assert first.json()["mobile"] == "+201001112222"  # stored through the shared E.164 util

        second = await client.post(
            f"/api/v1/clients/{client_id}/contacts", headers=h, json={"name": "أحمد", "title": "مشتريات", "is_primary": True}
        )
        assert second.status_code == 201

        listed = (await client.get(f"/api/v1/clients/{client_id}/contacts", headers=h)).json()
        assert len(listed) == 2
        assert {c["id"]: c["is_primary"] for c in listed}[second.json()["id"]] is True
        assert {c["id"]: c["is_primary"] for c in listed}[first.json()["id"]] is False

        overview = (await client.get(f"/api/v1/clients/{client_id}/overview", headers=h)).json()
        assert overview["contacts"][0]["name"] in {"منى فؤاد", "أحمد"}


async def test_insured_add_update_and_counts(client, ids) -> None:
    client_id = ids["clients"]["ألف"]
    async with login(client, REP_A) as h:
        added = await client.post(
            f"/api/v1/clients/{client_id}/insured",
            headers=h,
            json={
                "name": "هالة عبد الرحمن",
                "mobile": "01001234567",
                "national_id": "29105101203458",
                "department": "الحسابات",
                "job_title": "محاسبة",
                "coverage_status": "pending",
                "notes": "تحتاج شهادة راتب",
                "external_ref": "EMP-4501",
            },
        )
        assert added.status_code == 201, added.text
        insured_id = added.json()["id"]
        assert added.json()["coverage_status"] == "pending"
        assert added.json()["notes"] == "تحتاج شهادة راتب"
        assert added.json()["external_ref"] == "EMP-4501"

        listed = await client.get(f"/api/v1/clients/{client_id}/insured", headers=h)
        assert listed.status_code == 200
        page = listed.json()
        assert page["page"]["total"] == 1
        assert page["kpis"]["pending"] == 1

        patched = await client.patch(
            f"/api/v1/insured/{insured_id}",
            headers=h,
            json={"coverage_status": "active", "department": "المراجعة", "coverage_end": "2026-12-31"},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["coverage_status"] == "active"
        assert patched.json()["coverage_end"] == "2026-12-31"

        after = (await client.get(f"/api/v1/clients/{client_id}/insured", headers=h)).json()
        assert after["kpis"]["active"] == 1 and after["kpis"]["pending"] == 0

        # out of scope for the other rep, and unknown ids are absent
    async with login(client, REP_B) as h:
        assert (await client.get(f"/api/v1/clients/{client_id}/insured", headers=h)).status_code == 404
        assert (await client.patch(f"/api/v1/insured/{UUID(int=0)}", headers=h, json={"department": "x"})).status_code == 404


async def test_client_list_search_and_filters(client, ids) -> None:
    async with login(client, MANAGER) as h:
        assert (await client.get("/api/v1/clients", headers=h, params={"q": "ألف"})).json()["total"] == 1
        assert (await client.get("/api/v1/clients", headers=h, params={"q": "مفيش كده"})).json()["total"] == 0
        assert (await client.get("/api/v1/clients", headers=h, params={"status": "active"})).json()["total"] == 2
        converted = (await client.get("/api/v1/clients", headers=h, params={"only_converted": True})).json()
        assert converted["total"] == 0
        kpis = (await client.get("/api/v1/clients/kpis", headers=h)).json()
        assert kpis["total_clients"] == 2


async def test_the_overview_of_a_client_that_has_a_policy_serialises_its_insurance_type(client, ids: dict[str, dict[str, str]]) -> None:
    """Screen 5 must render for a client that actually bought something.

    The overview used to answer **500** for every such client: the policy block emitted
    `insurance_type: {code, name}` while the response schema is `{id, code, name}`, so FastAPI's
    serialisation refused the whole payload. No test ever created a policy before reading the overview,
    which is why only the e2e screen grid caught it (and it read the failure as "the page did not load").
    """
    from datetime import date
    from decimal import Decimal
    from uuid import UUID

    from app.core.db import get_sessionmaker
    from app.domain.enums import PaymentPlan, PolicyStatus
    from app.models.masters import InsuranceType
    from app.models.phase2 import Policy
    from sqlalchemy import select

    async with get_sessionmaker()() as session:
        type_id = (await session.execute(select(InsuranceType.id).limit(1))).scalar_one()
        session.add(
            Policy(
                client_id=UUID(ids["clients"]["ألف"]),
                insurance_type_id=type_id,
                policy_no="GRP-R3-0001",
                start_date=date(2026, 1, 1),
                end_date=date(2026, 12, 31),
                premium_amount=Decimal("240000.00"),
                payment_plan=PaymentPlan.QUARTERLY,
                commission_rate=Decimal("5.00"),
                status=PolicyStatus.ISSUED,
                headcount=120,
            )
        )
        await session.commit()

    async with login(client, MANAGER) as h:
        overview = await client.get(f"/api/v1/clients/{ids['clients']['ألف']}/overview", headers=h)
        assert overview.status_code == 200, overview.text
        policy = overview.json()["policy"]
        assert policy is not None
        assert policy["insurance_type"]["id"] == str(type_id)
        assert policy["insurance_type"]["code"]
        assert policy["insurance_type"]["name"]
        assert overview.json()["kpis"]["renewal_in_days"] is not None


async def test_a_draft_policy_without_a_number_still_opens_the_client_file(client, ids: dict[str, dict[str, str]]) -> None:
    """
    A policy booked before the insurer issues a number is legal: `PolicyCreate.policy_no` defaults to null and
    the column is nullable. `PolicyOut` declared it `str`, so FastAPI refused the *whole* overview with a
    `ResponseValidationError` — a 500 on the screen the design review photographs, for any client holding one
    such draft (R3: the e2e `screens` grid failed on both projects and the two client-file screenshots went
    with it). The contract is "the number is optional, the file still opens".
    """
    from datetime import date
    from decimal import Decimal
    from uuid import UUID

    from app.core.db import get_sessionmaker
    from app.domain.enums import PaymentPlan, PolicyStatus
    from app.models.phase2 import Policy

    async with get_sessionmaker()() as session:
        session.add(
            Policy(
                client_id=UUID(ids["clients"]["باء"]),
                policy_no=None,
                start_date=date(2026, 2, 1),
                end_date=date(2027, 1, 31),
                premium_amount=Decimal("90000.00"),
                payment_plan=PaymentPlan.MONTHLY,
                commission_rate=Decimal("5.00"),
                status=PolicyStatus.DRAFT,
            )
        )
        await session.commit()

    async with login(client, MANAGER) as h:
        overview = await client.get(f"/api/v1/clients/{ids['clients']['باء']}/overview", headers=h)
        assert overview.status_code == 200, overview.text
        body = overview.json()
        assert body["policy"] is not None
        assert body["policy"]["policy_no"] is None
        assert body["policy"]["status"] == "draft"
