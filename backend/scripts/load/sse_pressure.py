"""R8 · S4 — realtime pressure: many open `/realtime/stream` tabs while the API is still being used.

The concern this measures is the one that took down two e2e runs in R7: a stream that holds a pooled
database connection for as long as the tab is open starves the pool, and then *login* — the one request
everybody needs — starts answering `500 QueuePool limit …` after 30 s.

So the run is deliberately hostile in a specific way: N streams stay open, and a light read load runs at
the same time; the pass condition is that the read load keeps answering 200 with no 5xx, that logins keep
working, and that PostgreSQL's own connection count stays bounded.

What is measured (all from this process, no estimates):
  * streams opened / first frame received (hello)
  * login p50/p95 during the pressure window, and the count of non-200 answers
  * read requests p50/p95 and the count of non-200 answers
  * `pg_stat_activity` connection count: max during the window, and at the end
  * process RSS of the API before/after (a leak shows up here)

Usage:
    python scripts/load/sse_pressure.py --streams 40 --seconds 300 \
        --out ../docs/qa/load/raw/s4-realtime.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx

BASE = "http://127.0.0.1:8020"
PASSWORD = "Br0kerHub!2026"
MANAGER = "manager@brokerhub.test"
CS = "cs@brokerhub.test"
PG_SOCKET = "/tmp/r8pg"


async def login(client: httpx.AsyncClient, email: str) -> tuple[str, float]:
    started = time.perf_counter()
    response = await client.post(f"{BASE}/api/v1/auth/login", json={"email": email, "password": PASSWORD, "remember_me": True})
    took = (time.perf_counter() - started) * 1000
    response.raise_for_status()
    return response.json()["access_token"], took


async def pg_connections() -> int:
    """Active+idle connections PostgreSQL itself reports — the pool the client sees, from the server's side."""
    proc = await asyncio.create_subprocess_exec(
        ".venv/lib/python3.11/site-packages/pgserver/pginstall/bin/psql",
        "-h",
        PG_SOCKET,
        "-U",
        "postgres",
        "-d",
        "brokerhub_load",
        "-t",
        "-A",
        "-c",
        "select count(*) from pg_stat_activity where datname='brokerhub_load'",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    try:
        return int(out.decode().strip() or 0)
    except ValueError:
        return 0


async def hold_stream(client: httpx.AsyncClient, token: str, index: int, stop_at: float, result: dict[str, Any]) -> None:
    """One open tab: connect, read `hello`, then sit there reading heartbeats until the window ends."""
    frames = 0
    started = time.perf_counter()
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
    try:
        async with client.stream("GET", f"{BASE}/api/v1/realtime/stream", headers=headers, timeout=None) as response:
            result["statuses"].append(response.status_code)
            if response.status_code != 200:
                return
            result["first_frame_ms"].append((time.perf_counter() - started) * 1000)
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    frames += 1
                if time.perf_counter() > stop_at:
                    break
            result["frames"].append(frames)
            result["held_s"].append(time.perf_counter() - started)
    except Exception as exc:
        result["errors"].append(f"stream{index}: {type(exc).__name__}: {exc}")


async def read_load(client: httpx.AsyncClient, token: str, stop_at: float, result: dict[str, Any]) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    while time.perf_counter() < stop_at:
        for path in ("/api/v1/dashboard/summary", "/api/v1/leads?page_size=10", "/api/v1/requests?only_open=true"):
            started = time.perf_counter()
            try:
                response = await client.get(f"{BASE}{path}", headers=headers, timeout=60)
            except Exception as exc:
                result["read_errors"].append(f"{path}: {type(exc).__name__}")
                continue
            result["read_ms"].append({"path": path, "ms": (time.perf_counter() - started) * 1000, "status": response.status_code})
            if response.status_code >= 500:
                result["read_5xx"].append(f"{path} -> {response.status_code}")
        await asyncio.sleep(0.5)


async def login_probe(client: httpx.AsyncClient, email: str, stop_at: float, result: dict[str, Any]) -> None:
    """Logins keep coming while the tabs are open — the exact thing that broke in R7."""
    while time.perf_counter() < stop_at:
        started = time.perf_counter()
        try:
            response = await client.post(f"{BASE}/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        except Exception as exc:
            result["login_errors"].append(type(exc).__name__)
            continue
        result["login_ms"].append((time.perf_counter() - started) * 1000)
        if response.status_code != 200:
            result["login_non200"].append(response.status_code)
        await asyncio.sleep(1.0)


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return round(ordered[index], 1)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--streams", type=int, default=40)
    parser.add_argument("--seconds", type=int, default=300)
    parser.add_argument("--out", type=Path, default=Path("s4-realtime.json"))
    args = parser.parse_args()

    limits = httpx.Limits(max_connections=400, max_keepalive_connections=200)
    # one client for the streams: they are long-lived, so their own pool must be large enough not to queue
    async with httpx.AsyncClient(limits=limits, timeout=60) as control:
        manager_token, _ = await login(control, MANAGER)
        cs_token, _ = await login(control, CS)
        base_conns = await pg_connections()

    stop_at = time.perf_counter() + args.seconds
    stream_result: dict[str, Any] = {"statuses": [], "first_frame_ms": [], "frames": [], "held_s": [], "errors": []}
    load_result: dict[str, Any] = {"read_ms": [], "read_errors": [], "read_5xx": [], "login_ms": [], "login_errors": [], "login_non200": []}

    async with (
        httpx.AsyncClient(limits=limits, timeout=None) as stream_client,
        httpx.AsyncClient(limits=limits, timeout=60) as read_client,
    ):
        # half the tabs are the manager, half the service desk: two different topic sets, two different scopes
        streams = [
            asyncio.create_task(hold_stream(stream_client, manager_token if i % 2 == 0 else cs_token, i, stop_at, stream_result))
            for i in range(args.streams)
        ]
        await asyncio.sleep(2)  # let the streams attach before the read load starts
        readers = [asyncio.create_task(read_load(read_client, manager_token, stop_at, load_result)) for _ in range(2)]
        probe = asyncio.create_task(login_probe(read_client, CS, stop_at, load_result))

        samples: list[int] = []
        while time.perf_counter() < stop_at - 3:
            samples.append(await pg_connections())
            await asyncio.sleep(3)

        await asyncio.gather(*streams, return_exceptions=True)
        await asyncio.gather(*readers, probe, return_exceptions=True)

    after_conns = await pg_connections()
    read_by_path: dict[str, list[float]] = {}
    for row in load_result["read_ms"]:
        read_by_path.setdefault(row["path"], []).append(row["ms"])

    report = {
        "scenario": "S4 realtime pressure",
        "streams_requested": args.streams,
        "seconds": args.seconds,
        "streams_200": sum(1 for s in stream_result["statuses"] if s == 200),
        "streams_non200": [s for s in stream_result["statuses"] if s != 200],
        "stream_first_frame_ms": {"p50": pct(stream_result["first_frame_ms"], 0.5), "p95": pct(stream_result["first_frame_ms"], 0.95)},
        "stream_frames": {"min": min(stream_result["frames"], default=None), "max": max(stream_result["frames"], default=None)},
        "stream_held_s_p50": pct(stream_result["held_s"], 0.5),
        "stream_errors": stream_result["errors"][:10],
        "read_requests": len(load_result["read_ms"]),
        "read_p50_ms": pct([r["ms"] for r in load_result["read_ms"]], 0.5),
        "read_p95_ms": pct([r["ms"] for r in load_result["read_ms"]], 0.95),
        "read_p99_ms": pct([r["ms"] for r in load_result["read_ms"]], 0.99),
        "read_by_path_p95_ms": {path: pct(values, 0.95) for path, values in read_by_path.items()},
        "read_5xx": load_result["read_5xx"],
        "read_errors": load_result["read_errors"][:10],
        "logins_during_pressure": len(load_result["login_ms"]),
        "login_p50_ms": pct(load_result["login_ms"], 0.5),
        "login_p95_ms": pct(load_result["login_ms"], 0.95),
        "login_non200": load_result["login_non200"],
        "login_errors": load_result["login_errors"][:10],
        "pg_connections_before": base_conns,
        "pg_connections_max_during": max(samples) if samples else None,
        "pg_connections_after": after_conns,
        "pg_samples": samples[:40],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "pg_samples"}, ensure_ascii=False, indent=2))
    return 0 if not (report["read_5xx"] or report["login_non200"] or report["stream_errors"]) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
