"""Dump the app's OpenAPI to docs/api/openapi.json (the frontend generates its types from this)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import create_app


def main() -> None:
    app = create_app()
    schema = app.openapi()
    target = Path(__file__).resolve().parents[2] / "docs" / "api" / "openapi.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {target} ({len(schema['paths'])} paths, {len(schema.get('components', {}).get('schemas', {}))} schemas)")


if __name__ == "__main__":
    main()
