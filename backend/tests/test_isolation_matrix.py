"""The mandatory security matrix (spec §6). Rep A vs rep B, manager, portal, re-assignment."""

from __future__ import annotations

import pytest

from tests.helpers import login

pytestmark = pytest.mark.integration


async def test_rep_sees_only_own_leads(client, ids) -> None:
    async with login(client, "rep_a@brokerhub.test") as h:
        body = (await client.get("/api/v1/leads", headers=h)).json()
        names = [item["name"] for item in body["items"]]
        assert "ليد ألف" in names
        assert "ليد باء" not in names
        assert body["total"] == 1


async def test_out_of_scope_read_is_404_not_403(client, ids) -> None:
    async with login(client, "rep_a@brokerhub.test") as h:
        response = await client.get(f"/api/v1/leads/{ids['leads']['باء']}", headers=h)
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"


async def test_out_of_scope_write_is_404(client, ids) -> None:
    async with login(client, "rep_a@brokerhub.test") as h:
        response = await client.patch(f"/api/v1/leads/{ids['leads']['باء']}", headers=h, json={"name": "hijacked"})
        assert response.status_code == 404
    async with login(client, "rep_b@brokerhub.test") as h:
        mine = (await client.get(f"/api/v1/leads/{ids['leads']['باء']}", headers=h)).json()
        assert mine["name"] == "ليد باء"


async def test_rep_cannot_reassign(client, ids) -> None:
    async with login(client, "rep_a@brokerhub.test") as h:
        response = await client.post(f"/api/v1/leads/{ids['leads']['ألف']}/assign", headers=h, json={"assigned_to": ids["users"]["rep_b"]})
        assert response.status_code == 403


async def test_manager_assigns_and_it_is_audited_on_the_timeline(client, ids) -> None:
    async with login(client, "manager@brokerhub.test") as h:
        assigned = await client.get(f"/api/v1/leads/{ids['leads']['باء']}", headers=h)
        assert assigned.status_code == 200
        response = await client.post(f"/api/v1/leads/{ids['leads']['باء']}/assign", headers=h, json={"assigned_to": ids["users"]["rep_a"]})
        assert response.status_code == 200
        assert response.json()["assignee_id"] == ids["users"]["rep_a"]
        activities = (await client.get(f"/api/v1/leads/{ids['leads']['باء']}/activities", headers=h)).json()
        assert any(a["type"] == "assignment" for a in activities), "re-assignment must be recorded"


async def test_manager_sees_everything(client, ids) -> None:
    async with login(client, "manager@brokerhub.test") as h:
        body = (await client.get("/api/v1/leads", headers=h)).json()
        assert {"ليد ألف", "ليد باء"} <= {i["name"] for i in body["items"]}


async def test_board_stats_and_kanban_respect_scope(client, ids) -> None:
    async with login(client, "rep_b@brokerhub.test") as h:
        board = (await client.get("/api/v1/leads/board", headers=h)).json()
        in_board = [i["name"] for col in board["columns"] for i in col["items"]]
        assert "ليد باء" in in_board and "ليد ألف" not in in_board
        stats = (await client.get("/api/v1/leads/stats", headers=h)).json()
        assert stats["my_leads"] == 1
    async with login(client, "manager@brokerhub.test") as h:
        stats = (await client.get("/api/v1/leads/stats", headers=h)).json()
        assert stats["my_leads"] == 2


async def test_rep_sees_only_own_clients_and_their_requests(client, ids) -> None:
    async with login(client, "rep_a@brokerhub.test") as h:
        clients = (await client.get("/api/v1/clients", headers=h)).json()
        assert [c["name"] for c in clients["items"]] == ["شركة ألف"]
    async with login(client, "rep_b@brokerhub.test") as h:
        clients = (await client.get("/api/v1/clients", headers=h)).json()
        assert [c["name"] for c in clients["items"]] == ["شركة باء"]
        hidden = await client.get(f"/api/v1/clients/{ids['clients']['ألف']}/overview", headers=h)
        assert hidden.status_code == 404


async def test_unauthenticated_is_401(client, ids) -> None:
    assert (await client.get("/api/v1/leads")).status_code == 401


async def test_portal_owner_sees_only_their_company(client, ids) -> None:
    async with login(client, "owner@brokerhub.test") as h:
        overview = await client.get("/api/v1/portal/overview", headers=h)
        assert overview.status_code == 200
        assert overview.json()["client"]["name"] == "شركة ألف"
        # a portal token must not be able to read staff endpoints
        assert (await client.get("/api/v1/leads", headers=h)).status_code == 403
        assert (await client.get(f"/api/v1/clients/{ids['clients']['باء']}", headers=h)).status_code in {403, 404}
