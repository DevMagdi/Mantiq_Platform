"""Real-time feed and the overdue deep link (ADR-026, spec §R6 items 1–2).

Two things are proven here and nowhere else:

1. **the feed actually carries a change from one connection to another** — a second client is watching the
   stream while the first mutates, and the frame that arrives is asserted (topic, action, revision);
2. **the KPI and the list it links to cannot drift** — both read `overdue_condition()`, and this file fails
   if the number on the card differs from the number of rows behind it.

What is *not* claimed: that the browser re-renders. That is the e2e suite's job (`e2e/realtime.spec.ts`).
"""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from app.core.db import get_sessionmaker
from app.domain.enums import RequestStatus
from app.models.requests import ServiceRequest
from app.services import realtime

from tests.conftest import PASSWORD
from tests.helpers import login


@pytest.fixture(autouse=True)
def _fresh_feed() -> Any:
    """The hub is a process singleton: without this, one test's changes are the next test's replay."""
    realtime.hub.reset()
    yield
    realtime.hub.reset()


MANAGER = "manager@brokerhub.test"
REP_A = "rep_a@brokerhub.test"
CS = "cs@brokerhub.test"


# ---------------------------------------------------------------------------------------------
# the hub itself: ordering, replay, gaps, filtering
# ---------------------------------------------------------------------------------------------
def test_revisions_are_monotonic_and_per_topic() -> None:
    instance = realtime.RealtimeHub(buffer_size=10)
    first = instance.publish(topic="requests", action="created")
    second = instance.publish(topic="leads", action="updated")
    third = instance.publish(topic="requests", action="updated")

    assert (first.revision, second.revision, third.revision) == (1, 2, 3)
    assert instance.topic_revisions() == {"requests": 3, "leads": 2}


def test_a_client_replays_what_it_missed_and_is_told_when_it_cannot() -> None:
    instance = realtime.RealtimeHub(buffer_size=2)
    for _ in range(3):
        instance.publish(topic="requests", action="updated")

    replay, resync = instance.backlog(1, topics=frozenset({"requests"}), id_visible=frozenset())
    assert resync is False
    assert [event["revision"] for event in replay] == [2, 3]

    # `since=0` is a brand-new tab, not a tab that missed something: it has no gap to be told about, because
    # it is about to fetch everything fresh anyway. Replaying history to it was the R6 defect (ADR-026 §3).
    replay, resync = instance.backlog(0, topics=frozenset({"requests"}), id_visible=frozenset())
    assert (replay, resync) == ([], False)

    # a client that really did miss changes older than the buffer is told to resync instead
    for _ in range(2):
        instance.publish(topic="requests", action="updated")  # buffer now holds 4, 5 — revision 1 is gone
    replay, gap = instance.backlog(1, topics=frozenset({"requests"}), id_visible=frozenset())
    assert (replay, gap) == ([], True)


def test_a_full_queue_marks_the_subscriber_instead_of_growing_without_bound() -> None:
    instance = realtime.RealtimeHub()
    subscription = instance.subscribe(topics=frozenset({"requests"}), id_visible=frozenset())
    for _ in range(subscription.queue.maxsize + 5):
        instance.publish(topic="requests", action="updated")
    assert subscription.overflowed is True
    assert subscription.queue.qsize() <= subscription.queue.maxsize


def test_a_rep_is_told_about_leads_without_being_told_which_lead() -> None:
    """`leads.view_own` makes the topic relevant; only `leads.view_all` makes an id safe to include."""
    rep = frozenset({"leads.view_own", "clients.view_own"})
    manager = frozenset({"leads.view_all", "clients.view_all", "requests.view", "settings.manage"})

    assert realtime.topics_for(perms=rep, is_portal=False) == frozenset({"leads", "clients"})
    assert realtime.id_visible_for(perms=rep, is_portal=False) == frozenset()
    assert {"leads", "clients", "requests", "settings"} <= realtime.topics_for(perms=manager, is_portal=False)
    assert "settings" in realtime.id_visible_for(perms=manager, is_portal=False)


