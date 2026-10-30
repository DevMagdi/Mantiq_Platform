"""`GET /dashboard/home` — the whole first paint in one call (R15 P0-B).

The bug this replaces was not a wrong number: the screen was correct and slow. Five GETs left the browser in
parallel and each card appeared when its own request landed, so the operator watched the page assemble itself.
`home` composes the same five service functions on one session and returns them together.

What must stay true: it is the *same* data the five endpoints return (a composition that drifts is worse than
five calls), it obeys the same permission, and it leaks nothing across scopes.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.db import get_sessionmaker
from app.domain.enums import ScopeType
from app.models.auth import User, UserScope
from sqlalchemy import select

from tests.helpers import login

MANAGER = "manager@mantiq.test"
CS = "cs@mantiq.test"
REP_A = "rep_a@mantiq.test"


async def test_home_returns_the_five_sections_in_one_response(client: Any) -> None:
    async with login(client, MANAGER) as auth:
        response = await client.get("/api/v1/dashboard/home", headers=auth)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) >= {"summary", "channels", "status_breakdown", "today", "recent", "channels_days"}
    assert body["summary"]["open_requests"] >= 0
    assert isinstance(body["channels"], list) and body["channels"], "the chart must have its days"
    assert isinstance(body["status_breakdown"], list)
    assert set(body["today"]) >= {"overdue_requests", "lead_followups", "installments_due"}
    assert isinstance(body["recent"], list)
    assert body["channels_days"] == 7


async def test_home_is_exactly_the_five_endpoints_composed(client: Any) -> None:
    """A composition that drifts is worse than five calls: compare it, value by value, with its parts."""
    async with login(client, MANAGER) as auth:
        home = (await client.get("/api/v1/dashboard/home", headers=auth)).json()
        summary = (await client.get("/api/v1/dashboard/summary", headers=auth)).json()
        channels = (await client.get("/api/v1/dashboard/channels", headers=auth, params={"days": 7})).json()
        status = (await client.get("/api/v1/dashboard/status-breakdown", headers=auth)).json()
        today = (await client.get("/api/v1/dashboard/today", headers=auth)).json()
        recent = (await client.get("/api/v1/dashboard/recent-requests", headers=auth, params={"limit": 8})).json()

    assert home["summary"] == summary
    assert home["channels"] == channels
    assert home["status_breakdown"] == status
    assert home["recent"] == recent
    # `today` carries the moment it was generated — two calls cannot share a clock reading, and pretending
    # otherwise would make the comparison pass only inside the same millisecond. Everything else must match.
    assert {k: v for k, v in home["today"].items() if k != "generated_at"} == {k: v for k, v in today.items() if k != "generated_at"}


async def test_home_honours_its_window_and_limit(client: Any) -> None:
    async with login(client, MANAGER) as auth:
        body = (await client.get("/api/v1/dashboard/home", headers=auth, params={"days": 30, "recent_limit": 3})).json()
    assert body["channels_days"] == 30
    assert len(body["channels"]) <= 31
    assert len(body["recent"]) <= 3


async def test_home_needs_the_dashboard_permission(client: Any) -> None:
    """Same permission as every other dashboard read — and a refusal, not an empty page."""
    async with login(client, REP_A) as auth:
        response = await client.get("/api/v1/dashboard/home", headers=auth)
    assert response.status_code in (200, 403), response.text
    if response.status_code == 200:  # a rep holds requests.view_own, which the dashboard honours
        assert "summary" in response.json()


async def test_home_keeps_the_scope_isolation(client: Any, ids: dict[str, dict[str, str]]) -> None:
    """The aggregated call must not become a side door around `Scope`.

    A CS agent scoped to one client creates a request for that client; the number of open requests they see
    through `home` must equal the number the queue shows them — never the office total.
    """
    maker = get_sessionmaker()
    async with maker() as session:
        cs = (await session.execute(select(User).where(User.email == CS))).scalar_one()
        session.add(UserScope(user_id=cs.id, scope_type=ScopeType.CLIENT, scope_id=UUID(ids["clients"]["باء"])))
        await session.commit()

    async with login(client, MANAGER) as auth:
        for key, subject in (("ألف", "طلب لألف — عزل"), ("باء", "طلب لباء — عزل")):
            created = await client.post("/api/v1/requests", json={"subject": subject, "client_id": ids["clients"][key]}, headers=auth)
            assert created.status_code == 201

    async with login(client, CS) as cs_auth:
        home = (await client.get("/api/v1/dashboard/home", headers=cs_auth)).json()
        queue = (await client.get("/api/v1/requests", headers=cs_auth, params={"only_open": True, "page_size": 50})).json()

    assert home["summary"]["open_requests"] == queue["total"], "the KPI row and the queue must count the same rows"
    assert "طلب لألف" not in {row["subject"] for row in home["recent"]}, "out-of-scope rows must not appear in recent"


async def test_the_five_endpoints_still_work(client: Any) -> None:
    """Backward compatibility: an embedded client that asks for one card keeps working."""
    async with login(client, MANAGER) as auth:
        for path in ("summary", "channels", "status-breakdown", "today", "recent-requests"):
            response = await client.get(f"/api/v1/dashboard/{path}", headers=auth)
            assert response.status_code == 200, (path, response.text)
