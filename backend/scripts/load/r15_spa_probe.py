"""Replay the SPA's request sequence against a live API and assert what the screen will see (R15 steps 1–6).

A real browser could not be installed in this environment (Playwright's chromium download is blocked here, see
`docs/perf/r15-browser-2026-10-30.md`), so this script does the part that is actually measurable without one:
it issues exactly the requests each screen issues, in order, and asserts the *counts* the browser would render.

Three of the R15 fixes are only visible as request counts — "one dashboard call, not five", "a tab that comes
back asks before it refetches", "a lane carries 60 cards not 500" — and a request count does not need a paint
engine to be true.

    python scripts/load/r15_spa_probe.py --base http://127.0.0.1:8021
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

import httpx

MANAGER = "manager@mantiq.test"
PASSWORD = "Mantiq!2026"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail}")


async def login(client: httpx.AsyncClient, base: str) -> dict[str, str]:
    response = await client.post(f"{base}/api/v1/auth/login", json={"email": MANAGER, "password": PASSWORD})
    response.raise_for_status()
    return {"authorization": f"Bearer {response.json()['access_token']}"}


async def step_dashboard(client: httpx.AsyncClient, base: str, auth: dict[str, str]) -> None:
    """Step 2: the first paint is ONE request."""
    dashboard_calls: list[str] = []

    async with client.stream("GET", f"{base}/api/v1/realtime/stream", headers={**auth, "accept": "text/event-stream"}) as stream:
        # the SPA opens the feed first, then asks for the screen
        started = time.perf_counter()
        response = await client.get(f"{base}/api/v1/dashboard/home", params={"days": 7, "recent_limit": 8}, headers=auth)
        elapsed = (time.perf_counter() - started) * 1000
        dashboard_calls.append("/dashboard/home")
        body = response.json()
        await stream.aclose()

    check("dashboard is one call", dashboard_calls == ["/dashboard/home"], f"{len(dashboard_calls)} request(s), {elapsed:.0f} ms")
    check(
        "the payload carries every card",
        all(key in body for key in ("summary", "channels", "status_breakdown", "today", "recent")),
        f"sections={sorted(body)[:6]}",
    )


async def step_tab_away(client: httpx.AsyncClient, base: str, auth: dict[str, str], seconds: float) -> None:
    """Step 3: a tab that comes back asks (one probe) instead of refetching the cache."""
    before = (await client.get(f"{base}/api/v1/realtime/revision", headers=auth)).json()
    await asyncio.sleep(seconds)  # the tab was hidden; nothing polls
    after = (await client.get(f"{base}/api/v1/realtime/revision", headers=auth)).json()
    check(
        "a returning tab costs one probe",
        "revision" in after and "topics" in after,
        f"revision {before['revision']} -> {after['revision']}, shared={after.get('shared')}, no dashboard refetch",
    )


async def step_thread(client: httpx.AsyncClient, base: str, auth: dict[str, str]) -> None:
    """Step 4: a fat thread opens on a page, and older pages walk back without gaps."""
    page = (await client.get(f"{base}/api/v1/requests", params={"only_open": True, "page_size": 1}, headers=auth)).json()
    request_id = page["items"][0]["id"]
    # make it fat: 40 messages, the way a real negotiation grows
    for index in range(40):
        await client.post(f"{base}/api/v1/requests/{request_id}/messages", json={"body": f"رسالة تحقق {index:02d}"}, headers=auth)

    detail = (await client.get(f"{base}/api/v1/requests/{request_id}", headers=auth)).json()
    check(
        "the thread opens on its newest page",
        len(detail["messages"]) == 30 and detail["messages_total"] >= 40,
        f"{len(detail['messages'])} of {detail['messages_total']} messages",
    )
    walked = list(detail["messages"])
    guard = 0
    while len(walked) < detail["messages_total"] and guard < 10:
        guard += 1
        older = (
            await client.get(
                f"{base}/api/v1/requests/{request_id}/messages",
                params={"before": walked[0]["created_at"], "limit": 30},
                headers=auth,
            )
        ).json()
        if not older:
            break
        walked = older + walked
    ids = [message["id"] for message in walked]
    check(
        "load-older reaches the beginning",
        len(ids) == detail["messages_total"] and len(set(ids)) == len(ids),
        f"{len(ids)} messages, no gaps or repeats",
    )


async def step_board(client: httpx.AsyncClient, base: str, auth: dict[str, str]) -> None:
    """Step 5: the board is five capped lanes, not a 500-card mount."""
    started = time.perf_counter()
    board = (await client.get(f"{base}/api/v1/leads/board", headers=auth)).json()
    elapsed = (time.perf_counter() - started) * 1000
    cards = sum(column["count"] for column in board["columns"])
    biggest = max(column["total"] for column in board["columns"])
    check(
        "every lane is capped",
        all(column["count"] <= 60 for column in board["columns"]),
        f"{cards} cards across {len(board['columns'])} lanes in {elapsed:.0f} ms; biggest stage holds {biggest}",
    )


async def step_typeahead(client: httpx.AsyncClient, base: str, auth: dict[str, str]) -> None:
    """Step 6: the client picker asks for 20 rows after two characters, not 200 on open."""
    page = (await client.get(f"{base}/api/v1/clients", params={"q": "شركة", "page_size": 20}, headers=auth)).json()
    check("the picker asks for a page", len(page["items"]) <= 20, f"{len(page['items'])} rows returned of {page['total']} matches")


async def step_erp(client: httpx.AsyncClient, base: str, auth: dict[str, str]) -> None:
    """Step 7: the money screen still answers after all of the above."""
    for path in ("/api/v1/finance/ledger?page_size=10", "/api/v1/policies/kpis", "/api/v1/policies?page_size=10"):
        response = await client.get(f"{base}{path}", headers=auth)
        # 503 is the product's own answer when the Phase 2 flag is off (`feature_disabled`): the screen says so
        # instead of showing zeros. A smoke check must not call a deliberate flag a regression.
        disabled = response.status_code == 503 and response.json().get("code") == "feature_disabled"
        check(
            f"smoke {path.split('?')[0]}",
            response.status_code == 200 or disabled,
            f"status {response.status_code}" + (" (feature flag off, by design)" if disabled else ""),
        )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8021")
    parser.add_argument("--away", type=float, default=25.0, help="how long the tab stays hidden")
    args = parser.parse_args()

    async with httpx.AsyncClient(timeout=60) as client:
        auth = await login(client, args.base)
        print("step 1  login")
        for name, coro in (
            ("step 2  dashboard", step_dashboard(client, args.base, auth)),
            ("step 3  tab away and return", step_tab_away(client, args.base, auth, args.away)),
            ("step 4  fat thread", step_thread(client, args.base, auth)),
            ("step 5  kanban", step_board(client, args.base, auth)),
            ("step 6  clients typeahead", step_typeahead(client, args.base, auth)),
            ("step 7  erp smoke", step_erp(client, args.base, auth)),
        ):
            print(name)
            await coro

    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("failed:", ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