def test_a_portal_client_gets_its_two_topics_with_all_ids_stripped() -> None:
    assert realtime.topics_for(perms=frozenset(), is_portal=True) == frozenset({"requests", "policies"})
    assert realtime.id_visible_for(perms=frozenset(), is_portal=True) == frozenset()


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/api/v1/requests", None),
        ("POST", "/api/v1/requests", "requests"),
        ("PATCH", "/api/v1/requests/6f1c2f7e-0000-4000-8000-000000000000/status", "requests"),
        ("POST", "/api/v1/webhooks/whatsapp", "requests"),
        ("POST", "/api/v1/leads/6f1c2f7e-0000-4000-8000-000000000000/assign", "leads"),
        ("PUT", "/api/v1/clients/6f1c2f7e-0000-4000-8000-000000000000", "clients"),
        ("POST", "/api/v1/policies/6f1c2f7e-0000-4000-8000-000000000000/issue", "policies"),
        ("POST", "/api/v1/settings/integrations/whatsapp/live", "settings"),
        ("POST", "/api/v1/auth/login", None),
        ("POST", "/api/v1/realtime/nothing", None),
    ],
)
def test_path_mapping_is_a_closed_table(method: str, path: str, expected: str | None) -> None:
    assert realtime.topic_for(method, path) == expected


def test_entity_id_is_the_last_uuid_in_the_path() -> None:
    one = "6f1c2f7e-0000-4000-8000-000000000001"
    two = "6f1c2f7e-0000-4000-8000-000000000002"
    assert realtime.entity_from_path(f"/api/v1/clients/{one}/portal-accounts/{two}/password") == two
    assert realtime.entity_from_path("/api/v1/requests") is None


# ---------------------------------------------------------------------------------------------
# the HTTP surface
# ---------------------------------------------------------------------------------------------
async def test_the_stream_refuses_an_anonymous_caller(client) -> None:
    response = await client.get("/api/v1/realtime/stream")
    assert response.status_code == 401


async def test_the_revision_signature_is_scoped_to_the_caller(client) -> None:
    async with login(client, MANAGER) as manager:
        payload = (await client.get("/api/v1/realtime/revision", headers=manager)).json()
    assert {"revision", "topics", "subscribers"} <= set(payload)
    assert payload["revision"] >= 0

    async with login(client, REP_A) as rep:
        rep_payload = (await client.get("/api/v1/realtime/revision", headers=rep)).json()
    # a rep has no `requests.view` and no `settings.manage`: those topics are not even named to them
    assert "settings" not in rep_payload["topics"]
    assert "requests" not in rep_payload["topics"]


@asynccontextmanager
async def served_app() -> AsyncIterator[str]:
    """A real uvicorn server on a free port, in this test's own loop.

    `httpx.ASGITransport` buffers a response until the ASGI app returns, which never happens for an endless
    `text/event-stream` — the first version of this file hung for 15 minutes proving nothing. A loopback
    socket is the only honest way to test a stream: same event loop (so the engine stays bound to it), real
    HTTP framing, and the real middleware stack in between.
    """
    import uvicorn
    from app.main import create_app

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.05)
    assert server.started, "uvicorn did not start"
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)


async def _next_frame(lines: Any, *, event: str) -> dict[str, Any]:
    """Read SSE frames until `event` arrives. Fails with the frames seen so far, never with a bare timeout."""
    seen: list[str] = []

    async def read() -> dict[str, Any]:
        name = ""
        async for line in lines:
            if not line:
                continue
            seen.append(line)
            if line.startswith("event: "):
                name = line[7:].strip()
            elif line.startswith("data: ") and name == event:
                return json.loads(line[6:])
        raise AssertionError(f"stream ended before `{event}` — saw: {seen}")

    return await asyncio.wait_for(read(), timeout=5.0)


