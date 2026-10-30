"""The real Postgres bus: two hubs, one database, one channel.

`test_realtime_fanout.py` proves the hub's contract with a stand-in. This proves the thing itself — LISTEN,
NOTIFY, and the shared counter — against a live server, because the failure mode this whole mechanism exists
to prevent is *silent*: a publish that reaches nobody looks exactly like a tab with nothing to update.

Skipped unless `MANTIQ_TEST_POSTGRES_URL` points at a server (CI and the R15 load runs set it); the suite
must stay runnable on the SQLite dev path.
"""

from __future__ import annotations

import asyncio
import os

import pytest
import sqlalchemy as sa
from app.services.realtime import RealtimeHub
from app.services.realtime_bus import COUNTER_KEY, PostgresBus
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = os.getenv("MANTIQ_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="set MANTIQ_TEST_POSTGRES_URL to a live PostgreSQL to run")


async def _wait_for(predicate, *, budget: float = 3.0) -> bool:
    deadline = asyncio.get_running_loop().time() + budget
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


@pytest.fixture
async def pg():  # type: ignore[no-untyped-def]
    engine = create_async_engine(URL, future=True)
    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                """
                CREATE TABLE IF NOT EXISTS request_number_counter (
                    key VARCHAR(16) PRIMARY KEY,
                    last_value BIGINT NOT NULL
                )
                """
            )
        )
    yield engine, async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_a_change_published_by_one_worker_reaches_a_subscriber_on_another(pg) -> None:  # type: ignore[no-untyped-def]
    engine, sessionmaker = pg
    worker_a, worker_b = RealtimeHub(), RealtimeHub()
    bus_a = PostgresBus(engine=engine, sessionmaker=sessionmaker, hub=worker_a)
    bus_b = PostgresBus(engine=engine, sessionmaker=sessionmaker, hub=worker_b)
    assert await bus_a.start() is True
    assert await bus_b.start() is True
    try:
        watcher = worker_b.subscribe(topics=frozenset({"requests"}), id_visible=frozenset({"requests"}))
        worker_a.publish(topic="requests", action="updated", entity_id="r-42")

        assert await _wait_for(lambda: not watcher.queue.empty()), "the other worker's tab heard nothing"
        event = watcher.queue.get_nowait()
        assert event is not None
        assert (event["topic"], event["action"], event["entity_id"]) == ("requests", "updated", "r-42")
        # both workers now agree on what `since` means, which is the whole point of the shared counter
        assert worker_a.revision == worker_b.revision == event["revision"]
    finally:
        await bus_a.stop()
        await bus_b.stop()


async def test_the_revision_is_monotonic_across_workers(pg) -> None:  # type: ignore[no-untyped-def]
    engine, sessionmaker = pg
    worker_a, worker_b = RealtimeHub(), RealtimeHub()
    bus_a = PostgresBus(engine=engine, sessionmaker=sessionmaker, hub=worker_a)
    bus_b = PostgresBus(engine=engine, sessionmaker=sessionmaker, hub=worker_b)
    await bus_a.start()
    await bus_b.start()
    try:
        for index in range(4):
            (worker_a if index % 2 else worker_b).publish(topic="leads", action="updated")
        # four publishes from two workers must produce four distinct, increasing revisions everywhere
        assert await _wait_for(lambda: min(worker_a.revision, worker_b.revision) > 0)
        await asyncio.sleep(0.2)
        assert worker_a.revision == worker_b.revision
        revisions = [change.revision for change in worker_a._buffer]
        assert revisions == sorted(set(revisions)), "a shared counter must never repeat or go backwards"
    finally:
        await bus_a.stop()
        await bus_b.stop()


async def test_the_counter_row_is_namespaced_and_the_ids_still_strip(pg) -> None:  # type: ignore[no-untyped-def]
    engine, sessionmaker = pg
    hub = RealtimeHub()
    bus = PostgresBus(engine=engine, sessionmaker=sessionmaker, hub=hub)
    await bus.start()
    try:
        rep = hub.subscribe(topics=frozenset({"leads"}), id_visible=frozenset())
        hub.publish(topic="leads", action="updated", entity_id="lead-1")
        assert await _wait_for(lambda: not rep.queue.empty())
        event = rep.queue.get_nowait()
        assert event is not None and "entity_id" not in event, "the database channel must not widen who sees ids"
        async with sessionmaker() as session:
            value = (
                await session.execute(sa.text("SELECT last_value FROM request_number_counter WHERE key = :key"), {"key": COUNTER_KEY})
            ).scalar_one()
        assert value == event["revision"]
    finally:
        await bus.stop()
