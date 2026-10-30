"""Two workers, one feed (R15 P1-C).

The hub is an in-process object, and that was correct while the deployment ran one uvicorn worker. R15 makes
two the default, so a `publish()` that reaches only the tabs attached to *this* process would leave half the
office looking at a screen that never updates — the worst kind of bug, because it is intermittent and the
screen still looks alive.

These tests pin the two halves that matter:

1. the single-process path is unchanged (no bus → identical behaviour, so dev and the whole existing suite
   keep working), and
2. with a bus attached, a change published on one hub reaches a subscriber on *another* hub, carrying the
   same revision — which is what `?since=` means when a tab reconnects to a different worker.

The Postgres path itself is exercised against a live server in `tests/test_realtime_fanout_pg.py` when one is
configured; here the bus is a stand-in, because the thing under test is the hub's contract with it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.services.realtime import Change, RealtimeHub


class FakeBus:
    """Fan-out without a database: every hub that registers hears every change, stamped once."""

    def __init__(self) -> None:
        self.hubs: list[RealtimeHub] = []
        self.revision = 100  # deliberately not 1: a shared counter is *shared*, not per-process
        self.refuse = False

    def register(self, hub: RealtimeHub) -> None:
        self.hubs.append(hub)
        hub.attach_bus(self)

    def submit(
        self, *, topic: str, action: str, entity_id: str | None = None, actor_id: str | None = None, path: str | None = None
    ) -> bool:
        if self.refuse:
            return False
        self.revision += 1
        change = Change(
            revision=self.revision,
            topic=topic,
            action=action,
            at=datetime.now(UTC),
            entity_id=entity_id,
            actor_id=actor_id,
            path=path,
        )
        for hub in self.hubs:
            hub.deliver(Change.from_wire(change.to_wire()))
        return True


def _drain(subscription: Any) -> list[dict[str, Any]]:
    events = []
    while not subscription.queue.empty():
        item = subscription.queue.get_nowait()
        if item is not None:
            events.append(item)
    return events


def test_a_publish_reaches_a_subscriber_held_by_another_worker() -> None:
    bus = FakeBus()
    tab_on_a, tab_on_b = RealtimeHub(), RealtimeHub()
    bus.register(tab_on_a)
    bus.register(tab_on_b)

    watcher = tab_on_b.subscribe(topics=frozenset({"requests"}), id_visible=frozenset())
    tab_on_a.publish(topic="requests", action="updated", entity_id="r-1")

    events = _drain(watcher)
    assert len(events) == 1, "the tab attached to the other worker must hear the change"
    assert events[0]["topic"] == "requests" and events[0]["action"] == "updated"
    assert events[0]["revision"] == 101, "the revision comes from the shared counter, not from either process"
    assert tab_on_a.revision == tab_on_b.revision == 101, "both workers must agree on `since`"


def test_ids_are_still_stripped_per_subscriber_when_the_change_arrives_through_the_bus() -> None:
    """A broker must not become a side channel around the permission that decides who sees an id."""
    bus = FakeBus()
    worker = RealtimeHub()
    bus.register(worker)

    rep = worker.subscribe(topics=frozenset({"leads"}), id_visible=frozenset())  # holds leads.view_own
    manager = worker.subscribe(topics=frozenset({"leads"}), id_visible=frozenset({"leads"}))

    worker.publish(topic="leads", action="updated", entity_id="lead-9", actor_id="u-2")

    rep_event, manager_event = _drain(rep)[0], _drain(manager)[0]
    assert "entity_id" not in rep_event and "actor_id" not in rep_event
    assert manager_event["entity_id"] == "lead-9" and manager_event["actor_id"] == "u-2"


def test_a_bus_that_refuses_the_message_falls_back_to_local_delivery() -> None:
    """Queue full or database down: this worker's tabs still update, and the log says why."""
    bus = FakeBus()
    hub = RealtimeHub()
    bus.register(hub)
    watcher = hub.subscribe(topics=frozenset({"requests"}), id_visible=frozenset())

    bus.refuse = True
    hub.publish(topic="requests", action="created")

    assert len(_drain(watcher)) == 1


