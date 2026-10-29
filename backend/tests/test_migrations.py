"""The migration chain is a deployment artifact, so it is tested like one (ADR-014).

Until now nothing in the suite ever ran `alembic upgrade head`: the tests build their schema with
`Base.metadata.create_all`, and `compose up` (the documented path) was the only consumer of the chain —
on a machine nobody had. Running it for real found three defects that no unit test could have found:

* 0001 created its tables in alphabetical order, so `clients` was built before `users` and PostgreSQL
  aborted with `relation "users" does not exist`;
* the `leads` ↔ `clients` foreign keys form a cycle, which no `CREATE TABLE` can express on PostgreSQL;
* `CREATE POLICY ... FOR UPDATE, DELETE` is not valid SQL — a policy names exactly one command — and both
  0002 and the first draft of 0003 used it, so 0002 aborted on any real server;
* a 34-character revision id does not fit `alembic_version.version_num` (`VARCHAR(32)`), so *applying*
  a migration failed after the migration itself had run.

These tests are the regression gate for that whole class: they run the chain on a scratch database, in
both directions, and then compare the result with the models the application actually uses.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from app.models import Base
from sqlalchemy.ext.asyncio import create_async_engine

BACKEND = Path(__file__).resolve().parents[1]
VERSIONS = BACKEND / "migrations" / "versions"
#: alembic creates `alembic_version.version_num` as VARCHAR(32) and never widens it
MAX_REVISION_LEN = 32


def _config() -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    # `set` (not the ini file) because env.py re-reads DATABASE_URL and overwrites this value
    # `script_location` and `prepend_sys_path` are absolute here so the chain runs no matter which
    # directory pytest was invoked from; the ini's own relative values would not survive that.
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("prepend_sys_path", str(BACKEND))
    cfg.set_main_option("path_separator", "os")
    return cfg


def _with_url(url: str, action: object, *args: str) -> None:
    """env.py takes its URL from the environment, so the environment is what a test has to set.

    Alembic's own env calls `asyncio.run()`, so these tests stay synchronous and never nest loops.
    """
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        action(_config(), *args)  # type: ignore[operator]
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def _upgrade(url: str, revision: str = "head") -> None:
    _with_url(url, command.upgrade, revision)


def _downgrade(url: str, revision: str = "base") -> None:
    _with_url(url, command.downgrade, revision)


def test_every_revision_id_fits_the_version_table() -> None:
    """A longer id than `VARCHAR(32)` aborts the *last* step of an upgrade, after the DDL already ran."""
    for path in sorted(VERSIONS.glob("*.py")):
        match = re.search(r'^revision: str = "(?P<rev>[^"]+)"', path.read_text(encoding="utf-8"), re.M)
        assert match is not None, f"{path.name}: no `revision: str = ...` line"
        assert len(match.group("rev")) <= MAX_REVISION_LEN, (
            f"{path.name}: revision id {match.group('rev')!r} is {len(match.group('rev'))} chars; "
            f"alembic_version.version_num holds {MAX_REVISION_LEN}"
        )


def test_revision_chain_is_linear_and_complete() -> None:
    """No dangling `down_revision`, no two revisions claiming the same parent (a merge needs a reason)."""
    graph: dict[str, str | None] = {}
    for path in sorted(VERSIONS.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        rev = re.search(r'^revision: str = "(?P<v>[^"]+)"', text, re.M)
        down = re.search(r'^down_revision: str \| None = (?P<v>"[^"]+"|None)', text, re.M)
        assert rev and down, f"{path.name}: revision headers must use the annotated form"
        parent = None if down.group("v") == "None" else down.group("v").strip('"')
        graph[rev.group("v")] = parent
    heads = [r for r in graph if r not in {p for p in graph.values() if p}]
    assert len(heads) == 1, f"expected exactly one head, found {sorted(heads)}"
    for child, parent in graph.items():
        assert parent is None or parent in graph, f"{child}: down_revision {parent!r} is not a known revision"


def test_chain_runs_both_directions_on_sqlite(tmp_path: Path) -> None:
    # env.py always builds an async engine, so the URL has to name an async driver
    url = f"sqlite+aiosqlite:///{tmp_path / 'chain.db'}"
    _upgrade(url)
    _downgrade(url)
    _upgrade(url)


def test_0010_takes_the_issue_date_from_the_audit_trail(tmp_path: Path) -> None:
    """R10.1 point 5: the backfill is the *issue* event, not `created_at`, and an unknown date stays unknown.

    The chain is stopped at 0009 (which backfilled `issued_at = created_at`), rows are inserted in the state
    a real deployment has — one policy issued through the screen and therefore audited, one issued without a
    record, one still a draft — and then the chain is brought to head. This is the only place the data
    migration is observable; `Base.metadata.create_all` never runs it.
    """
    from datetime import UTC, datetime

    from app.models.phase2 import Policy
    from app.models.requests import AuditLog

    db = tmp_path / "issue_date.db"
    _with_url(f"sqlite+aiosqlite:///{db}", command.upgrade, "0009_finance_ledger")

    issued_by_screen = uuid.uuid4()
    issued_without_trail = uuid.uuid4()
    still_a_draft = uuid.uuid4()
    created = datetime(2026, 9, 28, 10, 0, tzinfo=UTC)
    really_issued = datetime(2026, 10, 2, 9, 30, tzinfo=UTC)

    engine = sa.create_engine(f"sqlite:///{db}")
    try:
        with engine.begin() as conn:
            for policy_id, status in (
                (issued_by_screen, "issued"),
                (issued_without_trail, "issued"),
                (still_a_draft, "draft"),
            ):
                conn.execute(
                    sa.insert(Policy.__table__).values(
                        id=policy_id,
                        client_id=uuid.uuid4(),
                        policy_no=f"T-{policy_id.hex[:8]}",
                        payment_plan="quarterly",
                        currency="EGP",
                        status=status,
                        created_at=created,
                        updated_at=created,
                        issued_at=None,
                    )
                )
            conn.execute(
                sa.insert(AuditLog.__table__).values(
                    id=uuid.uuid4(),
                    action="policy.issued",
                    entity_type="policy",
                    entity_id=issued_by_screen,
                    occurred_at=really_issued,
                )
            )
    finally:
        engine.dispose()

    _with_url(f"sqlite+aiosqlite:///{db}", command.upgrade, "head")

    engine = sa.create_engine(f"sqlite:///{db}")
    try:
        with engine.connect() as conn:
            rows = dict(conn.execute(sa.text("SELECT id, issued_at FROM policies")).all())
    finally:
        engine.dispose()

    def as_datetime(value: object) -> datetime | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    keys = {key.hex if isinstance(key, bytes) else str(key).replace("-", ""): value for key, value in rows.items()}
    # 1. the audit trail wins over `created_at` — September's draft was issued in October
    assert as_datetime(keys[issued_by_screen.hex]) == really_issued
    # 2. no audit row ⇒ no known date, and *not* `created_at`
    assert as_datetime(keys[issued_without_trail.hex]) is None
    # 3. a draft is not touched at all
    assert as_datetime(keys[still_a_draft.hex]) is None

    _with_url(f"sqlite+aiosqlite:///{db}", command.downgrade, "0009_finance_ledger")
    engine = sa.create_engine(f"sqlite:///{db}")
    try:
        with engine.connect() as conn:
            restored = dict(conn.execute(sa.text("SELECT id, issued_at FROM policies")).all())
    finally:
        engine.dispose()
    restored_keys = {key.hex if isinstance(key, bytes) else str(key).replace("-", ""): value for key, value in restored.items()}
    # the downgrade puts 0009's own behaviour back, including for the row whose date was unknown
    assert as_datetime(restored_keys[issued_without_trail.hex]) == created


def test_migrated_schema_matches_the_models(tmp_path: Path) -> None:
    """What `compose up` builds must be what the code and the tests assume.

    Comparing names (tables, columns, indexes) is the honest subset: types differ per dialect and server
    defaults drift for reasons unrelated to a missing migration.
    """
    url = f"sqlite+aiosqlite:///{tmp_path / 'drift.db'}"
    _upgrade(url)

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'drift.db'}")
    try:
        metadata = sa.MetaData()
        metadata.reflect(bind=engine)
    finally:
        engine.dispose()

    expected_tables = set(Base.metadata.tables)
    actual_tables = set(metadata.tables) - {"alembic_version"}
    assert expected_tables == actual_tables, (
        f"schema drift — only in models: {sorted(expected_tables - actual_tables)}, "
        f"only in migrations: {sorted(actual_tables - expected_tables)}. "
        "A new column or table needs a migration, not just a model change."
    )
    for name, table in Base.metadata.tables.items():
        migrated = metadata.tables[name]
        # names, not Column objects: `Column.__eq__` builds a SQL expression, so a set of columns can
        # never be compared with `==`
        model_cols, db_cols = set(table.columns.keys()), set(migrated.columns.keys())
        assert model_cols == db_cols, (
            f"{name}: only in models {sorted(model_cols - db_cols)}, only in migrations {sorted(db_cols - model_cols)}"
        )
        model_indexes = {ix.name for ix in table.indexes if ix.name}
        db_indexes = {ix.name for ix in migrated.indexes if ix.name and not ix.name.startswith(("_", "sqlite_"))}
        assert model_indexes - db_indexes == set(), (
            f"{name}: indexes the models expect but the chain lacks: {sorted(model_indexes - db_indexes)}"
        )


@pytest.mark.postgres_only
def test_chain_runs_on_a_fresh_postgresql_database(tmp_path: Path) -> None:
    """The primary deployment path: `alembic upgrade head` on an empty PostgreSQL 16 database.

    Run as a subprocess on purpose — `migrations/env.py` calls `asyncio.run()` itself, and the scratch
    database must be invisible to the shared test schema that `conftest.py` resets with `create_all`.
    """
    test_url = sa.engine.make_url(os.environ["DATABASE_URL_TEST"])
    scratch = test_url.set(database="brokerhub_chain_check")
    admin = test_url.set(database="postgres")

    def _admin_sql(*statements: str) -> None:
        import asyncio

        async def run() -> None:
            engine = create_async_engine(admin, isolation_level="autocommit")
            try:
                async with engine.connect() as conn:
                    for stmt in statements:
                        await conn.execute(sa.text(stmt))
            finally:
                await engine.dispose()

        asyncio.run(run())

    _admin_sql("DROP DATABASE IF EXISTS brokerhub_chain_check WITH (FORCE)")
    try:
        _admin_sql("CREATE DATABASE brokerhub_chain_check")
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
            cwd=BACKEND,
            env={**os.environ, "DATABASE_URL": scratch.render_as_string(hide_password=False)},
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == 0, f"upgrade head failed:\n{result.stderr[-2000:]}"
        assert _policies_and_role(scratch) == {
            "policies": ["clients_scope", "leads_scope"],
            "forced": ["clients", "leads"],
            "app_role": "brokerhub_app",
        }
        downgrade = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", "downgrade", "base"],
            cwd=BACKEND,
            env={**os.environ, "DATABASE_URL": scratch.render_as_string(hide_password=False)},
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert downgrade.returncode == 0, f"downgrade base failed:\n{downgrade.stderr[-2000:]}"
    finally:
        _admin_sql("DROP DATABASE IF EXISTS brokerhub_chain_check WITH (FORCE)")


def _policies_and_role(url: sa.URL) -> dict[str, object]:
    import asyncio

    async def run() -> dict[str, object]:
        engine = create_async_engine(url, isolation_level="autocommit")
        try:
            async with engine.connect() as conn:
                policies = [r[0] for r in (await conn.execute(sa.text("select polname from pg_policy order by 1")))]
                forced = [
                    r[0]
                    for r in (
                        await conn.execute(
                            sa.text(
                                "select relname from pg_class c join pg_namespace n on n.oid=c.relnamespace "
                                "where n.nspname='public' and c.relrowsecurity and c.relforcerowsecurity order by 1"
                            )
                        )
                    )
                ]
                role = (
                    await conn.execute(
                        sa.text(
                            "select rolname from pg_roles where rolname='brokerhub_app' "
                            "and not rolbypassrls and not rolsuper and not rolcanlogin"
                        )
                    )
                ).scalar()
        finally:
            await engine.dispose()
        return {"policies": policies, "forced": forced, "app_role": role}

    return asyncio.run(run())
