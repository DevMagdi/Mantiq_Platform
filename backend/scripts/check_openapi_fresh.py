"""Fail when the committed OpenAPI file no longer matches the app — compared as data, not as text.

Why not `git diff` after re-exporting? The exported JSON also carries cosmetics of the toolchain that
generated it (key insertion order for some members, the `openapi` version string a FastAPI release
starts emitting). A text diff then fails CI for reasons that are not contract changes, and re-running
fixes nothing for the reviewer. So we build the schema in-process and compare parsed JSON, then print
the first differences as annotations so the failure says *what* drifted.

Usage:
    python scripts/check_openapi_fresh.py          # CI check
    python scripts/check_openapi_fresh.py --write  # regenerate docs/api/openapi.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
TARGET = BACKEND.parent / "docs" / "api" / "openapi.json"
sys.path.insert(0, str(BACKEND))


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _diff_paths(a: Any, b: Any, path: str = "", found: list[str] | None = None) -> list[str]:
    """Collect up to a handful of JSON-pointer-ish paths where the two documents disagree."""
    spots = found if found is not None else []
    if len(spots) >= 12:
        return spots
    if type(a) is not type(b):
        spots.append(f"{path or '/'}: type {type(a).__name__} != {type(b).__name__}")
        return spots
    if isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a:
                spots.append(f"{path}/{key}: only in the generated document")
            elif key not in b:
                spots.append(f"{path}/{key}: missing from the generated document")
            else:
                _diff_paths(a[key], b[key], f"{path}/{key}", spots)
            if len(spots) >= 12:
                return spots
        return spots
    if isinstance(a, list):
        if len(a) != len(b):
            spots.append(f"{path}: {len(a)} items committed vs {len(b)} generated")
            return spots
        for index, (left, right) in enumerate(zip(a, b, strict=True)):
            _diff_paths(left, right, f"{path}[{index}]", spots)
        return spots
    if a != b:
        spots.append(f"{path}: {a!r} committed vs {b!r} generated")
    return spots


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the generated document to docs/api/openapi.json")
    args = parser.parse_args()

    from app.main import create_app

    generated = create_app().openapi()
    TARGET.parent.mkdir(parents=True, exist_ok=True)

    if args.write:
        TARGET.write_text(_dump(generated) + "\n", encoding="utf-8")
        print(f"wrote {TARGET}")
        return 0

    if not TARGET.exists():
        print(f"::error::{TARGET} does not exist — run: python backend/scripts/export_openapi.py")
        return 1

    committed = json.loads(TARGET.read_text(encoding="utf-8"))
    if committed == generated:
        print(f"openapi.json is fresh ({len(generated['paths'])} paths)")
        return 0

    try:  # cosmetic-only drift (e.g. the `openapi` version string) is worth reporting, not blocking
        if {k: v for k, v in committed.items() if k != "openapi"} == {k: v for k, v in generated.items() if k != "openapi"}:
            print(f"::warning::only the openapi version string differs ({committed.get('openapi')} -> {generated.get('openapi')})")
            return 0
    except (AttributeError, TypeError):  # pragma: no cover - defensive on malformed documents
        pass

    print("::error::docs/api/openapi.json is stale. Differences (committed vs generated):")
    for spot in _diff_paths(committed, generated):
        print(f"::error::openapi-drift: {spot}")
    print("::error::fix with: python backend/scripts/export_openapi.py")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
