"""A long thread must not be sent whole (R15 P1-A).

The measurement that opened R15: opening a negotiation with hundreds of WhatsApp messages returned every
message, every note, every attachment and every call, and the browser then mounted one node per row. The data
was correct; the *shape* was "the whole history or nothing", which is fine for a demo and unusable for a
client who has been writing to the office for a year.

What must stay true: the newest page is the one that opens (a chat reads bottom-up), an older page is one
request away and does not overlap or skip, and the totals still tell the truth — a page that happens to be
exactly the page size must not make the screen believe it has reached the beginning.
"""

from __future__ import annotations

from typing import Any

from tests.helpers import login

MANAGER = "manager@mantiq.test"


async def _fat_thread(client: Any, auth: dict[str, str], ids: dict[str, dict[str, str]], count: int = 45) -> str:
    created = await client.post(
        "/api/v1/requests",
        json={"subject": "محادثة طويلة", "client_id": ids["clients"]["ألف"]},
        headers=auth,
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    for index in range(count):
        posted = await client.post(f"/api/v1/requests/{request_id}/messages", json={"body": f"رسالة {index:02d}"}, headers=auth)
        assert posted.status_code in (200, 201), posted.text
    return request_id


async def test_the_detail_payload_carries_the_newest_page_and_a_true_total(client: Any, ids: dict[str, dict[str, str]]) -> None:
    async with login(client, MANAGER) as auth:
        request_id = await _fat_thread(client, auth, ids, count=45)
        detail = (await client.get(f"/api/v1/requests/{request_id}", headers=auth)).json()

    assert detail["messages_total"] == 45
    assert len(detail["messages"]) == 30, "the default page is 30, not the whole history"
    bodies = [message["body"] for message in detail["messages"]]
    assert bodies[-1] == "رسالة 44", "the page must end at the newest message"
    assert bodies[0] == "رسالة 15", "…and start exactly 30 back"


async def test_loading_older_pages_walks_back_without_gaps_or_overlap(client: Any, ids: dict[str, dict[str, str]]) -> None:
    async with login(client, MANAGER) as auth:
        request_id = await _fat_thread(client, auth, ids, count=45)
        first = (await client.get(f"/api/v1/requests/{request_id}", headers=auth)).json()["messages"]
        oldest = first[0]["created_at"]
        older = (await client.get(f"/api/v1/requests/{request_id}/messages", params={"before": oldest, "limit": 30}, headers=auth)).json()
        rest = (
            await client.get(
                f"/api/v1/requests/{request_id}/messages", params={"before": older[0]["created_at"], "limit": 30}, headers=auth
            )
        ).json()

    walked = [message["body"] for message in rest + older + first]
    assert walked == [f"رسالة {index:02d}" for index in range(45)], "no message may be skipped or repeated"
    assert rest == [] or len(rest) == 15


async def test_the_window_is_a_parameter_and_has_a_ceiling(client: Any, ids: dict[str, dict[str, str]]) -> None:
    async with login(client, MANAGER) as auth:
        request_id = await _fat_thread(client, auth, ids, count=12)
        small = (await client.get(f"/api/v1/requests/{request_id}", params={"message_limit": 5}, headers=auth)).json()
        too_big = await client.get(f"/api/v1/requests/{request_id}", params={"message_limit": 5000}, headers=auth)

    assert len(small["messages"]) == 5 and small["messages_total"] == 12
    assert too_big.status_code == 422, "the ceiling is the API's, not the client's good manners"


async def test_the_side_panels_are_windows_too(client: Any, ids: dict[str, dict[str, str]]) -> None:
    """Notes/calls/attachments were unbounded in the same query; they now have their own pages and totals."""
    async with login(client, MANAGER) as auth:
        request_id = await _fat_thread(client, auth, ids, count=3)
        for index in range(55):
            note = await client.post(f"/api/v1/requests/{request_id}/notes", json={"body": f"ملاحظة {index}"}, headers=auth)
            assert note.status_code in (200, 201), note.text
        detail = (await client.get(f"/api/v1/requests/{request_id}", headers=auth)).json()

    assert detail["notes_total"] == 55
    assert len(detail["notes"]) == 50, "the panel loads a window, and the total still says 55"


async def test_a_thread_shorter_than_the_page_is_still_complete(client: Any, ids: dict[str, dict[str, str]]) -> None:
    """The window must not truncate what fits: a client asking for 200 gets all 45, and the total agrees."""
    async with login(client, MANAGER) as auth:
        request_id = await _fat_thread(client, auth, ids, count=45)
        detail = (await client.get(f"/api/v1/requests/{request_id}", params={"message_limit": 200}, headers=auth)).json()

    assert len(detail["messages"]) == detail["messages_total"] == 45
