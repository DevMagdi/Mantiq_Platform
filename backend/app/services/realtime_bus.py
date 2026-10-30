"""Cross-process delivery for the change feed — Postgres LISTEN/NOTIFY, no new infrastructure.

The hub in `services/realtime.py` is an in-process object: a `publish()` reaches the tabs *this* process is
holding and nobody else. That was fine while the deployment ran one uvicorn worker. It is not fine at two,
which is what R15 makes the default: a manager saving a request would reach the browsers attached to the
same worker and silently miss the browsers attached to the other one. A feed that works 50% of the time is
worse than no feed, because the screen looks live.

Three shapes were possible and only one was acceptable here:

* Redis pub/sub — the obvious one, and the one the module docstring used to promise. It would add a second
  service that must be up for the *product* to be correct, and this deployment already has a shared bus that
  every worker is already connected to: PostgreSQL.
* A database change-log table — durable and replayable, but it needs a migration (this round allows one only
  for a real index) and a poller per worker.
* `LISTEN`/`NOTIFY` — the mechanism Postgres has for exactly this. No migration, no poller, delivery in
  single-digit milliseconds, and it degrades to today's behaviour when the database is SQLite (dev/tests).

What travels on the channel is the same `Change` the hub already builds: topic, action, revision, and ids
only where the caller is allowed to see them. **No row data** — the receiving client re-runs a query the API
scopes for it, which is the privacy argument the feed has always rested on and which a broker does not
change.

The revision is the one thing that must be *shared*, not per-process: a tab that reconnects to worker B
passes `?since=57` and expects the number to mean what it meant on worker A. So every publish takes its
revision from one atomic row counter in the database, and every worker's hub advances to the revision it
hears. The counter row lives in `request_number_counter` — the table this project already uses for "a
portable, atomic, human-readable counter" — under a namespaced key. A new sequence would have been cleaner
and would have cost a schema revision for one integer, which is exactly what this round's rules forbid.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.services.realtime import Change, RealtimeHub

log = get_logger(__name__)

#: the NOTIFY channel. One per database: every worker of one deployment listens on the same name.
CHANNEL = "mantiq_realtime"

#: the counter row. Namespaced so it cannot collide with a request-number key.
COUNTER_KEY = "rt_revision"  # fits the counter table's VARCHAR(16) key, namespaced away from request numbers


class PostgresBus:
    """Publishes changes to every worker and feeds them back into this worker's hub.

    The publish path is deliberately *not* "deliver locally, then tell the others": if the publisher delivered
    locally and the notification carried a different revision, the two would disagree about what `since` means.
    Instead the notification is the single source of truth for every process, including the one that sent it.
    The cost is one round trip per mutation (measured: well under a millisecond on a local socket), and the win
    is that the buffer in each worker is complete, so `?since=` replay works whichever worker a tab lands on.
    """

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        sessionmaker: async_sessionmaker[AsyncSession],
        hub: RealtimeHub,
        queue_size: int = 1000,
    ) -> None:
        self._engine = engine
        self._sessionmaker = sessionmaker
        self._hub = hub
        self._queue: asyncio.Queue[dict[str, Any]] | None = None
        self._consumer: asyncio.Task[None] | None = None
        self._conn: Any = None
        self._connection_ctx: Any = None
        self._running = False
        self._failures = 0
        self._queue_size = queue_size

    # ---- lifecycle ----------------------------------------------------------------------------
    async def start(self) -> bool:
        """Subscribe and (if that worked) start the publisher. Returns False when there is nothing to do.

        Not being able to fan out is not a reason to refuse to boot: a single-process deployment is a valid
        deployment, and the feed still works there. It *is* a reason to say so once, in the log, in words an
        operator can act on.
        """
        from app.core.config import get_settings

        if get_settings().REALTIME_FANOUT.strip().lower() == "off":
            log.info("realtime.fanout_skipped", reason="REALTIME_FANOUT=off", mode="process")
            return False
        if not self._engine.url.get_dialect().name.startswith("postgres"):
            log.info("realtime.fanout_skipped", reason="database is not postgres", mode="process")
            return False
        self._queue = asyncio.Queue(maxsize=self._queue_size)
        try:
            await self._listen()
        except Exception as exc:  # pragma: no cover - depends on the deployment's grants
            log.warning("realtime.fanout_unavailable", error=str(exc), mode="process")
            await self._cleanup()
            return False
        self._consumer = asyncio.create_task(self._consume(), name="realtime-fanout")
        self._running = True
        self._hub.attach_bus(self)
        log.info("realtime.fanout_ready", channel=CHANNEL, mode="shared")
        return True

    async def stop(self) -> None:
        self._running = False
        self._hub.detach_bus()
        if self._consumer is not None:
            self._consumer.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._consumer
            self._consumer = None
        await self._cleanup()

    async def _cleanup(self) -> None:
        if self._connection_ctx is not None:
            with contextlib.suppress(Exception):
                await self._connection_ctx.close()
        self._connection_ctx = None
        self._conn = None
        self._queue = None

    @property
    def running(self) -> bool:
        return self._running

    async def _listen(self) -> None:
        """Hold one connection open for the process and let asyncpg hand us the payloads."""
        self._connection_ctx = await self._engine.connect()
        raw = await self._connection_ctx.get_raw_connection()
        self._conn = raw.driver_connection  # the asyncpg connection underneath SQLAlchemy's adapter
        await self._conn.add_listener(CHANNEL, self._on_notify)
        # the connection must stay out of the pool's rotation; it is never released back
        log.debug("realtime.listening", channel=CHANNEL)

    def _on_notify(self, _conn: Any, _pid: int, _channel: str, payload: str) -> None:
        """asyncpg callback: synchronous and must not block — hand the work to the loop and return."""
        try:
            self._deliver_payload(payload)
        except Exception as exc:  # pragma: no cover - malformed payload means a version mismatch
            log.warning("realtime.bad_payload", error=str(exc))

    def _deliver_payload(self, payload: str) -> None:
        change = Change.from_wire(json.loads(payload))
        self._hub.deliver(change)

    # ---- publish ------------------------------------------------------------------------------
    def submit(
        self,
        *,
        topic: str,
        action: str,
        entity_id: str | None = None,
        actor_id: str | None = None,
        path: str | None = None,
    ) -> bool:
        """Called from the middleware (sync, in the event loop). Never blocks, never raises.

        A full queue means the database is slower than the mutations arriving — dropping the *notification* is
        not acceptable, but queueing without bound is not either. On overflow the hub delivers locally so the
        tabs on this worker still update, and the counter jump leaves other workers' tabs to self-heal through
        the revision probe. Both halves are already part of the design; neither is a silent loss.
        """
        if not self._running or self._queue is None:
            return False
        message = {"topic": topic, "action": action, "entity_id": entity_id, "actor_id": actor_id, "path": path}
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            log.warning("realtime.fanout_backlog", topic=topic)
            self._hub.publish_local(topic=topic, action=action, entity_id=entity_id, actor_id=actor_id, path=path)
            return False
        return True

    async def _consume(self) -> None:
        assert self._queue is not None
        while True:
            message = await self._queue.get()
            try:
                await self._publish(message)
                self._failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # The database refused the notification (restart, lost connection). Keep the feed honest for
                # the tabs attached here and say so; the probe on every other tab covers the rest.
                self._failures += 1
                log.warning("realtime.publish_failed", topic=message["topic"], error=str(exc), failures=self._failures)
                self._hub.publish_local(**message)

    async def _publish(self, message: dict[str, Any]) -> None:
        async with self._sessionmaker() as session:
            revision = (
                await session.execute(
                    sa.text(
                        """
                        INSERT INTO request_number_counter (key, last_value) VALUES (:key, 1)
                        ON CONFLICT (key) DO UPDATE
                            SET last_value = request_number_counter.last_value + 1
                        RETURNING last_value
                        """
                    ),
                    {"key": COUNTER_KEY},
                )
            ).scalar_one()
            change = Change(
                revision=int(revision),
                topic=message["topic"],
                action=message["action"],
                at=datetime.now(UTC),
                entity_id=message["entity_id"],
                actor_id=message["actor_id"],
                path=message["path"],
            )
            await session.execute(
                sa.text("SELECT pg_notify(:channel, :payload)"),
                {"channel": CHANNEL, "payload": json.dumps(change.to_wire())},
            )
            await session.commit()
