"""The board must not hand the browser a whole stage (R15 P1-B).

`/leads/board` used to be one query with a global `LIMIT 500` over all five stages. Two things were wrong
with that, and only one of them is speed:

* 500 cards × four eager-loaded relations is a payload and a DOM the browser mounts before it can scroll;
* a **global** limit is a budget the stages compete for. A stage that is busy during a campaign can consume
  the first 400 rows, and the lane the manager is looking at comes back empty — "no leads in negotiation",
  which is a lie, not a page.

The fix is a window per stage plus a true count per stage, so the lane can say "newest 60 of 214" and mean it.
"""

from __future__ import annotations

from typing import Any

from app.core.db import get_sessionmaker
from app.models.leads import Lead
from sqlalchemy import func, select

from tests.helpers import login

MANAGER = "manager@mantiq.test"


async def _crowd(status: str, count: int, *, tag: str) -> None:
    """Put `count` leads into one stage.

    Inserted rather than moved: the fixture graph seeds two leads, and a test that claims to prove a *crowded*
    lane must actually create the crowd — moving two rows would have made the "busy lane" case untested.
    """
    maker = get_sessionmaker()
    async with maker() as session:
        session.add_all(
            [
                Lead(name=f"ليد {tag} {index}", type="company", status=status, mobile=f"+2019{abs(hash((tag, index))) % 10**8:08d}")
                for index in range(count)
            ]
        )
        await session.commit()


async def _stage_total(status: str) -> int:
    maker = get_sessionmaker()
    async with maker() as session:
        return int((await session.execute(select(func.count(Lead.id)).where(Lead.status == status))).scalar_one())


async def test_each_lane_is_capped_and_reports_its_true_total(client: Any) -> None:
    await _crowd("quote_sent", 90, tag="crowd")
    total = await _stage_total("quote_sent")

    async with login(client, MANAGER) as auth:
        board = (await client.get("/api/v1/leads/board", params={"per_column": 20}, headers=auth)).json()

    lane = next(column for column in board["columns"] if column["status"] == "quote_sent")
    assert total >= 90
    assert lane["count"] == 20, "the lane carries a page, not the stage"
    assert lane["total"] == total, "…and says how many are behind it"


async def test_a_busy_lane_cannot_starve_the_others(client: Any) -> None:
    """The regression the old global LIMIT had: 400 rows in stage one, nothing left for stage five."""
    await _crowd("quote_sent", 400, tag="busy")
    await _crowd("meeting_scheduled", 5, tag="quiet")

    async with login(client, MANAGER) as auth:
        board = (await client.get("/api/v1/leads/board", params={"per_column": 60}, headers=auth)).json()

    proposal = next(column for column in board["columns"] if column["status"] == "meeting_scheduled")
    negotiation = next(column for column in board["columns"] if column["status"] == "quote_sent")
    assert negotiation["count"] == 60
    assert proposal["count"] >= 1, "a crowded stage must not empty another lane"


async def test_the_lane_is_the_newest_cards_of_that_stage(client: Any) -> None:
    async with login(client, MANAGER) as auth:
        board = (await client.get("/api/v1/leads/board", params={"per_column": 15}, headers=auth)).json()

    for column in board["columns"]:
        stamps = [item["last_activity_at"] for item in column["items"]]
        assert stamps == sorted(stamps, reverse=True), f"{column['status']} must be ordered by recency"


async def test_the_ceiling_is_the_api_s(client: Any) -> None:
    async with login(client, MANAGER) as auth:
        too_many = await client.get("/api/v1/leads/board", params={"per_column": 5000}, headers=auth)
        too_few = await client.get("/api/v1/leads/board", params={"per_column": 1}, headers=auth)
    assert too_many.status_code == 422 and too_few.status_code == 422


async def test_the_manager_filter_still_narrows_and_never_widens(client: Any, ids: dict[str, dict[str, str]]) -> None:
    """The cap must not become a way around scope: a rep asking for a colleague's board still sees nothing."""
    async with login(client, MANAGER) as auth:
        manager_board = (await client.get("/api/v1/leads/board", headers=auth)).json()
        narrowed = (await client.get("/api/v1/leads/board", params={"assigned_to": ids["users"]["rep_a"]}, headers=auth)).json()

    assert sum(column["total"] for column in narrowed["columns"]) <= sum(column["total"] for column in manager_board["columns"])
