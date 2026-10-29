"""How fast can people log in, and where does the 800 ms acceptance line break?

R8 §6 asks for **p95 login < 800 ms**. The ramp in `locustfile.py` spawns every user at the same instant, so
its login sample is one synchronized burst of 25 Argon2 verifies — a thundering herd, not a login rate. This
tool measures the thing the criterion actually describes: a steady stream of logins at a known rate, with
no other traffic, reported per rate. Each login is a fresh HTTP client (a new browser would have a new cookie
jar), so nothing is cached.

Every hash is `~110 ms` of Argon2 (`app/services/auth.py` runs it off the event loop, but the CPU is still
the CPU), so the expected knee is around one login per core — this run is where that becomes a number.

Usage:
    python scripts/load/login_probe.py --rates 2,5,10 --seconds 30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any

import httpx

BASE = os.environ.get("LOAD_BASE", "http://127.0.0.1:8020")
EMAIL = os.environ.get("LOAD_MANAGER", "manager@brokerhub.test")
PASSWORD = os.environ.get("LOAD_PASSWORD", "Br0kerHub!2026")


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return round(ordered[index], 1)


async def _one_login(client: httpx.AsyncClient) -> tuple[float, int]:
    started = time.perf_counter()
    response = await client.post(f"{BASE}/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    elapsed_ms = (time.perf_counter() - started) * 1000
    return elapsed_ms, response.status_code


async def run_rate(rate: float, seconds: float) -> dict[str, Any]:
    interval = 1.0 / rate
    latencies: list[float] = []
    codes: dict[int, int] = {}
    async with httpx.AsyncClient(timeout=60.0) as client:
        started = time.perf_counter()
        pending: list[asyncio.Task[tuple[float, int]]] = []
        next_at = started
        while time.perf_counter() - started < seconds:
            delay = next_at - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            pending.append(asyncio.create_task(_one_login(client)))
            next_at += interval
        for task in pending:
            elapsed_ms, code = await task
            latencies.append(elapsed_ms)
            codes[code] = codes.get(code, 0) + 1
    return {
        "rate_per_s": rate,
        "seconds": seconds,
        "logins": len(latencies),
        "p50_ms": _pct(latencies, 0.50),
        "p95_ms": _pct(latencies, 0.95),
        "p99_ms": _pct(latencies, 0.99),
        "max_ms": round(max(latencies), 1) if latencies else 0.0,
        "mean_ms": round(statistics.fmean(latencies), 1) if latencies else 0.0,
        "codes": {str(k): v for k, v in sorted(codes.items())},
        "non_200": sum(v for k, v in codes.items() if k != 200),
    }


def _write(path: str, report: dict[str, Any]) -> None:
    """Small blocking write, kept out of the async path so the event loop never blocks on disk (ASYNC240)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rates", default="2,5,10")
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    report: dict[str, Any] = {"base": BASE, "email": EMAIL, "rates": []}
    for chunk in args.rates.split(","):
        rate = float(chunk)
        result = await run_rate(rate, args.seconds)
        report["rates"].append(result)
        print(
            f"{rate:>5.1f} logins/s → {result['logins']:>3} logins · p50 {result['p50_ms']:>6} ms · "
            f"p95 {result['p95_ms']:>6} ms · p99 {result['p99_ms']:>6} ms · max {result['max_ms']:>6} ms · "
            f"non-200 {result['non_200']}"
        )

    failing = [r for r in report["rates"] if r["non_200"] or r["p95_ms"] >= 800]
    report["p95_under_800_at_rates"] = [r["rate_per_s"] for r in report["rates"] if r["p95_ms"] < 800 and not r["non_200"]]
    if args.out:
        _write(args.out, report)
    return 1 if failing and all(r["p95_ms"] >= 800 for r in report["rates"]) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
