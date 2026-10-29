#!/usr/bin/env python3
"""Gate the coverage the spec demands on the *core* modules, not on the whole app average.

Spec §10: ≥80% for SLA, scope/can-access, lead conversion, the insured importer and the portal. A global
percentage hides those five behind `__init__.py` noise, so the CI step measures them one by one.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

CORE = [
    "app/domain/sla.py",
    "app/domain/phone.py",
    "app/domain/lead_fsm.py",
    "app/core/scope.py",
    "app/services/leads.py",
    "app/services/clients.py",
    "app/services/portal.py",
    "app/services/intake.py",
    "app/services/requests_svc.py",
    "app/services/users.py",
    "app/services/masters.py",
    "app/services/audit.py",
    # the two services this round added, gated from the day they landed: a sheet importer whose dedupe or
    # phone branch quietly loses coverage is exactly what the next person will not notice
    "app/services/lead_import.py",
    "app/services/team.py",
    # R10's money layer, gated from the round that added it (R10.1): the ledger, the executive dashboard and
    # the installment receipt are the three places where a wrong number is a *financial* statement, not a
    # cosmetic one. Measured on the full suite: see docs/qa/r10-basic-erp-2026-10-28.md §9.
    "app/services/finance.py",
    "app/services/executive.py",
    "app/services/installments.py",
]
FLOOR = 80.0


def main() -> int:
    """Read `coverage json`, not `coverage report`.

    `[tool.coverage.report] skip_covered = true` hides every 100% file from the text report, so a
    perfectly covered core module used to be read as "missing" and fail CI. The JSON report is not
    affected by that display option.
    """
    root = Path(__file__).resolve().parents[1]
    dump = root / ".core-coverage.json"
    proc = subprocess.run(
        ["coverage", "json", "-o", str(dump), "--include=" + ",".join(CORE)],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if not dump.exists():
        print(proc.stderr or proc.stdout, file=sys.stderr)
        return 2

    rows: dict[str, float] = {}
    try:
        data = json.loads(dump.read_text())
    except json.JSONDecodeError as exc:  # coverage wrote something we cannot read
        print(f"unparsable coverage json: {exc}", file=sys.stderr)
        return 2
    finally:
        dump.unlink(missing_ok=True)
    for name, entry in data.get("files", {}).items():
        rows[name.replace("\\", "/")] = float(entry["summary"]["percent_covered"])
    if proc.returncode != 0 and not rows:
        print(proc.stdout, file=sys.stderr)
        return 2

    failed = False
    for name in CORE:
        pct = rows.get(name)
        status = "ok" if pct is not None and pct >= FLOOR else "FAIL"
        if status == "FAIL":
            failed = True
        shown = "missing" if pct is None else f"{pct:.1f}"
        print(f"{status:4} {name:38} {shown}%")
    if failed:
        print(f"\ncore coverage must reach {FLOOR:.0f}% per module (spec §10)", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
