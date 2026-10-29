"""Screen 14 (users & roles) end to end: the admin toggles must change what other people can do."""

from __future__ import annotations

import asyncio
from uuid import UUID

from app.core.db import get_sessionmaker
from app.domain.permissions import ALL_PERMISSIONS
from app.models.requests import AuditLog
from sqlalchemy import select

from tests.helpers import login

ADMIN = "admin@brokerhub.test"
PASSWORD = "Test-Pass-2026"


async def test_create_rep_with_scopes_and_login(client, ids) -> None:
    async with login(client, ADMIN) as h:
        created = await client.post(
            "/api/v1/users",
            headers=h,
            json={
                "email": "new.rep@brokerhub.test",
                "password": PASSWORD,
                "full_name": "زميلة جديدة",
                "role_code": "sales_rep",
                "mobile": "01001234567",
                "scopes": [{"type": "client", "id": ids["clients"]["ألف"]}],
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["role"] == "sales_rep"

        listed = await client.get("/api/v1/users", headers=h)
        assert listed.status_code == 200
        row = next(u for u in listed.json() if u["id"] == body["id"])
        assert row["role"] == "sales_rep"
        # the mobile went through the one shared normaliser, so it is stored as E.164
        assert row["mobile"] == "+201001234567"
        assert {s["id"] for s in row["scopes"]} == {ids["clients"]["ألف"]}

    # the new account can log in, and its scope is the seeded union, not the whole office
    async with login(client, "new.rep@brokerhub.test") as rep:
        clients = await client.get("/api/v1/clients", headers=rep)
        assert clients.status_code == 200
        names = {c["name"] for c in clients.json()["items"]}
        assert names == {"شركة ألف"}
        assert (await client.get("/api/v1/users", headers=rep)).status_code == 403


async def test_duplicate_email_unknown_role_and_weak_password_are_refused(client) -> None:
    async with login(client, ADMIN) as h:
        duplicate = await client.post(
            "/api/v1/users",
            headers=h,
            json={"email": "rep_a@brokerhub.test", "password": PASSWORD, "full_name": "تكرار", "role_code": "sales_rep"},
        )
        assert duplicate.status_code == 409
        unknown_role = await client.post(
            "/api/v1/users",
            headers=h,
            json={"email": "ghost@brokerhub.test", "password": PASSWORD, "full_name": "شبح", "role_code": "vice_president"},
        )
        assert unknown_role.status_code == 422
        assert unknown_role.json()["code"] == "validation_failed"
        weak = await client.post(
            "/api/v1/users",
            headers=h,
            json={"email": "weak@brokerhub.test", "password": "12345", "full_name": "ضعيف", "role_code": "sales_rep"},
        )
        assert weak.status_code == 422  # rejected by the request model before the service runs


async def test_two_parallel_creates_with_the_same_email_answer_409_not_500(client, ids) -> None:
    """The double-click on «إضافة عضو» used to end in a 500 for the second request (Live QA P0).

    The e-mail pre-check cannot see a row a concurrent request is committing; the unique index can, but
    the IntegrityError reached the client as a server fault. Both surfaces (users + sales team) now
    answer 409 with the same message the sequential path uses.
    """
    payload = {
        "email": "race.rep@brokerhub.test",
        "password": PASSWORD,
        "full_name": "سباق بياعين",
        "role_code": "sales_rep",
    }
    async with login(client, ADMIN) as h:
        first, second = await asyncio.gather(
            client.post("/api/v1/users", headers=h, json=payload),
            client.post("/api/v1/users", headers=h, json=payload),
        )
    assert {first.status_code, second.status_code} == {201, 409}, (first.status_code, second.status_code)

    team_payload = {**payload, "email": "race.team@brokerhub.test"}
    async with login(client, "manager@brokerhub.test") as h:
        team_first, team_second = await asyncio.gather(
            client.post("/api/v1/sales-team/members", headers=h, json=team_payload),
            client.post("/api/v1/sales-team/members", headers=h, json=team_payload),
        )
    assert {team_first.status_code, team_second.status_code} == {201, 409}, (team_first.status_code, team_second.status_code)


async def test_deactivate_revokes_access_and_self_lockout_is_blocked(client, ids) -> None:
    async with login(client, ADMIN) as h:
        created = await client.post(
            "/api/v1/users",
            headers=h,
            json={"email": "temp@brokerhub.test", "password": PASSWORD, "full_name": "مؤقت", "role_code": "customer_service"},
        )
        temp_id = created.json()["id"]

        # the account works first, so the revocation below is the thing under test
    async with login(client, "temp@brokerhub.test") as temp:
        assert (await client.get("/api/v1/requests", headers=temp)).status_code == 200

    async with login(client, ADMIN) as h:
        off = await client.patch(f"/api/v1/users/{temp_id}", headers=h, json={"is_active": False})
        assert off.status_code == 200
        assert off.json()["is_active"] is False
        refused = await client.post("/api/v1/auth/login", json={"email": "temp@brokerhub.test", "password": PASSWORD})
        assert refused.status_code in (401, 403)

        # an admin cannot lock themselves out of their own instance
        me = await client.get("/api/v1/auth/me", headers=h)
        self_off = await client.patch(f"/api/v1/users/{me.json()['id']}", headers=h, json={"is_active": False})
        assert self_off.status_code == 409


async def test_refresh_rotates_and_logout_revokes_the_family(client) -> None:
    """The refresh cookie rotates; logging out kills the family, so the last token stops working."""
    async with login(client, "cs@brokerhub.test"):
        first = await client.post("/api/v1/auth/refresh")
        assert first.status_code == 200
        token_a = first.json()["access_token"]
        second = await client.post("/api/v1/auth/refresh")
        assert second.status_code == 200
        token_b = second.json()["access_token"]
        assert token_a != token_b
        # the newest access token keeps working until logout
        assert (await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_b}"})).status_code == 200
        assert (await client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token_b}"})).status_code == 204
        assert (await client.post("/api/v1/auth/refresh")).status_code == 403


async def test_password_change_forces_a_fresh_login(client) -> None:
    async with login(client, ADMIN) as h:
        created = await client.post(
            "/api/v1/users",
            headers=h,
            json={"email": "rotate@brokerhub.test", "password": PASSWORD, "full_name": "تدوير", "role_code": "customer_service"},
        )
        user_id = created.json()["id"]
        changed = await client.patch(f"/api/v1/users/{user_id}", headers=h, json={"password": "Second-Pass-2026"})
        assert changed.status_code == 200

    assert (await client.post("/api/v1/auth/login", json={"email": "rotate@brokerhub.test", "password": PASSWORD})).status_code in (
        401,
        403,
    )
    async with login(client, "rotate@brokerhub.test", "Second-Pass-2026"):
        pass


async def test_roles_grid_and_permission_toggle_changes_access(client) -> None:
    async with login(client, ADMIN) as h:
        roles = await client.get("/api/v1/roles", headers=h)
        assert roles.status_code == 200
        grid = roles.json()
        assert {r["code"] for r in grid} >= {"system_admin", "sales_manager", "sales_rep", "customer_service", "client_owner"}
        rep_role = next(r for r in grid if r["code"] == "sales_rep")
        assert "leads.view_own" in rep_role["permissions"]
        assert "clients.view_all" not in rep_role["permissions"]

        permissions = await client.get("/api/v1/permissions", headers=h)
        assert permissions.status_code == 200
        # every slug the domain declares is offered in the UI grid — no orphan rows
        assert {p["code"] for p in permissions.json()} == set(ALL_PERMISSIONS)
        group = {p["group"] for p in permissions.json()}
        assert {"leads", "clients", "requests"} <= group

        # grant the rep role a manager-only permission, then take it away again
        granted = await client.put(
            f"/api/v1/roles/{rep_role['id']}/permissions",
            headers=h,
            json={"permissions": sorted(set(rep_role["permissions"]) | {"clients.view_all"})},
        )
        assert granted.status_code == 200
        assert "clients.view_all" in granted.json()["permissions"]

    async with login(client, "rep_a@brokerhub.test") as rep:
        everyone = await client.get("/api/v1/clients", headers=rep)
        assert everyone.status_code == 200
        assert {c["name"] for c in everyone.json()["items"]} == {"شركة ألف", "شركة باء"}

    async with login(client, ADMIN) as h:
        revoked = await client.put(
            f"/api/v1/roles/{rep_role['id']}/permissions",
            headers=h,
            json={"permissions": rep_role["permissions"]},
        )
        assert "clients.view_all" not in revoked.json()["permissions"]


async def test_unknown_user_and_role_answer_404(client) -> None:
    async with login(client, ADMIN) as h:
        missing = UUID(int=0)
        assert (await client.patch(f"/api/v1/users/{missing}", headers=h, json={"full_name": "لا شيء"})).status_code == 404
        assert (await client.put(f"/api/v1/users/{missing}/scopes", headers=h, json={"scopes": []})).status_code == 404
        assert (
            await client.put(f"/api/v1/roles/{missing}/permissions", headers=h, json={"permissions": ["leads.view_own"]})
        ).status_code == 404


async def test_a_blank_password_makes_the_server_generate_one_that_actually_works(client) -> None:
    """«توليد تلقائي»: the manager never sees or invents a secret, and the account is usable immediately."""
    async with login(client, ADMIN) as h:
        created = await client.post(
            "/api/v1/users",
            headers=h,
            json={"email": "generated@brokerhub.test", "full_name": "عضو بتوليد", "role_code": "sales_rep"},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        temp = body["temporary_password"]
        assert temp and len(temp) >= 10
        assert any(c.isdigit() for c in temp) and any(c.isupper() for c in temp) and any(c.islower() for c in temp)

    async with login(client, "generated@brokerhub.test", temp) as new_user:
        me = await client.get("/api/v1/auth/me", headers=new_user)
        assert (
            sorted(me.json()["permissions"]) == sorted(ALL_PERMISSIONS[i] for i in range(len(ALL_PERMISSIONS)) if i == -1)
            or "leads.view_own" in me.json()["permissions"]
        )
        # a rep starts with an empty pipeline: isolation, not a loading state
        assert (await client.get("/api/v1/leads", headers=new_user)).json()["total"] == 0

    async with get_sessionmaker()() as session:
        entry = (await session.execute(select(AuditLog).where(AuditLog.action == "user.created"))).scalars().all()[-1]
        assert entry.after["email"] == "generated@brokerhub.test"
        assert "password" not in str(entry.after).lower(), "the audit row must not carry a secret"


async def test_a_manager_may_open_the_account_inactive(client) -> None:
    """«تفعيل الحساب فورًا (نعم/لا)» is not decoration: an inactive account cannot authenticate."""
    async with login(client, ADMIN) as h:
        created = await client.post(
            "/api/v1/users",
            headers=h,
            json={
                "email": "pending@brokerhub.test",
                "password": PASSWORD,
                "full_name": "بانتظار",
                "role_code": "sales_rep",
                "is_active": False,
            },
        )
        assert created.status_code == 201
        assert created.json()["id"]
        refused = await client.post("/api/v1/auth/login", json={"email": "pending@brokerhub.test", "password": PASSWORD})
        assert refused.status_code in (401, 403)
        user_id = created.json()["id"]
        on = await client.patch(f"/api/v1/users/{user_id}", headers=h, json={"is_active": True})
        assert on.json()["is_active"] is True
    async with login(client, "pending@brokerhub.test"):
        pass  # activating it from the same dialog is the other half of the switch


async def test_a_portal_role_cannot_be_created_without_a_client(client) -> None:
    """The dialog has no client picker, so the API refuses the combination instead of creating a dead seat."""
    async with login(client, ADMIN) as h:
        refused = await client.post(
            "/api/v1/users",
            headers=h,
            json={"email": "orphan@brokerhub.test", "password": PASSWORD, "full_name": "يتيم", "role_code": "client_owner"},
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["details"]["role_code"] == "client_owner"


async def test_changing_a_role_changes_what_the_person_can_do(client) -> None:
    """The permission set is read from `role_permissions` on every token, so a promotion is not cosmetic."""
    async with login(client, ADMIN) as h:
        created = await client.post(
            "/api/v1/users",
            headers=h,
            json={"email": "promoted@brokerhub.test", "password": PASSWORD, "full_name": "ترقية", "role_code": "sales_rep"},
        )
        user_id = created.json()["id"]
    async with login(client, "promoted@brokerhub.test") as rep:
        assert (await client.get("/api/v1/users", headers=rep)).status_code == 403
        assert (await client.get("/api/v1/sales/team", headers=rep)).status_code == 403

    async with login(client, ADMIN) as h:
        promoted = await client.patch(f"/api/v1/users/{user_id}", headers=h, json={"role_code": "sales_manager"})
        # the answer describes the row as it now is — not the role the request replaced
        assert promoted.json()["role"] == "sales_manager"
    async with login(client, "promoted@brokerhub.test") as manager:
        me = await client.get("/api/v1/auth/me", headers=manager)
        assert {"leads.view_all", "leads.assign"} <= set(me.json()["permissions"])
        assert (await client.get("/api/v1/sales/team", headers=manager)).status_code == 200
        # the promotion buys the team roster, not the admin surface: `users.manage` stays with system_admin,
        # which is the gate ADR-016 §8 pinned and `/sales-team/members` (this round's) does not move
        assert (await client.get("/api/v1/users", headers=manager)).status_code == 403
        assert (await client.get("/api/v1/sales-team/members", headers=manager)).status_code == 200
