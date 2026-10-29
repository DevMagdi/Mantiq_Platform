"""PostgreSQL-only: the row-security net actually nets (spec §6, ADR-003, ADR-014).

Two claims are proven here, because they fail independently:

1. **the policies filter** — asserted as `brokerhub_app`, because a superuser skips row security even on a
   `FORCE`'d table. Installing the net was therefore only half the job; the deployment role matters, and
   migration 0003 is what creates it.
2. **the application publishes the scope** — `core/rls.py` installs `app.bypass_rls` / `app.scope_user_id`
   / `app.scope_client_id` per transaction, so a request-level `SET` is never needed and nothing leaks onto
   a pooled connection behind it.

The DDL comes from `app/core/rls_ddl.py`, the module migration 0003 executes; the test database is built
with `create_all`, so the statements are applied here rather than by running Alembic.
`test_migrations.py` covers the other half: that the chain itself installs them.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from uuid import UUID

import pytest
import pytest_asyncio
import sqlalchemy as sa
from app.core import rls, rls_ddl
from app.core.db import get_sessionmaker
from app.core.scope import Scope
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

pytestmark = pytest.mark.postgres_only


def pg_url() -> str:
    """Read lazily: on a SQLite run this module is imported, then skipped by the marker."""
    url = os.environ.get("DATABASE_URL_TEST") or ""
    assert url.startswith("postgresql"), "these tests only make sense against PostgreSQL"
    return url


@pytest_asyncio.fixture
async def net(engine) -> AsyncIterator[AsyncEngine]:
    """The policies and the unprivileged role of a migrated production database, on the test schema."""
    eng = create_async_engine(pg_url(), future=True)
    database = str(sa.make_url(pg_url()).database)
    async with eng.begin() as conn:
        for statement in rls_ddl.statements(app_role_grants=True, database=database):
            await conn.exec_driver_sql(statement)
    yield eng
    # `engine` drops the whole schema after every test, so the tables and policies are self-cleaning;
    # only the role outlives a single test and it is re-used, not recreated, by the statements above
    await eng.dispose()


@asynccontextmanager
async def as_role(
    eng: AsyncEngine,
    settings: Mapping[str, str] | None = None,
    *,
    commit: bool = False,
) -> AsyncIterator[AsyncConnection]:
    """One transaction, played as the unprivileged role.

    `SET LOCAL ROLE` and `set_config(..., true)` are transaction-scoped on purpose, and the transaction is
    rolled back unless `commit` says otherwise: the connection returns to the pool with neither the role nor
    the GUCs attached, and the seeded fixture data stays exactly as it was.
    """
    async with eng.connect() as conn:
        transaction = await conn.begin()
        try:
            await conn.exec_driver_sql(f"SET LOCAL ROLE {rls_ddl.APP_ROLE}")
            for key, value in (settings or {}).items():
                await conn.execute(sa.text("SELECT set_config(:key, :value, true)"), {"key": key, "value": value})
            yield conn
        finally:
            if commit:
                await transaction.commit()
            else:
                await transaction.rollback()


# both helpers take their table name from `rls_ddl._GUARDED`, never from user input
async def count_rows(conn: AsyncConnection, table: str) -> int:
    result = await conn.exec_driver_sql(f"SELECT count(*) FROM {table}")
    return int(result.scalar_one())


async def names_in(conn: AsyncConnection, table: str) -> list[str]:
    result = await conn.exec_driver_sql(f"SELECT name FROM {table} ORDER BY name")
    return [row[0] for row in result.fetchall()]


def owner(user_id: str) -> dict[str, str]:
    return Scope(mode="owner", user_id=UUID(user_id)).session_settings()


# --- 1) the policies -----------------------------------------------------------------------------


async def test_an_unscoped_connection_is_permissive_by_design(net: AsyncEngine) -> None:
    """No GUC means system access (migrations, seeding, health checks) and sees the whole table.

    That is a property of the predicate, not an oversight: were it strict by default, `alembic upgrade
    head` and `scripts/seed_demo.py` would be the first victims of the net.
    """
    async with as_role(net) as conn:
        assert await count_rows(conn, "clients") == 2
        assert await count_rows(conn, "leads") == 2


async def test_an_owner_sees_exactly_its_own_rows(net: AsyncEngine, ids: dict) -> None:
    async with as_role(net, owner(ids["users"]["rep_a"])) as conn:
        assert await names_in(conn, "clients") == ["شركة ألف"]
        assert await names_in(conn, "leads") == ["ليد ألف"]


async def test_a_row_the_scope_excludes_is_absent_not_forbidden(net: AsyncEngine, ids: dict) -> None:
    """`rep_b`'s client exists, is readable by nobody else in its office, and simply is not there.

    Deliberately *not* an error: the repository layer answers 404 for foreign ids for the same reason
    (`app/core/scope.py`), so an unfiltered query degrades to an empty result instead of leaking a row.
    """
    async with as_role(net, owner(ids["users"]["rep_b"])) as conn:
        assert await names_in(conn, "clients") == ["شركة باء"]
        assert await count_rows(conn, "leads") == 1
    async with as_role(net, owner(ids["users"]["rep_a"])) as conn:
        foreign = await conn.exec_driver_sql("SELECT count(*) FROM clients WHERE name = 'شركة باء'")
        assert int(foreign.scalar_one()) == 0, "the other rep's company leaked through the net"


async def test_a_user_that_owns_nothing_sees_nothing_where_bypass_sees_everything(net: AsyncEngine, ids: dict) -> None:
    """A `SELECT` with no filter, run as an owner scope with no rows: zero — not the whole table."""
    async with as_role(net, owner(ids["users"]["cs"])) as conn:
        assert await count_rows(conn, "clients") == 0
        assert await count_rows(conn, "leads") == 0

    manager = Scope(mode="all", user_id=UUID(ids["users"]["manager"])).session_settings()
    assert manager[rls.Guc.BYPASS] == "on", "a manager scope must publish the bypass, or RLS hides its office"
    async with as_role(net, manager) as conn:
        assert await count_rows(conn, "clients") == 2


async def test_a_portal_session_is_pinned_to_its_own_company(net: AsyncEngine, ids: dict) -> None:
    scope = Scope(
        mode="portal_client",
        user_id=UUID(ids["users"]["owner"]),
        portal_client_id=UUID(ids["clients"]["ألف"]),
    ).session_settings()
    async with as_role(net, scope) as conn:
        assert await names_in(conn, "clients") == ["شركة ألف"]
        # `leads` carries no portal clause on purpose: the portal never reads the pipeline, so it must not
        # learn from it either — not even the lead its own company was converted from.
        assert await count_rows(conn, "leads") == 0


async def test_writes_are_checked_and_not_only_filtered(net: AsyncEngine, ids: dict) -> None:
    """A policy that only filters reads would let one rep create rows in another rep's name.

    The `FOR ALL` policy repeats the predicate in `WITH CHECK`. 0002 had SELECT/UPDATE/DELETE only, and
    PostgreSQL then rejects *every* INSERT for a `FORCE`'d role (`new row violates row-level security
    policy`) — which is why the net could not simply be switched on, and why 0003 exists.
    """
    rep_a, rep_b = UUID(ids["users"]["rep_a"]), UUID(ids["users"]["rep_b"])
    insert = (
        "INSERT INTO leads (id, name, type, status, assigned_to, created_at, updated_at) "
        "VALUES (gen_random_uuid(), 'ليد تحت الشبكة', 'company', 'new', :owner, now(), now())"
    )
    async with as_role(net, Scope(mode="owner", user_id=rep_a).session_settings()) as conn:
        before = await count_rows(conn, "leads")
        await conn.execute(sa.text(insert), {"owner": str(rep_a)})
        assert await count_rows(conn, "leads") == before + 1

    with pytest.raises(ProgrammingError, match="row-level security"):
        async with as_role(net, owner(str(rep_a))) as conn:
            await conn.execute(sa.text(insert), {"owner": str(rep_b)})


# --- 2) the request wiring -----------------------------------------------------------------------


async def test_the_hook_reapplies_the_scope_after_a_mid_request_commit(net: AsyncEngine, ids: dict) -> None:
    """Endpoints commit mid-request; a transaction-local `SET` would be gone by the next statement.

    `rls.install` re-runs `set_config` on every `after_begin`, so the scope is in force again for the
    transaction the commit opens — and then stops at the pool boundary, which is checked next.
    """
    session = get_sessionmaker()()
    rls.set_scope(Scope(mode="owner", user_id=UUID(ids["users"]["rep_a"])))
    rls.install(session)
    try:
        first = await rls.current_gucs(session)
        assert first[rls.Guc.SCOPE_USER] == str(ids["users"]["rep_a"])
        assert first[rls.Guc.BYPASS] == "off"

        await session.commit()  # the transaction that carried the settings is over

        after = await rls.current_gucs(session)
        assert after[rls.Guc.SCOPE_USER] == str(ids["users"]["rep_a"]), "the scope was lost across the commit"
    finally:
        await session.close()
        rls.set_scope(Scope())

    async with net.connect() as conn:
        leaked = {key: (await conn.execute(sa.text("SELECT current_setting(:key, true)"), {"key": key})).scalar() for key in rls.Guc.ALL}
    assert not any(v for v in leaked.values()), f"a scope survived its connection: {leaked}"


async def test_the_scope_of_one_request_does_not_become_the_scope_of_the_next(net: AsyncEngine, ids: dict) -> None:
    """Two sessions on the same pool, one owner then none: the second must not inherit the first."""
    first = get_sessionmaker()()
    rls.set_scope(Scope(mode="owner", user_id=UUID(ids["users"]["rep_a"])))
    rls.install(first)
    assert (await rls.current_gucs(first))[rls.Guc.SCOPE_USER] == str(ids["users"]["rep_a"])
    await first.close()

    second = get_sessionmaker()()
    rls.set_scope(Scope())  # a system/health path: no scope published
    rls.install(second)
    try:
        gucs = await rls.current_gucs(second)
    finally:
        await second.close()
    # an unsolicited scope publishes "no user" and keeps the bypass off — never the previous rep's id
    assert gucs[rls.Guc.SCOPE_USER] != str(ids["users"]["rep_a"]), f"the previous request's scope is still set: {gucs}"
    assert gucs[rls.Guc.SCOPE_USER] == "" and gucs[rls.Guc.BYPASS] == "off", gucs
