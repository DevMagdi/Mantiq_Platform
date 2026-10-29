"""Client portal: one company only, and never staff data (spec §6.3, §9.6)."""

from __future__ import annotations

from typing import Any

from tests.helpers import login

OWNER = "owner@brokerhub.test"
MANAGER = "manager@brokerhub.test"
FORBIDDEN_KEYS = {
    "assigned_to",
    "assignee_name",
    "internal_notes",
    "notes",
    "commission",
    "commissions",
    "recording",
    "recording_url",
    "recording_key",
    "listen_allowed",
    "calls",
}


def _walk(payload: Any) -> set[str]:
    if isinstance(payload, dict):
        return set(payload) | {key for value in payload.values() for key in _walk(value)}
    if isinstance(payload, list):
        return {key for item in payload for key in _walk(item)}
    return set()


async def test_portal_overview_is_the_owners_own_company(client, ids: dict[str, dict[str, str]]) -> None:
    async with login(client, OWNER) as auth:
        overview = await client.get("/api/v1/portal/overview", headers=auth)
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["client"]["id"] == ids["clients"]["ألف"]
    assert set(body["kpis"]) == {"requests_this_month", "avg_response_minutes", "premiums"}
    assert "covered_count" in body and "renewal_in_days" in body


async def test_portal_sees_only_own_requests_and_no_staff_fields(client, ids: dict[str, dict[str, str]]) -> None:
    async with login(client, MANAGER) as admin_auth:
        # one request for the owner's company, one for another client
        mine = await client.post(
            "/api/v1/requests", json={"subject": "طلب من شركة ألف", "client_id": ids["clients"]["ألف"]}, headers=admin_auth
        )
        theirs = await client.post(
            "/api/v1/requests", json={"subject": "طلب من شركة باء", "client_id": ids["clients"]["باء"]}, headers=admin_auth
        )
        note = await client.post(
            f"/api/v1/requests/{mine.json()['id']}/notes", json={"body": "ملاحظة داخلية للموظفين فقط"}, headers=admin_auth
        )
        assert note.status_code in (200, 201)
        await client.post(
            f"/api/v1/requests/{mine.json()['id']}/messages",
            json={"body": "نظرت في طلبك", "channel": "whatsapp"},
            headers=admin_auth,
        )

    async with login(client, OWNER) as auth:
        listed = await client.get("/api/v1/portal/requests", headers=auth)
        # The overview is the other door into a client's money: it must leak nothing either.
        overview = await client.get("/api/v1/portal/overview", headers=auth)
    assert listed.status_code == 200, listed.text
    assert overview.status_code == 200
    assert _walk(overview.json()) & FORBIDDEN_KEYS == set(), _walk(overview.json()) & FORBIDDEN_KEYS
    items = listed.json()["items"]
    ids_seen = {row["id"] for row in items}
    assert mine.json()["id"] in ids_seen
    assert theirs.json()["id"] not in ids_seen  # other companies are invisible, not 403
    assert _walk(items) & FORBIDDEN_KEYS == set()

    thread = [msg for row in items if row["id"] == mine.json()["id"] for msg in row["messages"]]
    assert any(msg["body"] == "نظرت في طلبك" for msg in thread)
    assert all("ملاحظة داخلية" not in (msg["body"] or "") for msg in thread)


async def test_owner_can_open_a_request_for_their_own_company(client, ids: dict[str, dict[str, str]]) -> None:
    async with login(client, OWNER) as auth:
        created = await client.post(
            "/api/v1/portal/requests", json={"subject": "إضافة موظف جديد", "body": "البداية الشهر الجاي"}, headers=auth
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["number"] >= 1000
        listed = await client.get("/api/v1/portal/requests", headers=auth)
        mine = next(row for row in listed.json()["items"] if row["id"] == body["request_id"])
    async with login(client, MANAGER) as auth:
        staff_view = await client.get(f"/api/v1/requests/{body['request_id']}", headers=auth)
    assert mine["client_id"] == ids["clients"]["ألف"]  # pinned to their own company
    assert staff_view.json()["client_id"] == ids["clients"]["ألف"]
    assert staff_view.json()["channel"] == "manual"


async def test_portal_cannot_touch_staff_endpoints(client, ids: dict[str, dict[str, str]]) -> None:
    async with login(client, OWNER) as auth:
        for path in [
            "/api/v1/leads",
            "/api/v1/requests",
            "/api/v1/users",
            "/api/v1/clients",
            "/api/v1/commissions",
            f"/api/v1/clients/{ids['clients']['باء']}/insured",
        ]:
            response = await client.get(path, headers=auth)
            assert response.status_code in (403, 404), f"{path} -> {response.status_code}"
        # staff client files are not part of the portal at all: the permission layer answers
        other_file = await client.get(f"/api/v1/clients/{ids['clients']['باء']}/overview", headers=auth)
        assert other_file.status_code in (403, 404)
        # and the portal surface itself refuses a staff user
    async with login(client, MANAGER) as auth:
        staff_on_portal = await client.get("/api/v1/portal/overview", headers=auth)
    assert staff_on_portal.status_code == 403


async def test_attachment_of_another_company_is_not_downloadable(client, ids: dict[str, dict[str, str]]) -> None:
    async with login(client, MANAGER) as auth:
        created = await client.post(
            "/api/v1/requests", json={"subject": "طلب شركة باء للمرفق", "client_id": ids["clients"]["باء"]}, headers=auth
        )
        upload = await client.post(
            f"/api/v1/requests/{created.json()['id']}/attachments",
            files={"file": ("terms.pdf", b"%PDF-1.4 secret", "application/pdf")},
            headers=auth,
        )
        attachment_id = upload.json()["id"]
    async with login(client, OWNER) as auth:
        denied = await client.get(f"/api/v1/portal/attachments/{attachment_id}", headers=auth)
    assert denied.status_code == 404
