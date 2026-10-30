"""The simulator panel's «فتح صندوق الطلبات» link (item 5).

The panel is served by the **API** (`GET /api/v1/dev/sim`, uvicorn on :8000) while the screen it links to is
served by the **SPA** (Vite on :5173, a different host in production). Building the link from the request
origin — the obvious one-liner — sends the developer to `:8000/inbox`, a 404 from a page whose whole purpose
is "show me what just happened".

So the address is configuration (`FRONTEND_URL`), injected into the page.
"""

from __future__ import annotations

import json
from typing import Any

from app.api.v1.endpoints import dev_sim
from app.core.config import get_settings


def test_an_unset_frontend_url_falls_back_to_the_deployment_origin(monkeypatch: Any) -> None:
    """Compose does not pass an unlisted variable, so an empty `FRONTEND_URL` in production must still work.

    The fallback order is `FRONTEND_URL` → `PUBLIC_BASE_URL` (required in prod) → the dev default; a
    single-domain deployment keeps both on the same host, and a fresh local `.env` gets :5173.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "FRONTEND_URL", "")
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://app.example.com/")
    assert settings.frontend_url == "https://app.example.com"
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "")
    assert settings.frontend_url == "http://localhost:5173"


def test_the_panel_is_served_with_the_configured_frontend_url() -> None:
    html = dev_sim._panel_html("http://localhost:5173")
    assert 'const FRONTEND = "http://localhost:5173";' in html
    assert dev_sim._FRONTEND_SLOT not in html, "the placeholder must never reach the browser"


def test_a_trailing_slash_does_not_produce_a_double_slash() -> None:
    html = dev_sim._panel_html("https://app.example.com/")
    assert 'const FRONTEND = "https://app.example.com";' in html
    assert "https://app.example.com//inbox" not in html


def test_a_quote_in_the_setting_cannot_break_out_of_the_script() -> None:
    """`FRONTEND_URL` is operator input and the page is HTML. `json.dumps` is the barrier, so it is tested."""
    tricky = 'https://x.test/" ; alert(1); //x'
    html = dev_sim._panel_html(tricky)
    line = next(row for row in html.splitlines() if row.strip().startswith("const FRONTEND ="))
    # the whole value has to sit inside one escaped literal — nothing after the quote can become code
    assert line.strip() == f"const FRONTEND = {json.dumps(tricky)};"


async def test_the_served_page_links_to_the_spa_not_to_the_api(client: Any, monkeypatch: Any) -> None:
    """End-to-end through the route: what the browser actually receives."""
    monkeypatch.setattr(get_settings(), "FRONTEND_URL", "http://localhost:5173")
    response = await client.get("/api/v1/dev/sim")
    assert response.status_code == 200, response.text
    body = response.text
    assert 'const FRONTEND = "http://localhost:5173";' in body
    assert 'location.href = FRONTEND + "/inbox"' in body
    # the old bug, verbatim: a bare "/inbox" is resolved against the API origin and 404s
    assert 'location.href = "/inbox"' not in body


def test_the_panel_carries_no_legacy_indigo_or_beige_identity() -> None:
    """The dev panel is user-visible; the palette rule (ADR-034) is not optional because a page is internal."""
    html = dev_sim._panel_html("http://localhost:5173").lower()
    assert "#4f46e5" not in html, "legacy indigo primary"
    assert "#f3f4f9" not in html, "old off-white identity, replaced by #FAFBFC/#FFFFFF"
    assert "#bda77e" in html, "Mantiq Gold is the accent"
    assert "#0e1728" in html, "Deep Navy ink"
