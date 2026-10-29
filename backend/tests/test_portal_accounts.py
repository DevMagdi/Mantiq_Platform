"""Portal accounts and the portal's view of issued policies (ADR-022, spec §6).

Two things are proved here that the product could not do before R5: a client owner can sign in with a
*username* (they are a company contact, not a corporate mailbox), and the portal can list and download the
policy documents the broker has issued to their company — and only theirs.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from app.core.db import get_sessionmaker

from tests.helpers import PASSWORD, login

pytestmark = pytest.mark.integration

ADMIN = "admin@brokerhub.test"
MANAGER = "manager@brokerhub.test"
OWNER = "owner@brokerhub.test"
REP = "rep_a@brokerhub.test"


async def _phase2_on(client, admin_headers) -> None:
    from app.models.masters import FeatureFlag
    from sqlalchemy import select

    async with get_sessionmaker()() as session:
        exists = (await session.execute(select(FeatureFlag).where(FeatureFlag.key == "phase2.enabled"))).scalar_one_or_none()
        if exists is None:
            session.add(FeatureFlag(key="phase2.enabled", enabled=False, description="Phase 2 screens"))
            await session.commit()
    response = await client.patch("/api/v1/feature-flags/phase2.enabled", headers=admin_headers, json={"enabled": True})
    assert response.status_code == 200, response.text


async def test_a_username_only_portal_account_logs_in_and_stays_inside_its_company(client, ids) -> None:
    username = "alf.owner"
    async with login(client, MANAGER) as mgr:
        created = await client.post(
            f"/api/v1/clients/{ids['clients']['ألف']}/portal-accounts",
            json={"full_name": "مالك شركة ألف", "username": username, "role_code": "client_owner"},
            headers=mgr,
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["username"] == username
        assert body["portal_role"] == "owner"
        # the mailbox is a local handle, generated so the row keeps a unique key
        assert body["email"] == f"{username}@portal.brokerhub.local"
        temp = body["temporary_password"]
        assert temp and len(temp) >= 10, "a temporary password must be shown once, and be usable"

        listed = await client.get(f"/api/v1/clients/{ids['clients']['ألف']}/portal-accounts", headers=mgr)
        assert listed.status_code == 200
        row = next(item for item in listed.json() if item["id"] == body["id"])
        assert "temporary_password" not in row, "the temp password is returned once, never replayed"

        # the username is taken now
        dup = await client.post(f"/api/v1/clients/{ids['clients']['ألف']}/portal-accounts", json={"username": username}, headers=mgr)
        assert dup.status_code == 409, dup.text

    # sign in with the username, not the generated mailbox
    signed = await client.post("/api/v1/auth/login", json={"email": username, "password": temp})
    assert signed.status_code == 200, signed.text
    token = {"Authorization": f"Bearer {signed.json()['access_token']}"}
    me = await client.get("/api/v1/portal/overview", headers=token)
    assert me.status_code == 200
    assert me.json()["client"]["id"] == ids["clients"]["ألف"]

    # a reset invalidates the old password and returns the new one once
    async with login(client, MANAGER) as mgr:
        reset = await client.post(f"/api/v1/clients/{ids['clients']['ألف']}/portal-accounts/{body['id']}/password", json={}, headers=mgr)
        assert reset.status_code == 200, reset.text
        new_temp = reset.json()["temporary_password"]
        assert new_temp and new_temp != temp
        assert set(reset.json()) == {"id", "username", "temporary_password"}
    assert (await client.post("/api/v1/auth/login", json={"email": username, "password": temp})).status_code == 401
    assert (await client.post("/api/v1/auth/login", json={"email": username, "password": new_temp})).status_code == 200

    # deactivation refuses the login instead of deleting history
    async with login(client, MANAGER) as mgr:
        off = await client.patch(
            f"/api/v1/clients/{ids['clients']['ألف']}/portal-accounts/{body['id']}", json={"is_active": False}, headers=mgr
        )
        assert off.status_code == 200, off.text
        assert off.json()["is_active"] is False
    assert (await client.post("/api/v1/auth/login", json={"email": username, "password": new_temp})).status_code == 403


async def test_portal_account_management_respects_client_scope_and_username_rules(client, ids) -> None:
    """A rep holds `clients.manage` — so the guard that matters is the client scope, not the role name.

    شركة ألف belongs to rep_a, شركة باء does not: the second is a 404 (never "forbidden", which would confirm
    the client exists in someone else's book).
    """
    async with login(client, REP) as rep:
        own = await client.get(f"/api/v1/clients/{ids['clients']['ألف']}/portal-accounts", headers=rep)
        assert own.status_code == 200, own.text
        foreign = await client.get(f"/api/v1/clients/{ids['clients']['باء']}/portal-accounts", headers=rep)
        assert foreign.status_code == 404, foreign.text
        blocked = await client.post(f"/api/v1/clients/{ids['clients']['باء']}/portal-accounts", json={"username": "rep.made"}, headers=rep)
        assert blocked.status_code == 404, blocked.text
    async with login(client, MANAGER) as mgr:
        # a client that is not in the manager's scope is a 404, not an empty list
        foreign = await client.get("/api/v1/clients/00000000-0000-4000-8000-000000000000/portal-accounts", headers=mgr)
        assert foreign.status_code == 404
        bad = await client.post(f"/api/v1/clients/{ids['clients']['ألف']}/portal-accounts", json={"username": "العميل"}, headers=mgr)
        assert bad.status_code == 422, bad.text


async def test_portal_lists_and_downloads_only_its_own_issued_policies(client, ids) -> None:
    async with login(client, ADMIN) as admin, login(client, MANAGER) as mgr:
        await _phase2_on(client, admin)
        mine = await client.post(
            "/api/v1/policies",
            json={
                "client_id": ids["clients"]["ألف"],
                "start_date": date.today().isoformat(),
                "end_date": (date.today() + timedelta(days=365)).isoformat(),
                "premium_amount": 9000,
            },
            headers=mgr,
        )
        assert mine.status_code == 201, mine.text
        theirs = await client.post(
            "/api/v1/policies",
            json={
                "client_id": ids["clients"]["باء"],
                "start_date": date.today().isoformat(),
                "premium_amount": 4000,
            },
            headers=mgr,
        )
        assert theirs.status_code == 201, theirs.text
        issued = await client.post(f"/api/v1/policies/{mine.json()['id']}/issue", headers=mgr)
        assert issued.status_code == 200, issued.text
        await client.post(f"/api/v1/policies/{theirs.json()['id']}/issue", headers=mgr)

    async with login(client, OWNER) as owner:
        listed = await client.get("/api/v1/portal/policies", headers=owner)
        assert listed.status_code == 200, listed.text
        rows = listed.json()
        seen = {row["id"] for row in rows}
        assert mine.json()["id"] in seen
        assert theirs.json()["id"] not in seen
        row = next(item for item in rows if item["id"] == mine.json()["id"])
        assert row["status"] == "issued"
        assert row["policy_no"] == issued.json()["policy_no"]
        assert row["document"]["download_url"] == f"/api/v1/portal/policies/{row['id']}/document"
        assert row["document"]["filename"].endswith(".pdf")
        assert "commission" not in row and "assigned_to" not in row

        download = await client.get(f"/api/v1/portal/policies/{mine.json()['id']}/document", headers=owner)
        assert download.status_code == 200
        assert download.content.startswith(b"%PDF-")
        # the other company's file is "not here", never 403 — the portal does not confirm existence
        assert (await client.get(f"/api/v1/portal/policies/{theirs.json()['id']}/document", headers=owner)).status_code == 404

    # and a staff user has no portal surface at all
    async with login(client, MANAGER) as mgr:
        assert (await client.get("/api/v1/portal/policies", headers=mgr)).status_code == 403


async def test_existing_owner_password_login_still_works(client) -> None:
    """The username change must not touch the email path — that would break every existing owner."""
    signed = await client.post("/api/v1/auth/login", json={"email": OWNER, "password": PASSWORD})
    assert signed.status_code == 200, signed.text
