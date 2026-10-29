"""Server-sent change feed: one in-process hub, one revision counter, per-subscriber filtering (ADR-026).

Why a hub and not a table: the requirement is "≤ 2 seconds, no refresh", and the cheapest honest way to
get there is to *tell* the browser something changed and let it re-run the query it already trusts. The
payload therefore carries **no row data** — only a topic, an action, a revision and (for callers allowed to
see it) an id. A client that receives `requests` simply invalidates `/requests`; the API's own scoping
decides what that client is allowed to see. That ordering is the whole privacy argument: pushing data would
mean re-implementing `Scope` inside the feed, and any drift there is a leak.

Single process, stated out loud: the deployment runs one uvicorn worker (`infra/docker-compose.yml`) and the
Celery workers never publish. If the app is ever scaled horizontally this module needs a Redis pub/sub fan-out
behind the same `publish()` signature — the revision counter and the replay buffer are already shaped for it.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.domain import permissions as P

#: how many recent changes a reconnecting client can replay. A browser that was away longer gets `resync`
#: and refetches everything it has on screen, which is cheap and always correct.
BUFFER_SIZE = 500

#: topics a subscriber can ask for, and the permission that makes the topic relevant to them.
TOPIC_PERMS: dict[str, tuple[str, ...]] = {
    "requests": (P.REQUESTS_VIEW,),
    "leads": (P.LEADS_VIEW_OWN, P.LEADS_VIEW_ALL),
    "clients": (P.CLIENTS_VIEW_OWN, P.CLIENTS_VIEW_ALL),
    "policies": (P.POLICIES_VIEW, P.POLICIES_MANAGE),
    "settings": (P.SETTINGS_MANAGE,),
}

#: the topic is delivered *with* ids only to a caller who can already see any row in it. A rep holds
#: `leads.view_own`, so the leads topic reaches them — but stripped, because "lead 7f3… changed" is already
#: a fact about somebody else's book. For requests, `requests.view` is office-wide by design (CS/manager).
ID_VISIBLE_PERMS: dict[str, tuple[str, ...]] = {
    "requests": (P.REQUESTS_VIEW,),
    "leads": (P.LEADS_VIEW_ALL,),
    "clients": (P.CLIENTS_VIEW_ALL,),
    "policies": (P.POLICIES_VIEW, P.POLICIES_MANAGE),
    "settings": (P.SETTINGS_MANAGE,),
}

#: portal users hold no staff permissions, and their link pins them to one client. They get the two topics
#: their screens are built on, always stripped — their own scoped refetch is what fills the screen.
PORTAL_TOPICS: frozenset[str] = frozenset({"requests", "policies"})

#: path prefix (after `/api/v1/`) → topic. Longest prefix wins, so `/portal/policies` stays a policy change.
_TOPIC_BY_PREFIX: tuple[tuple[str, str], ...] = (
    ("portal/policies", "policies"),
    ("portal/requests", "requests"),
    ("portal", "requests"),
    ("requests", "requests"),
    ("webhooks", "requests"),
    ("ai", "requests"),
    ("dashboard", "requests"),
    ("notifications", "requests"),
    ("exports", "requests"),
    ("leads", "leads"),
    ("sales", "leads"),
    ("team", "leads"),
    ("clients", "clients"),
    ("policies", "policies"),
    ("issuing-cases", "policies"),
    ("commissions", "policies"),
    ("reports", "policies"),
    ("masters", "clients"),
    ("settings", "settings"),
)

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

#: methods that change something. `GET`/`HEAD`/`OPTIONS` never publish: a feed that fires on reads is noise,
#: and two clients polling each other's reads is an infinite refetch loop.
_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def topic_for(method: str, path: str) -> str | None:
    """Which topic a mutating request belongs to, or `None` for "not interesting" (auth, realtime, exports)."""
    if method.upper() not in _MUTATING:
        return None
    if not path.startswith("/api/v1/"):
        return None
    tail = path[len("/api/v1/") :]
    head = tail.split("/", 1)[0]
    if head in {"auth", "realtime", "healthz", "health", "metrics", "openapi.json"}:
        return None
    for prefix, topic in sorted(_TOPIC_BY_PREFIX, key=lambda item: len(item[0]), reverse=True):
        if tail == prefix or tail.startswith(f"{prefix}/"):
            return topic
    return None


def entity_from_path(path: str) -> str | None:
    """The last UUID in a path, if any — enough for a client to highlight the row it just changed."""
    found = _UUID.findall(path)
    return found[-1] if found else None


def action_for(method: str) -> str:
    return {"POST": "created", "PUT": "updated", "PATCH": "updated", "DELETE": "deleted"}.get(method.upper(), "changed")


@dataclass(frozen=True)
class Change:
    revision: int
    topic: str
    action: str
    at: datetime
    entity_id: str | None = None
    actor_id: str | None = None
    path: str | None = None

    def as_event(self, *, with_ids: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "revision": self.revision,
            "topic": self.topic,
            "action": self.action,
            "at": self.at.isoformat(),
        }
        if with_ids:
            if self.entity_id:
                payload["entity_id"] = self.entity_id
            if self.actor_id:
                payload["actor_id"] = self.actor_id
            if self.path:
                payload["path"] = self.path
        return payload


@dataclass(eq=False)  # identity, not value: two tabs with the same topics are two subscribers
class Subscription:
    """One connected browser tab. `queue` is bounded: a tab that stops reading is dropped, not buffered."""

    topics: frozenset[str]
    id_visible: frozenset[str]
    queue: asyncio.Queue[dict[str, Any] | None] = field(default_factory=lambda: asyncio.Queue(maxsize=256))
    #: set when events were lost for this subscriber — the next frame is a `resync`, never a silent gap
    overflowed: bool = False

    def wants(self, topic: str) -> bool:
        return topic in self.topics


class RealtimeHub:
    def __init__(self, *, buffer_size: int = BUFFER_SIZE) -> None:
        self._buffer: deque[Change] = deque(maxlen=buffer_size)
        self._subscribers: set[Subscription] = set()
        self._revision = 0
        self._topic_revision: dict[str, int] = {}

    # ---- subscribe ---------------------------------------------------------------------------
    def subscribe(self, *, topics: frozenset[str], id_visible: frozenset[str]) -> Subscription:
        subscription = Subscription(topics=topics, id_visible=id_visible)
        self._subscribers.add(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        self._subscribers.discard(subscription)

    def close(self, subscription: Subscription) -> None:
        """Wake the reader so the HTTP handler can return (client gone or server shutting down)."""
        self.unsubscribe(subscription)
        with contextlib.suppress(asyncio.QueueFull):  # the reader is gone already
            subscription.queue.put_nowait(None)

    # ---- publish -----------------------------------------------------------------------------
    def publish(
        self,
        *,
        topic: str,
        action: str,
        entity_id: str | None = None,
        actor_id: str | None = None,
        path: str | None = None,
    ) -> Change:
        """Record a change and hand it to every interested subscriber. Never raises, never blocks."""
        self._revision += 1
        self._topic_revision[topic] = self._revision
        change = Change(
            revision=self._revision,
            topic=topic,
            action=action,
            at=datetime.now(UTC),
            entity_id=entity_id,
            actor_id=actor_id,
            path=path,
        )
        self._buffer.append(change)
        for subscription in list(self._subscribers):
            if not subscription.wants(topic):
                continue
            with_ids = topic in subscription.id_visible
            try:
                subscription.queue.put_nowait(change.as_event(with_ids=with_ids))
            except asyncio.QueueFull:
                # A tab that has not read 256 changes is asleep or broken. Dropping the backlog and marking
                # the subscriber means it will be told to refetch — the same outcome as replaying, without
                # an unbounded server-side buffer.
                subscription.overflowed = True
        return change

    # ---- read --------------------------------------------------------------------------------
    @property
    def revision(self) -> int:
        return self._revision

    def topic_revisions(self) -> dict[str, int]:
        """The fallback channel: one small GET tells a client which topics moved while it was not listening."""
        return dict(self._topic_revision)

    def backlog(self, since: int, *, topics: frozenset[str], id_visible: frozenset[str]) -> tuple[list[dict[str, Any]], bool]:
        """(events to replay, needs_resync). `since` older than the buffer means some changes are gone.

        `since=0` means "I am new, tell me only what happens next" — the documented contract of the query
        parameter — and *not* "replay everything you remember". The first version replayed the whole buffer to
        a brand-new tab, so every reconnect fired hundreds of invalidations at once and the screen it was
        supposed to keep fresh spent the next seconds refetching history (measured in R6: a change made right
        after a page load did not appear within the 2 s budget it exists to meet).
        """
        if since <= 0 or since >= self._revision:
            return [], False
        oldest = self._buffer[0].revision if self._buffer else self._revision + 1
        if self._buffer and since < oldest - 1:
            return [], True
        replay = [
            change.as_event(with_ids=change.topic in id_visible)
            for change in self._buffer
            if change.revision > since and change.topic in topics
        ]
        return replay, False

    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def reset(self) -> None:
        """Test hook: forget revisions and subscribers. Never called by the application."""
        self._buffer.clear()
        self._topic_revision.clear()
        self._subscribers.clear()
        self._revision = 0


#: module-level singleton — the app is one process, and `install_publisher` reads it from the middleware
hub = RealtimeHub()


def topics_for(*, perms: frozenset[str] | set[str], is_portal: bool) -> frozenset[str]:
    """Which topics this caller may be told about at all (before any id filtering)."""
    if is_portal:
        return PORTAL_TOPICS
    allowed = {topic for topic, required in TOPIC_PERMS.items() if any(perm in perms for perm in required)}
    return frozenset(allowed)


def id_visible_for(*, perms: frozenset[str] | set[str], is_portal: bool) -> frozenset[str]:
    if is_portal:
        return frozenset()  # stripped, always: the portal's ids are its own business
    return frozenset(topic for topic, required in ID_VISIBLE_PERMS.items() if any(perm in perms for perm in required))


def _as_id(value: Any) -> str | None:
    if isinstance(value, UUID):
        return str(value)
    return value if isinstance(value, str) and value else None
