"""The second net (spec §6): the request's Scope is also installed on the PostgreSQL session.

`app/repositories/scope_filters.py` is what *enforces* isolation today. The RLS policies of migrations
0002/0003 exist for the case that matters: a query that forgot the filter, a hand-written `SELECT`, or
anyone touching the database with the application's own role. They read three settings —
`app.bypass_rls`, `app.scope_user_id`, and (for a portal session) `app.scope_client_id`. Nothing used to
set them, so the policies were permissive by construction; that gap is what this module closes.

Why an `after_begin` hook instead of one `SET` per request:

* endpoints commit mid-request, and a session-level `SET` would either leak onto the pooled connection
  (the next request inherits `bypass_rls=on`, which is the opposite of a safety net) or need a reset that
  itself gets rolled back by the pool;
* `set_config(..., true)` is transaction-local, and the hook re-applies it for *every* transaction the
  request opens — so the settings are in force for the statement after a commit, and vanish at the
  boundary the pool cares about.

No-op off PostgreSQL: SQLite (unit runs, local dev) has no RLS, and raising here would only train people
to wrap calls in try/except.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

from sqlalchemy import Connection, event, text

from app.core.scope import Scope

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession


class Guc:
    """The session settings the RLS policies read.

    Names rather than literals repeated across files, so the hook, the DDL in `core/rls_ddl.py` (which is
    what migration 0003 executes) and the tests cannot drift apart.
    """

    BYPASS = "app.bypass_rls"
    SCOPE_USER = "app.scope_user_id"
    SCOPE_CLIENT = "app.scope_client_id"
    ALL: tuple[str, ...] = (BYPASS, SCOPE_USER, SCOPE_CLIENT)


_KEYS = Guc.ALL


def supported(session: AsyncSession) -> bool:
    bind = session.get_bind()
    return bind is not None and bind.dialect.name == "postgresql"


#: the scope of the request being served; set by the `scope_of` dependency and read at transaction begin
current_scope: ContextVar[Scope | None] = ContextVar("broker_hub_rls_scope", default=None)

_INSTALLED = "_broker_hub_rls_installed"


def set_scope(scope: Scope) -> None:
    """Called by the scope dependency — the hook below reads it whenever a transaction starts."""
    current_scope.set(scope)


def install(session: AsyncSession) -> None:
    """Attach the `after_begin` hook to this request's session (idempotent, no-op on SQLite)."""
    if not supported(session) or getattr(session, _INSTALLED, None) is not None:
        return
    sync_session = session.sync_session

    @event.listens_for(sync_session, "after_begin")
    def _on_begin(_session: object, _transaction: object, connection: Connection) -> None:
        scope = current_scope.get()
        if scope is None:
            return
        for key, value in scope.session_settings().items():
            connection.execute(text("SELECT set_config(:key, :value, true)"), {"key": key, "value": value})

    setattr(sync_session, _INSTALLED, True)


async def current_gucs(session: AsyncSession) -> dict[str, str]:
    """What the server currently sees — used by the RLS tests and by `scripts/check_rls.py`."""
    out: dict[str, str] = {}
    for key in _KEYS:
        value = (await session.execute(text("SELECT current_setting(:key, true)"), {"key": key})).scalar()
        out[key] = "" if value is None else str(value)
    return out


async def raw_count(session: AsyncSession, table: str) -> int:
    """Count through the server's own eyes, bypassing the repository filters (tests only)."""
    return int((await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one())
