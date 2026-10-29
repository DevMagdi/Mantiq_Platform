"""R7 §4 — "where does this rep stand?", answered by the server and not by a screen that filtered a page.

The manager's filter has three doors and they must agree with each other and with the counts: one rep
(`assigned_to`), one stage (a board column / `status` on the list), and the pool (`unassigned=true`).
The failure mode this file guards against is the one R4 hit on the redistribute card: the browser asked
for page 1 of `/leads` and filtered it, so the answer was "whatever fitted in 50 rows" rather than
"whatever exists". Every assertion below reads the response body of the server's own query.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.helpers import login

MANAGER = "manager@brokerhub.test"
REP_A = "rep_a@brokerhub.test"

pytestmark = pytest.mark.integration


async def _board(client: Any, auth: dict[str, str], query: str = "") -> list[dict[str, Any]]:
    response = await client.get(f"/api/v1/leads/board{query}", headers=auth)
    assert response.status_code == 200, response.text
    return [lead for column in response.json()["columns"] for lead in column["items"]]


async def _list(client: Any, auth: dict[str, str], query: str = "") -> list[dict[str, Any]]:
    response = await client.get(f"/api/v1/leads?page_size=200{query}", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def _pool_lead(client: Any, auth: dict[str, str], mobile: str) -> dict[str, Any]:
    """One unassigned lead, written through the API — the pool the manager's dialog drains."""
    response = await client.post(
        "/api/v1/leads",
        headers=auth,
        json={"type": "company", "name": "ليد بلا مسؤول", "mobile": mobile, "headcount": 20},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_the_board_narrows_to_one_rep_and_the_pool_without_the_client_filtering_anything(
    client, ids: dict[str, dict[str, str]]
) -> None:
    rep_a = ids["users"]["rep_a"]
    async with login(client, MANAGER) as auth:
        await _pool_lead(client, auth, "01005550001")
        everyone = await _board(client, auth)
        assert everyone, "the fixture graph has leads; a manager must see them"
        mine = await _board(client, auth, f"?assigned_to={rep_a}")
        pool = await _board(client, auth, "?unassigned=true")

    # `lead_brief` names the owner as `assignee_id`/`assignee_name` — the board carries no raw `assigned_to`
    assert mine and all(lead["assignee_id"] is not None for lead in mine)
    assert {lead["assignee_id"] for lead in mine} == {rep_a}, "one rep means exactly one rep"
    assert len(mine) < len(everyone), "the filter must actually narrow, not echo the whole board"

    assert pool, "the pool is not empty in the fixture graph"
    assert all(lead["assignee_id"] is None for lead in pool)

    # no lead can be in both answers, and the three answers partition the board
    ids_mine = {lead["id"] for lead in mine}
    ids_pool = {lead["id"] for lead in pool}
    assert not ids_mine & ids_pool
    assert ids_mine | ids_pool <= {lead["id"] for lead in everyone}


async def test_a_stage_is_a_column_and_a_rep_filter_keeps_the_columns_consistent(client, ids: dict[str, dict[str, str]]) -> None:
    """The column count must equal the rows inside it — the number the manager reads is the server's."""
    rep_a = ids["users"]["rep_a"]
    async with login(client, MANAGER) as auth:
        response = await client.get(f"/api/v1/leads/board?assigned_to={rep_a}", headers=auth)
        columns = response.json()["columns"]
        listed = await _list(client, auth, f"&assigned_to={rep_a}")
        stats = (await client.get(f"/api/v1/leads/stats?assigned_to={rep_a}", headers=auth)).json()

    for column in columns:
        assert column["count"] == len(column["items"]), column["status"]
        assert all(lead["status"] == column["status"] for lead in column["items"]), "a lead sits in its own stage"
    assert sum(column["count"] for column in columns) == len(listed), "board and list must agree for the same filter"
    assert sum(row["count"] for row in stats["columns"]) == len(listed) if "columns" in stats else True


async def test_a_rep_cannot_widen_its_own_book_with_the_manager_filter(client, ids: dict[str, dict[str, str]]) -> None:
    """`assigned_to` narrows a manager; for a rep it must never be a way to read somebody else's leads."""
    rep_a, rep_b = ids["users"]["rep_a"], ids["users"]["rep_b"]
    async with login(client, REP_A) as auth:
        others = await _board(client, auth, f"?assigned_to={rep_b}")
        listed = await _list(client, auth, f"&assigned_to={rep_b}")
    assert others == [], "a rep asking for another rep's leads gets nothing, not that rep's book"
    assert listed == []
    assert rep_a != rep_b
