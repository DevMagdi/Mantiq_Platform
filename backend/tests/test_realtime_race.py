"""The R10.1 flake probe: a tab that refetches **on the frame** must already be able to see the row.

e2e run 40734067912 (`r6-round.spec.ts:129`) failed with the tab's revision at 16 — the change frame had
arrived — while the queue refetch that the frame triggered (33 ms after the POST, `page_size=40`, 10 rows)
did not hold the request the manager had just created. The server's own queue held it 17 s later, so the
row existed; the question is *when it became readable* relative to the frame that announced it.

That ordering is invisible to the browser test (it only shows the symptom), so it is measured here: the
POST runs as a task, the frame is awaited, and the queue is queried the instant the frame arrives — exactly
the interleaving `realtimeProvider.handleFrame → invalidateQueries → GET /requests` produces. Repeated,
because a race that fires once in fifty runs is still a race.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from tests.conftest import PASSWORD
from tests.test_realtime import CS, MANAGER, _next_frame, served_app

#: how many frame-then-read rounds one run measures. The e2e suite sees this interleaving once per run,
#: so the probe samples it harder than any single CI job can.
ROUNDS = 40


async def _token(http: httpx.AsyncClient, email: str) -> dict[str, str]:
    response = await http.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_the_created_row_is_readable_the_moment_the_change_frame_arrives(ids: dict[str, dict[str, str]]) -> None:
    """Fail with the count of misses, not with "sometimes red"."""
    misses: list[str] = []
    late: list[str] = []
    async with served_app() as base, httpx.AsyncClient(base_url=base, timeout=15) as http:
        cs_auth = await _token(http, CS)
        manager_auth = await _token(http, MANAGER)

        # the client the new request belongs to. `e2e/r6-round.spec.ts` reads it through a row the CS agent
        # already sees; the fixture graph names it directly, which is the same isolation claim (CS holds
        # `requests.view`, so its scope is office-wide) without a second dependency on seed state.
        client_id = ids["clients"]["ألف"]

        async with http.stream("GET", "/api/v1/realtime/stream", headers=cs_auth) as stream:
            assert stream.status_code == 200
            lines: Any = stream.aiter_lines()
            hello = await _next_frame(lines, event="hello")
            assert "requests" in hello["topics"]

            for round_no in range(ROUNDS):
                name = f"سباق البث {round_no} {time.time_ns()}"
                post = asyncio.create_task(
                    http.post(
                        "/api/v1/requests",
                        headers=manager_auth,
                        json={
                            "channel": "manual",
                            "client_id": client_id,
                            "subject": name,
                            "contact_name": name,
                            "description": "R10.1 race probe",
                            "first_message": name,
                        },
                    )
                )
                # the frame is what the tab reacts to — so read the queue now, not after the POST returns
                await _next_frame(lines, event="change")
                queue = await http.get(
                    "/api/v1/requests?only_open=true&page_size=40&sort=last_activity_at&order=desc",
                    headers=cs_auth,
                )
                rows = queue.json().get("items", [])
                if not any(row.get("subject") == name for row in rows):
                    misses.append(f"round {round_no}: {len(rows)} rows, subject absent")
                    # keep watching: "it arrived 30 ms late" and "it never arrived" are different bugs
                    for _ in range(50):
                        await asyncio.sleep(0.02)
                        again = await http.get(
                            "/api/v1/requests?only_open=true&page_size=40&sort=last_activity_at&order=desc",
                            headers=cs_auth,
                        )
                        if any(row.get("subject") == name for row in again.json().get("items", [])):
                            late.append(f"round {round_no}: visible after {(_ + 1) * 20} ms")
                            break
                created = await post
                assert created.status_code == 201, created.text

    assert not misses, (
        f"{len(misses)} of {ROUNDS} change frames arrived before the row was readable "
        f"(the tab would refetch an empty queue and then wait for a second event that never comes): "
        f"{misses}; late arrivals: {late or 'none'}"
    )