async def test_an_open_stream_does_not_pin_a_database_connection(ids: dict[str, dict[str, str]]) -> None:
    """R7: a stream must cost a subscription and a queue, never a pooled connection.

    The route used to take `CurrentUser`/`ScopeDep`, and a dependency-yielded session lives until the
    response ends — which for `text/event-stream` is as long as the tab is open. Every open tab therefore held
    one connection for minutes; the e2e run answered `QueuePool limit of size 5 overflow 10 reached` after
    30 s on `/auth/login` while the browsers were merely sitting there with their streams open.

    The engine here has exactly **one** connection, so the fact is not a matter of tuning: if the stream keeps
    a session, the next request cannot be served at all. It is served, in milliseconds.
    """
    import httpx
    from app.core.db import get_engine, set_sessionmaker
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tests.conftest import IS_POSTGRES, TEST_DB, TEST_URL

    one = create_async_engine(TEST_URL if IS_POSTGRES else f"sqlite+aiosqlite:///{TEST_DB}", pool_size=1, max_overflow=0, future=True)
    set_sessionmaker(async_sessionmaker(one, expire_on_commit=False, autoflush=False))
    try:
        async with served_app() as base, httpx.AsyncClient(base_url=base, timeout=10) as http:
            login = await http.post("/api/v1/auth/login", json={"email": MANAGER, "password": PASSWORD})
            auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
            async with http.stream("GET", "/api/v1/realtime/stream", headers=auth) as response:
                assert response.status_code == 200
                lines = response.aiter_lines()
                hello = await _next_frame(lines, event="hello")
                assert hello["revision"] >= 0

                # the only connection in the pool must be free *while* the stream is open
                other = await asyncio.wait_for(http.get("/api/v1/realtime/revision", headers=auth), timeout=5)
                assert other.status_code == 200, other.text
                assert get_engine().pool.checkedout() <= 1  # type: ignore[attr-defined]
    finally:
        await one.dispose()
        set_sessionmaker(async_sessionmaker(get_engine(), expire_on_commit=False, autoflush=False))


async def test_a_change_on_one_connection_reaches_another(ids: dict[str, dict[str, str]]) -> None:
    """The requirement, literally: client B is told within seconds that client A changed a request."""
    import httpx

    async with served_app() as base, httpx.AsyncClient(base_url=base, timeout=10) as http:
        cs = await http.post("/api/v1/auth/login", json={"email": CS, "password": PASSWORD})
        manager = await http.post("/api/v1/auth/login", json={"email": MANAGER, "password": PASSWORD})
        cs_auth = {"Authorization": f"Bearer {cs.json()['access_token']}"}
        manager_auth = {"Authorization": f"Bearer {manager.json()['access_token']}"}

        async with http.stream("GET", "/api/v1/realtime/stream", headers=manager_auth) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            lines = response.aiter_lines()
            hello = await _next_frame(lines, event="hello")
            assert "requests" in hello["topics"]

            created = await http.post(
                "/api/v1/requests",
                json={"subject": "طلب يظهر فورًا للزميل", "client_id": ids["clients"]["ألف"]},
                headers=cs_auth,
            )
            assert created.status_code == 201, created.text
            request_id = created.json()["id"]

            creation = await _next_frame(lines, event="change")
            assert creation["topic"] == "requests"
            assert creation["action"] == "created"
            assert creation["revision"] > hello["revision"]
            # a create has no id in the URL, so the frame carries none — the client refetches the list anyway
            assert "entity_id" not in creation

            moved = await http.post(f"/api/v1/requests/{request_id}/status", json={"status": "in_progress"}, headers=cs_auth)
            assert moved.status_code == 200, moved.text

            change = await _next_frame(lines, event="change")
            assert change["topic"] == "requests"
            assert change["action"] == "created" or change["action"] == "updated"
            # the id is in the path *and* the manager holds `requests.view`, so it reaches them
            assert change["entity_id"] == request_id


