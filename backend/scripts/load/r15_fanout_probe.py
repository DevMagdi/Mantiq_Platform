"""Does a mutation handled by worker A reach a tab attached to worker B? (R15 P1-C)

Two uvicorn workers share one listening socket, so an SSE stream and the `PATCH` that follows it land on
whichever worker the kernel hands them. With an in-process hub, a stream that lands on the other worker than
the mutation is *silent*: the change happened, the tab is open, and nothing arrives — the failure this script
was written to expose. It is invisible by construction, which is why it needs a number rather than an opinion.

Method: repeat N times — open a stream, read its `hello`, mutate, wait up to `--wait` seconds for the frame —
and report how many attempts heard their change. With two workers and no fan-out, each attempt succeeds only
when both connections land on the same worker (~50%), so N=12 delivers all 12 with probability ~0.02%. With
fan-out every attempt must succeed; one miss is a failure, not noise.

Usage:
    python scripts/load/r15_fanout_probe.py --base http://127.0.0.1:8021 --attempts 12

Exit code is the deliverable: 0 only when every attempt heard its own change.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

import httpx

EMAIL = "manager@mantiq.test"
PASSWORD = "Mantiq!2026"


async def login(client: httpx.AsyncClient, base: str) -> str:
    response = await client.post(f"{base}/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    response.raise_for_status()
    return str(response.json()["access_token"])


async def attempt(client: httpx.AsyncClient, base: str, token: str, request_id: str, *, wait: float) -> dict[str, object]:
    """One stream, one mutation on the same resource, and whether the frame arrived in time."""
    started = time.perf_counter()
    headers = {"authorization": f"Bearer {token}", "accept": "text/event-stream"}
    delivered = False
    revision: int | None = None
    async with client.stream("GET", f"{base}/api/v1/realtime/stream", params={"since": 0}, headers=headers, timeout=wait + 5) as stream:
        stream.raise_for_status()
        read = asyncio.create_task(_first_frame(stream))
        # the mutation goes out while the stream is open; the route publishes on the `requests` topic
        await asyncio.sleep(0.05)
        patched = await client.patch(
            f"{base}/api/v1/requests/{request_id}",
            json={"subject": f"تحديث تزامن {time.time():.3f}"},
            headers={"authorization": f"Bearer {token}"},
        )
        if patched.status_code >= 400:
            read.cancel()
            patched.raise_for_status()
        try:
            frame = await asyncio.wait_for(read, timeout=wait)
        except TimeoutError:
            # a miss is the *measurement*, not an error: the mutation was accepted and this tab never heard it
            read.cancel()
            frame = None
    if frame is not None:
        delivered, revision = frame
    return {"delivered": delivered, "revision": revision, "seconds": round(time.perf_counter() - started, 3)}


async def _first_frame(stream: httpx.Response) -> tuple[bool, int | None]:
    """Wait for the first `event: change` frame after `hello`. Returns (heard, revision)."""
    event = ""
    async for line in stream.aiter_lines():
        if line.startswith("event: "):
            event = line[7:].strip()
        elif line.startswith("data: ") and event == "change":
            payload = json.loads(line[6:])
            return True, payload.get("revision")
    return False, None


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8021")
    parser.add_argument("--attempts", type=int, default=12)
    parser.add_argument("--wait", type=float, default=2.0, help="the product's own ≤2 s promise, not a slack budget")
    args = parser.parse_args()

    async with httpx.AsyncClient(timeout=30) as client:
        token = await login(client, args.base)
        # a stable target to mutate: any open request the manager can patch
        page = await client.get(
            f"{args.base}/api/v1/requests",
            params={"only_open": True, "page_size": 1},
            headers={"authorization": f"Bearer {token}"},
        )
        page.raise_for_status()
        request_id = page.json()["items"][0]["id"]

        results = []
        for index in range(args.attempts):
            result = await attempt(client, args.base, token, request_id, wait=args.wait)
            results.append(result)
            print(f"  attempt {index + 1:2d}: delivered={result['delivered']} revision={result['revision']} in {result['seconds']}s")

    heard = sum(1 for result in results if result["delivered"])
    slowest = max((float(result["seconds"]) for result in results if result["delivered"]), default=0.0)
    print(f"\n{heard}/{len(results)} attempts heard their own change; slowest delivery {slowest:.3f}s")
    return 0 if heard == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
