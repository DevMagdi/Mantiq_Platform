"""Screen 6's team tab: a sales manager can add and move office seats, and nothing else."""

from __future__ import annotations

from typing import Any

from tests.helpers import login

MANAGER = "manager@brokerhub.test"
ADMIN = "admin@brokerhub.test"
REP_A = "rep_a@brokerhub.test"
PASSWORD = "Test-Pass-2026"


async def _roster(client: Any, auth: dict[str, str]) -> dict[str, Any]:
    return (await client.get("/api/v1/sales-team/members", headers=auth)).json()  # type: ignore[no-any-return]


async def _member(client: Any, auth: dict[str, str], email: str) -> dict[str, Any]:
    rows = (await _roster(client, auth))["members"]
    return next(row for row in rows if row["email"] == email)


async def test_a_manager_adds_a_rep_who_starts_empty(client) -> None:
    async with login(client, MANAGER) as auth:
        created = await client.post(
            "/api/v1/sales-team/members",
            headers=auth,
            json={"full_name": "بياع جديد", "email": "fresh.rep@brokerhub.test", "role_code": "sales_rep", "mobile": "01112223344"},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["role"] == "sales_rep"
        assert body["is_active"] is True
        temp = body["temporary_password"]
        assert temp and len(temp) >= 10

        roles = {row["code"]: row["name"] for row in (await _roster(client, auth))["assignable_roles"]}
        # the dialog's options come from the API, so what it offers is what the service accepts
        assert set(roles) == {"sales_rep", "sales_manager", "customer_service", "account_manager"}
        assert roles["sales_rep"] == "بياع"

        row = await _member(client, auth, "fresh.rep@brokerhub.test")
        assert row["role_name"] == "بياع"  # the badge the list shows comes from the role row, not the slug
        assert row["mobile"] == "+201112223344"
        assert row["open_leads"] == 0

        duplicate = await client.post(
            "/api/v1/sales-team/members",
            headers=auth,
            json={"full_name": "تكرار", "email": "FRESH.rep@brokerhub.test", "role_code": "sales_rep"},
        )
        assert duplicate.status_code == 409, duplicate.text

    # the new seat works, and it starts with no leads: isolation is the default, not a filter
    async with login(client, "fresh.rep@brokerhub.test", temp) as fresh:
        me = (await client.get("/api/v1/auth/me", headers=fresh)).json()
        assert "leads.view_own" in me["permissions"] and "leads.view_all" not in me["permissions"]
        assert (await client.get("/api/v1/leads", headers=fresh)).json()["total"] == 0
        assert (await client.get("/api/v1/clients", headers=fresh)).json()["total"] == 0


async def test_a_rep_cannot_use_the_team_tab(client) -> None:
    async with login(client, REP_A) as auth:
        listed = await client.get("/api/v1/sales-team/members", headers=auth)
        assert listed.status_code == 403, listed.text
        refused = await client.post(
            "/api/v1/sales-team/members",
            headers=auth,
            json={"full_name": "تسريب", "email": "sneaky@brokerhub.test", "role_code": "sales_rep"},
        )
        assert refused.status_code == 403
        assert set(refused.json()["details"]["missing"]) == {"leads.assign"}


async def test_the_tab_cannot_mint_an_admin_or_a_portal_login(client) -> None:
    async with login(client, MANAGER) as auth:
        for role in ("system_admin", "client_owner"):
            refused = await client.post(
                "/api/v1/sales-team/members",
                headers=auth,
                json={"full_name": f"{role} مزيف", "email": f"{role}@victim.test", "role_code": role},
            )
            assert refused.status_code == 403, (role, refused.text)
            assert refused.json()["details"]["allowed"] == ["account_manager", "customer_service", "sales_manager", "sales_rep"]
        # and it cannot demote the one admin that exists: the roster does not contain them
        async with login(client, ADMIN) as admin:
            admin_row = (await client.get("/api/v1/users", headers=admin)).json()[0]
        outside = await client.patch(f"/api/v1/sales-team/members/{admin_row['id']}", headers=auth, json={"is_active": False})
        assert outside.status_code == 404, outside.text


async def test_a_seat_can_be_moved_and_switched_off(client) -> None:
    async with login(client, MANAGER) as auth:
        created = await client.post(
            "/api/v1/sales-team/members",
            headers=auth,
            json={"full_name": "زميلة متنقلة", "email": "moving@brokerhub.test", "role_code": "sales_rep", "password": PASSWORD},
        )
        member_id = created.json()["id"]
        row = await _member(client, auth, "moving@brokerhub.test")
        assert row["is_active"] is True

    async with login(client, "moving@brokerhub.test") as mover:
        assert (await client.get("/api/v1/leads", headers=mover)).status_code == 200

    async with login(client, MANAGER) as auth:
        moved = await client.patch(f"/api/v1/sales-team/members/{member_id}", headers=auth, json={"role_code": "customer_service"})
        assert moved.status_code == 200, moved.text
        assert moved.json()["role"] == "customer_service"
    async with login(client, "moving@brokerhub.test") as mover:
        # a CS agent reads the inbox but has no business in the pipeline
        assert (await client.get("/api/v1/requests", headers=mover)).status_code == 200
        assert (await client.get("/api/v1/leads", headers=mover)).status_code == 403

    async with login(client, MANAGER) as auth:
        off = await client.patch(f"/api/v1/sales-team/members/{member_id}", headers=auth, json={"is_active": False})
        assert off.json()["is_active"] is False
    assert (await client.post("/api/v1/auth/login", json={"email": "moving@brokerhub.test", "password": PASSWORD})).status_code in (
        401,
        403,
    )
    async with login(client, MANAGER) as auth:
        on = await client.patch(f"/api/v1/sales-team/members/{member_id}", headers=auth, json={"is_active": True})
        assert on.json()["is_active"] is True
    async with login(client, "moving@brokerhub.test"):
        pass  # and the switch is reversible from the same dialog


async def test_the_roster_counts_what_a_redistribution_handed_out(client, ids: dict[str, dict[str, str]]) -> None:
    """The two halves of this round meet here: imported rows, then a redistribution, then the load per seat."""
    from openpyxl import Workbook

    wb = Workbook()
    sheet = wb.active
    sheet.append(["الاسم", "الموبايل", "الشركة"])
    for n in range(4):
        sheet.append([f"مستورد {n}", f"01055{n:06d}", f"شركة {n}"])
    import io

    buffer = io.BytesIO()
    wb.save(buffer)

    async with login(client, MANAGER) as auth:
        uploaded = await client.post(
            "/api/v1/leads/import",
            headers=auth,
            files={"file": ("roster.xlsx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert uploaded.status_code == 201, uploaded.text
        job = uploaded.json()
        assert job["stats"]["unassigned"] == 4
        await client.post(f"/api/v1/leads/import/{job['id']}/commit", headers=auth)

        before = await _member(client, auth, REP_A)
        assert before["open_leads"] == 1  # the fixture lead

        moved = await client.post(
            "/api/v1/leads/assign-bulk", json={"unassigned_only": True, "to": [ids["users"]["rep_a"], ids["users"]["rep_b"]]}, headers=auth
        )
        assert moved.status_code == 200, moved.text
        assert sorted(moved.json()["per_rep"].values()) == [2, 2]

        after_a = await _member(client, auth, REP_A)
        assert after_a["open_leads"] == before["open_leads"] + 2


async def test_a_duplicate_email_is_a_conflict_not_a_second_seat(client) -> None:
    async with login(client, MANAGER) as auth:
        duplicate = await client.post(
            "/api/v1/sales-team/members",
            headers=auth,
            json={"full_name": "نسخة من ريب A", "email": "rep_a@brokerhub.test", "role_code": "sales_rep"},
        )
        assert duplicate.status_code == 409, duplicate.text
        assert duplicate.json()["code"] == "conflict"
        roster = (await client.get("/api/v1/sales-team/members", headers=auth)).json()
        assert len([row for row in roster["members"] if row["email"] == "rep_a@brokerhub.test"]) == 1


async def test_a_suspended_seat_cannot_log_in_and_the_answer_is_a_forbidden(client) -> None:
    """The login is the real gate: `is_active` must stop the session, with the error code the UI branches on."""
    async with login(client, MANAGER) as auth:
        created = await client.post(
            "/api/v1/sales-team/members",
            headers=auth,
            json={"full_name": "مقعد يوقف", "email": "suspended@brokerhub.test", "role_code": "sales_rep", "password": PASSWORD},
        )
        assert created.status_code == 201, created.text
        member_id = created.json()["id"]
        off = await client.patch(f"/api/v1/sales-team/members/{member_id}", headers=auth, json={"is_active": False})
        assert off.status_code == 200, off.text
    refused = await client.post("/api/v1/auth/login", json={"email": "suspended@brokerhub.test", "password": PASSWORD})
    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "forbidden"
    async with login(client, MANAGER) as auth:
        on = await client.patch(f"/api/v1/sales-team/members/{member_id}", headers=auth, json={"is_active": True})
        assert on.status_code == 200
    async with login(client, "suspended@brokerhub.test"):
        pass  # the switch is reversible and the login comes back


async def test_a_new_rep_is_isolated_from_everybody_elses_leads(client, ids: dict[str, dict[str, str]]) -> None:
    """A seat created from the team screen must inherit isolation immediately — 404 by id, not 403."""
    foreign_lead = ids["leads"]["ألف"]
    async with login(client, MANAGER) as auth:
        created = await client.post(
            "/api/v1/sales-team/members",
            headers=auth,
            json={"full_name": "عضو جديد", "email": "isolated@brokerhub.test", "role_code": "sales_rep", "password": PASSWORD},
        )
        assert created.status_code == 201, created.text
    async with login(client, "isolated@brokerhub.test") as mine:
        assert (await client.get(f"/api/v1/leads/{foreign_lead}", headers=mine)).status_code == 404
        assert (await client.get(f"/api/v1/leads/{foreign_lead}/activities", headers=mine)).status_code == 404
        assert (await client.patch(f"/api/v1/leads/{foreign_lead}", headers=mine, json={"status": "lost"})).status_code == 404
        listing = (await client.get("/api/v1/leads?page_size=200", headers=mine)).json()
        assert listing["total"] == 0
        assert (await client.get("/api/v1/requests?page_size=1", headers=mine)).status_code == 403
        assert (await client.get("/api/v1/dashboard/summary", headers=mine)).status_code == 403


async def test_the_roster_exposes_the_lead_owning_flag(client) -> None:
    async with login(client, MANAGER) as auth:
        roster = (await client.get("/api/v1/sales-team/members", headers=auth)).json()
    rep = next(row for row in roster["members"] if row["role"] == "sales_rep")
    cs = next((row for row in roster["members"] if row["role"] == "customer_service"), None)
    assert rep["accepts_leads"] is True
    if cs is not None:
        assert cs["accepts_leads"] is False


async def test_the_role_list_offers_the_rep_seat_first(client) -> None:
    """
    The dialog seeds its role field from `assignable_roles[0]`. The list used to be alphabetical, so the
    default seat was `account_manager` — a manager who pressed «إضافة عضو» and typed a name created an
    account manager, who then could not hold a lead (R3, `team-round.spec.ts` desktop: `seat.role` was
    `account_manager`). The order is a product decision now, not a collation accident.
    """
    async with login(client, MANAGER) as auth:
        roster = (await client.get("/api/v1/sales-team/members", headers=auth)).json()
    codes = [row["code"] for row in roster["assignable_roles"]]
    assert codes[0] == "sales_rep", codes
    assert codes == ["sales_rep", "sales_manager", "customer_service", "account_manager"], codes
    # every role named here is one the roster actually contains, so the dialog cannot offer a dead option
    assert {row["role"] for row in roster["members"]} <= set(codes)
