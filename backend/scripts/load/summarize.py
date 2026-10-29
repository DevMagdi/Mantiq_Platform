"""Turn the raw load artefacts into the numbers the report quotes — one table, no hand-typed cells.

Every value in `docs/qa/load/r8-load-*.md` comes from here. The inputs are the files Locust wrote
(`*_stats.csv` per run) and the JSON written by `sse_pressure.py` / the browser load spec; nothing is
recomputed by hand.

Usage:
    python scripts/load/summarize.py ../docs/qa/load/raw
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

#: the endpoints the acceptance criteria name, plus the ones that turned out to bound capacity
WATCHED = (
    "POST /auth/login",
    "GET /leads",
    "GET /requests?only_open=true",
    "GET /sales/team",
    "GET /leads/board",
    "GET /leads/board?assigned_to",
    "GET /dashboard/summary",
    "GET /clients",
    "POST /leads",
    "POST /leads/{id}/assign",
    "POST /requests/{id}/notes",
)


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def locust_run(stats: Path) -> dict[str, Any]:
    rows = _rows(stats)
    aggregate = next((r for r in rows if r["Name"] == "Aggregated"), None)
    # the `Aggregated` row is the sum of the rows above it — counting it would double every total
    body = [r for r in rows if r["Name"] != "Aggregated"]
    total = sum(int(r["Request Count"]) for r in body)
    failures = sum(int(r["Failure Count"]) for r in body)
    out: dict[str, Any] = {
        "requests": total,
        "failures": failures,
        "error_pct": round(100 * failures / total, 2) if total else 0.0,
        "rps": None,
        "p50": None,
        "p95": None,
        "p99": None,
        "by_endpoint": {},
    }
    if aggregate is not None:
        out.update(
            rps=round(float(aggregate["Requests/s"]), 2),
            p50=int(float(aggregate["50%"])),
            p95=int(float(aggregate["95%"])),
            p99=int(float(aggregate["99%"])),
        )
    for row in rows:
        if row["Name"] in WATCHED:
            out["by_endpoint"][row["Name"]] = {
                "n": int(row["Request Count"]),
                "fails": int(row["Failure Count"]),
                "p50": int(float(row["50%"])),
                "p95": int(float(row["95%"])),
                "p99": int(float(row["99%"])),
            }
    return out


def main(argv: list[str]) -> int:
    raw = Path(argv[1] if len(argv) > 1 else "../docs/qa/load/raw")
    print(f"# raw artefacts in {raw}\n")
    for stats in sorted(raw.glob("*_stats.csv")):
        run = stats.name.replace("_stats.csv", "")
        data = locust_run(stats)
        print(f"## {run}")
        print(
            f"requests={data['requests']} failures={data['failures']} error%={data['error_pct']} "
            f"rps={data['rps']} p50={data['p50']}ms p95={data['p95']}ms p99={data['p99']}ms"
        )
        for name, row in sorted(data["by_endpoint"].items()):
            print(f"  {name:32} n={row['n']:>5} fails={row['fails']:>3} p50={row['p50']:>5} p95={row['p95']:>5} p99={row['p99']:>6}")
        print()

    for name in ("s4-realtime.json", "ci-s4-realtime.json", "ci-s3-browser.json"):
        path = raw / name
        if not path.exists():
            continue
        report = json.loads(path.read_text(encoding="utf-8"))
        print(f"## {name}")
        for key in (
            "streams",
            "streams_200",
            "first_frame_p50_ms",
            "first_frame_p95_ms",
            "read_p95_ms",
            "logins",
            "login_p50_ms",
            "login_p95_ms",
            "login_non200",
            "read_5xx",
            "stream_errors",
            "pg_connections_max_during",
            "pg_connections_after",
            "users",
            "flows",
            "flows_ok_pct",
            "step_p95_ms",
            "pageerror_count",
            "server_5xx_count",
        ):
            if key in report:
                print(f"  {key} = {report[key]}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