async def test_a_rep_is_not_told_which_lead_changed(ids: dict[str, dict[str, str]]) -> None:
    import httpx

    async with served_app() as base, httpx.AsyncClient(base_url=base, timeout=10) as http:
        manager = await http.post("/api/v1/auth/login", json={"email": MANAGER, "password": PASSWORD})
        rep = await http.post("/api/v1/auth/login", json={"email": REP_A, "password": PASSWORD})
        manager_auth = {"Authorization": f"Bearer {manager.json()['access_token']}"}
        rep_auth = {"Authorization": f"Bearer {rep.json()['access_token']}"}

        async with http.stream("GET", "/api/v1/realtime/stream", headers=rep_auth) as response:
            lines = response.aiter_lines()
            await _next_frame(lines, event="hello")

            moved = await http.patch(f"/api/v1/leads/{ids['leads']['ألف']}", json={"status": "contacted"}, headers=manager_auth)
            assert moved.status_code == 200, moved.text

            change = await _next_frame(lines, event="change")
        assert change["topic"] == "leads"
        assert "entity_id" not in change
        assert "path" not in change


async def test_since_replays_the_changes_nobody_was_listening_for(ids: dict[str, dict[str, str]]) -> None:
    """A browser that reconnects after a blip gets the gap, not a silent hole."""
    import httpx

    async with served_app() as base, httpx.AsyncClient(base_url=base, timeout=10) as http:
        auth = (await http.post("/api/v1/auth/login", json={"email": MANAGER, "password": PASSWORD})).json()
        headers = {"Authorization": f"Bearer {auth['access_token']}"}

        # one change before the tab opens, so the tab's cursor is a real revision rather than 0 (which now
        # means "I am new, send me nothing old")
        before = await http.post("/api/v1/requests", json={"subject": "قبل الانقطاع", "client_id": ids["clients"]["ألف"]}, headers=headers)
        assert before.status_code == 201

        async with http.stream("GET", "/api/v1/realtime/stream", headers=headers) as response:
            hello = await _next_frame(response.aiter_lines(), event="hello")
        revision = hello["revision"]
        assert revision >= 1

        created = await http.post(
            "/api/v1/requests", json={"subject": "طلب أثناء انقطاع", "client_id": ids["clients"]["ألف"]}, headers=headers
        )
        assert created.status_code == 201

        async with http.stream("GET", f"/api/v1/realtime/stream?since={revision}", headers=headers) as response:
            replay = await _next_frame(response.aiter_lines(), event="change")
        assert replay["revision"] > revision
        assert replay["topic"] == "requests"


async def test_a_brand_new_tab_is_not_replayed_a_history_lesson(ids: dict[str, dict[str, str]]) -> None:
    """`since=0` means "tell me what happens next", not "send me everything you remember".

    This is the difference between a page that is fresh and a page that is busy being caught up. The first
    version replayed the whole 500-entry buffer to every new tab; on a busy instance that is hundreds of
    invalidations at once, and the change that matters (the one made after the page loaded) waits behind
    them. Measured in CI as a row that had not appeared 2 000 ms after it was created.
    """
    import httpx

    async with served_app() as base, httpx.AsyncClient(base_url=base, timeout=10) as http:
        auth = (await http.post("/api/v1/auth/login", json={"email": MANAGER, "password": PASSWORD})).json()
        headers = {"Authorization": f"Bearer {auth['access_token']}"}

        # make some history first, so a replay would have something to send
        for index in range(3):
            made = await http.post(
                "/api/v1/requests",
                json={"subject": f"تاريخ {index}", "client_id": ids["clients"]["ألف"]},
                headers=headers,
            )
            assert made.status_code == 201, made.text

        async with http.stream("GET", "/api/v1/realtime/stream?since=0", headers=headers) as response:
            lines = response.aiter_lines()
            hello = await _next_frame(lines, event="hello")
            assert hello["revision"] >= 3
            # the very next frame must be a *new* change, never a replay of one made before the tab existed
            created = await http.post(
                "/api/v1/requests",
                json={"subject": "الحدث الأول لهذا التبويب", "client_id": ids["clients"]["ألف"]},
                headers=headers,
            )
            assert created.status_code == 201, created.text
            first = await _next_frame(lines, event="change")
        assert first["revision"] > hello["revision"]
        assert first["topic"] == "requests"


