"""The product is Mantiq. Anything a customer can read must say so.

R13 renamed the brand and swept the UI, but a rebrand regresses the moment someone adds a document
header or an e-mail subject from memory. These tests fail on the *customer-visible* surfaces only —
PDF letterheads, spreadsheets, outbound e-mail, the OpenAPI metadata, the simulator panel — and leave
internal identifiers alone (a Postgres role or a Prometheus metric is not a brand).

`ALLOWED_INTERNAL_NAMES` is the explicit list of the names that were deliberately not renamed; see
ADR-034 §7 and docs/PRODUCTION_READINESS.md. Adding a new entry is a decision, not a workaround.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"

#: Customer-facing modules: everything in here is read by a person, so it must carry the Mantiq name.
CUSTOMER_VISIBLE = [
    "services/documents.py",  # policy PDF
    "services/exports.py",  # XLSX / CSV
    "services/intake.py",  # notifications raised from the inbox
    "services/leads.py",  # lead hand-off copy
    "services/integrations_settings.py",  # the settings screen labels
    "api/v1/endpoints/sim_panel.html",  # the simulator page
    "main.py",  # OpenAPI title / description
]

OLD_BRAND = re.compile(r"broker[\s_-]*hub", re.IGNORECASE)


@pytest.mark.parametrize("relative", CUSTOMER_VISIBLE)
def test_no_old_brand_on_a_customer_visible_surface(relative: str) -> None:
    path = APP / relative
    assert path.exists(), f"{relative} moved: update CUSTOMER_VISIBLE instead of deleting the test"
    hits = [
        f"{relative}:{number}: {line.strip()}"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if OLD_BRAND.search(line)
    ]
    assert not hits, "old brand on a customer-visible surface:\n" + "\n".join(hits)


def test_the_openapi_metadata_says_mantiq() -> None:
    """The API title is what `/docs` and every client generator show."""

    from app.core.config import get_settings

    assert get_settings().APP_NAME == "Mantiq"
    main_source = (APP / "main.py").read_text(encoding="utf-8")
    assert 'title="Mantiq API"' in main_source or "title=f\"{settings.APP_NAME}" in main_source


def test_the_document_and_export_headers_say_mantiq() -> None:
    for relative in ("services/documents.py", "services/exports.py"):
        source = (APP / relative).read_text(encoding="utf-8")
        assert "Mantiq" in source, f"{relative} should stamp the product name"
        assert "Mantiq Technologies" in source, f"{relative} should carry the legal line"


# --- internal identifiers: documented, not renamed ---------------------------------------------
#
# Each of these is a name that survives in the codebase on purpose. They are listed here so a future
# reader (or an auditor) sees the decision instead of finding a stray `brokerhub` and guessing.

ALLOWED_INTERNAL_NAMES: dict[str, str] = {
    "brokerhub_app": "Postgres application role (RLS). Renaming is a migration with a lock window.",
    "brokerhub_load": "Postgres load-test role, CI only.",
    "brokerhub": "Database name in the dev compose and the DATABASE_URL default.",
    "broker_hub": "Celery app name and task names (queued-message compatibility).",
    "broker_hub_": "Prometheus metric prefix (dashboards and alerts reference it).",
    "@broker-hub/frontend": "npm package scope of a private, unpublished package.",
    "bh.lang": "localStorage key holding the user's language choice.",
}


def test_the_internal_allowlist_is_still_accurate() -> None:
    """If an allowed name disappears from the code, drop it from the table rather than leaving dead docs.

    The scan covers the whole repository: three of these names live outside `backend/app/`
    (`brokerhub_load` in the load scripts, `@broker-hub/frontend` in package.json, `bh.lang` in the SPA).
    """
    repo = APP.parents[1]
    suffixes = {".py", ".html", ".ts", ".tsx", ".json", ".yml", ".yaml", ".md", ".example"}
    sources = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in repo.rglob("*")
        if path.is_file()
        and path.suffix in suffixes
        and "node_modules" not in path.parts
        and "dist" not in path.parts
        and "__pycache__" not in path.parts
    )
    stale = [name for name in ALLOWED_INTERNAL_NAMES if name not in sources]
    assert not stale, f"no longer present anywhere in app/: {stale}"
