"""`/realtime/*` — the server-sent change feed and its polling fallback (ADR-026).

Two routes, deliberately only two:

* `GET /realtime/stream` — `text/event-stream`. Authenticated with the same bearer token every other route
  uses, because the client reads it with `fetch()` instead of `EventSource` (EventSource cannot send an
  Authorization header, and putting a token in a query string is how tokens end up in access logs and
  `Referer` headers).
* `GET /realtime/revision` — the cheap signature a client polls when it cannot hold a stream open: one
  integer per topic, so a fallback tick costs nothing and still converges.

Both are scoped: the topics come from the caller's permissions and every id is stripped for a caller who
cannot already see it (`services/realtime.py`).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, cast

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from app.api.v1.deps import CurrentUser, ScopeDep
from app.core.db import get_sessionmaker
from app.core.deps import authenticate
from app.services import realtime

router = APIRouter(tags=["realtime"])

#: comment frames keep proxies and load balancers from closing an idle stream. 15 s is under the 60 s idle
#: timeout of every proxy in the deployment, and the client's own watchdog fires at 40 s.
HEARTBEAT_SECONDS = 15.0


def _frame(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


@router.get("/realtime/stream", summary="Server-sent change feed (topics + revision, no row data)")
async def stream(
    request: Request,
    since: int = Query(default=0, ge=0, description="last revision the client applied; 0 = only new changes"),
) -> StreamingResponse:
    """Authenticated *before* the stream opens, in a session that is closed before the first frame.

    The dependency form (`CurrentUser` + `ScopeDep`) kept a pooled connection checked out for the entire life
    of the stream — one per open tab — and starved the pool under parallel browser sessions (R7). The identity
    is resolved here instead, so a stream holds a subscription and a queue, never a connection.
    """
    async with get_sessionmaker()() as session:
        actor, scope = await authenticate(request, session)
    perms = frozenset(actor.permission_codes)
    topics = realtime.topics_for(perms=perms, is_portal=scope.is_portal)
    id_visible = realtime.id_visible_for(perms=perms, is_portal=scope.is_portal)
    subscription = realtime.hub.subscribe(topics=topics, id_visible=id_visible)

    async def frames() -> AsyncIterator[str]:
        try:
            # 3 s is the browser-side retry hint; the client implements its own backoff on top of it.
            yield "retry: 3000\n\n"
            yield _frame(
                "hello",
                {
                    "revision": realtime.hub.revision,
                    "topics": sorted(topics),
                    "ids": sorted(id_visible),
                    "heartbeat_seconds": HEARTBEAT_SECONDS,
                },
            )
            replay, needs_resync = realtime.hub.backlog(since, topics=topics, id_visible=id_visible)
            if needs_resync:
                yield _frame("resync", {"revision": realtime.hub.revision, "reason": "gap"})
            for event in replay:
                yield _frame("change", event)

            while True:
                if await request.is_disconnected():
                    break
                try:
                    # `wait_for` is typed against `Future[...]`; a coroutine is what the queue hands back
                    event = await asyncio.wait_for(cast("Any", subscription.queue.get()), timeout=HEARTBEAT_SECONDS)
                except TimeoutError:  # `asyncio.wait_for` raises the builtin since 3.11
                    yield ": ping\n\n"
                    continue
                if event is None:  # the hub was closed — shutting down
                    break
                if subscription.overflowed:
                    subscription.overflowed = False
                    yield _frame("resync", {"revision": realtime.hub.revision, "reason": "overflow"})
                yield _frame("change", event)
        finally:
            realtime.hub.unsubscribe(subscription)

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={
            "cache-control": "no-store",
            "x-accel-buffering": "no",  # nginx: do not buffer the stream into a single blob
            "connection": "keep-alive",
        },
    )


@router.get("/realtime/revision", summary="Change signature per topic — the polling fallback")
async def revision(actor: CurrentUser, scope: ScopeDep) -> dict[str, Any]:
    perms = frozenset(actor.permission_codes)
    topics = realtime.topics_for(perms=perms, is_portal=scope.is_portal)
    return {
        "revision": realtime.hub.revision,
        "topics": {topic: value for topic, value in realtime.hub.topic_revisions().items() if topic in topics},
        "subscribers": realtime.hub.subscriber_count(),
    }