# ---------------------------------------------------------------------------------------------
# the overdue KPI and the screen behind it
# ---------------------------------------------------------------------------------------------
async def test_the_overdue_kpi_equals_the_rows_behind_the_deep_link(client, ids: dict[str, dict[str, str]]) -> None:
    """Three rows, one of them counts: an overdue open request.

    The other two are the traps the old filter fell into — an overdue *resolved* request (closed work is not
    a crisis) and a healthy open one. The card and the filtered list must both say exactly 1.
    """
    maker = get_sessionmaker()
    now = datetime.now(UTC)
    async with maker() as session:
        client_id = UUID(ids["clients"]["ألف"])
        session.add_all(
            [
                ServiceRequest(
                    number=900_101,
                    subject="متأخر ومفتوح",
                    client_id=client_id,
                    status=RequestStatus.NEW,
                    channel="manual",
                    is_overdue=True,
                    sla_due_at=now - timedelta(hours=6),
                    last_activity_at=now - timedelta(hours=6),
                ),
                ServiceRequest(
                    number=900_102,
                    subject="متأخر لكن مُغلق",
                    client_id=client_id,
                    status=RequestStatus.RESOLVED,
                    channel="manual",
                    is_overdue=True,
                    sla_due_at=now - timedelta(hours=9),
                    last_activity_at=now - timedelta(hours=9),
                ),
                ServiceRequest(
                    number=900_103,
                    subject="سليم",
                    client_id=client_id,
                    status=RequestStatus.IN_PROGRESS,
                    channel="manual",
                    is_overdue=False,
                    sla_due_at=now + timedelta(hours=4),
                    last_activity_at=now,
                ),
            ]
        )
        await session.commit()

    async with login(client, MANAGER) as auth:
        summary = (await client.get("/api/v1/dashboard/summary", headers=auth)).json()
        listed = (await client.get("/api/v1/requests?only_overdue=true&page_size=50", headers=auth)).json()

    subjects = [row["subject"] for row in listed["items"]]
    assert "متأخر ومفتوح" in subjects
    assert "متأخر لكن مُغلق" not in subjects
    assert "سليم" not in subjects
    assert listed["total"] == 1
    assert summary["overdue"] == listed["total"], "the card and the screen it opens must count the same rows"


async def test_overdue_uses_one_definition_everywhere(client, ids: dict[str, dict[str, str]]) -> None:
    """The status breakdown's «متأخر» slice is the same clause as the KPI, not a second copy."""
    maker = get_sessionmaker()
    async with maker() as session:
        session.add(
            ServiceRequest(
                number=900_104,
                subject="متأخر للتفصيل",
                client_id=UUID(ids["clients"]["باء"]),
                status=RequestStatus.IN_PROGRESS,
                channel="manual",
                is_overdue=True,
                sla_due_at=datetime.now(UTC) - timedelta(hours=3),
                last_activity_at=datetime.now(UTC),
            )
        )
        await session.commit()

    async with login(client, MANAGER) as auth:
        summary = (await client.get("/api/v1/dashboard/summary", headers=auth)).json()
        breakdown = {row["key"]: row["count"] for row in (await client.get("/api/v1/dashboard/status-breakdown", headers=auth)).json()}
    assert breakdown["overdue"] == summary["overdue"]