def test_no_bus_means_the_old_single_process_behaviour_exactly() -> None:
    """Dev and the test suite run without a bus; nothing about them may change."""
    hub = RealtimeHub()
    watcher = hub.subscribe(topics=frozenset({"clients"}), id_visible=frozenset({"clients"}))

    first = hub.publish(topic="clients", action="created", entity_id="c-1")
    second = hub.publish(topic="clients", action="updated", entity_id="c-1")

    assert first is not None and second is not None
    assert (first.revision, second.revision) == (1, 2)
    assert [event["action"] for event in _drain(watcher)] == ["created", "updated"]
    assert hub.shared is False


def test_a_duplicate_revision_is_ignored() -> None:
    """LISTEN delivers once per worker; a retry must not double-count a revision."""
    hub = RealtimeHub()
    change = Change(revision=7, topic="requests", action="created", at=datetime.now(UTC))
    hub.deliver(change)
    hub.deliver(Change(revision=7, topic="requests", action="created", at=datetime.now(UTC)))
    assert hub.revision == 7
    assert hub.topic_revisions()["requests"] == 7


def test_a_bus_publish_is_asynchronous_by_contract() -> None:
    """`publish()` with a bus returns None: the revision is assigned by the publisher, not guessed here."""
    hub = RealtimeHub()
    FakeBus().register(hub)
    assert hub.publish(topic="requests", action="created") is None


def test_replay_across_workers_uses_the_same_numbers() -> None:
    """`?since=` is only meaningful if every worker counts the same way — the reason for the shared counter."""
    bus = FakeBus()
    a, b = RealtimeHub(), RealtimeHub()
    bus.register(a)
    bus.register(b)

    for index in range(3):
        a.publish(topic="requests", action="updated", entity_id=f"r-{index}")

    # a tab that was on worker A reconnects to worker B with the last revision it applied
    replay, needs_resync = b.backlog(101, topics=frozenset({"requests"}), id_visible=frozenset({"requests"}))
    assert needs_resync is False
    assert [event["entity_id"] for event in replay] == ["r-1", "r-2"]


def test_only_topics_this_process_subscribed_to_are_delivered() -> None:
    bus = FakeBus()
    a, b = RealtimeHub(), RealtimeHub()
    bus.register(a)
    bus.register(b)
    watcher = b.subscribe(topics=frozenset({"policies"}), id_visible=frozenset())

    a.publish(topic="requests", action="created")
    a.publish(topic="policies", action="updated")

    assert [event["topic"] for event in _drain(watcher)] == ["policies"]


def test_the_hub_and_the_bus_can_be_detached_without_losing_the_subscribers() -> None:
    bus = FakeBus()
    hub = RealtimeHub()
    bus.register(hub)
    watcher = hub.subscribe(topics=frozenset({"requests"}), id_visible=frozenset())

    hub.detach_bus()
    hub.publish(topic="requests", action="created")

    assert len(_drain(watcher)) == 1
    assert hub.shared is False


async def test_postgres_bus_declines_cleanly_on_sqlite() -> None:
    """The bus is optional on purpose: without Postgres the app must boot and keep working."""
    from app.services.realtime_bus import PostgresBus

    from tests.conftest import engine  # noqa: F401  (imported for the side effect of the test database)

    class _SqliteEngine:
        class url:  # noqa: N801
            @staticmethod
            def get_dialect() -> Any:
                class _D:
                    name = "sqlite"

                return _D()

    bus = PostgresBus(engine=_SqliteEngine(), sessionmaker=None, hub=RealtimeHub())  # type: ignore[arg-type]
    assert await bus.start() is False
    assert bus.running is False
    await bus.stop()
    await asyncio.sleep(0)
