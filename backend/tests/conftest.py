"""The suite runs on whichever database `DATABASE_URL_TEST` names.

Empty (the local default) ⇒ a throwaway SQLite file, which is what makes `pytest` zero-setup.
Set to PostgreSQL — as CI does — and the same tests run on the real engine, which is the only way
`Base.metadata` types, Numeric round-trips and the partial indexes stay honest (ADR-003).
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

TEST_DB = Path(__file__).resolve().parents[1] / "test.db"
# `DATABASE_URL_TEST` is the single switch: postgres in CI, SQLite on a laptop (ADR-003).
TEST_URL = os.environ.get("DATABASE_URL_TEST") or ""
IS_POSTGRES = TEST_URL.startswith("postgresql")
os.environ["DATABASE_URL"] = TEST_URL or f"sqlite+aiosqlite:///{TEST_DB}"
os.environ["ENV"] = "dev"
os.environ["INTEGRATIONS_DRIVER"] = "sim"
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "1"
os.environ["STORAGE_DIR"] = str(Path(tempfile.mkdtemp(prefix="bh_test_media_")))

from app.core.db import get_sessionmaker, set_engine, set_sessionmaker  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.domain import permissions as P  # noqa: E402
from app.domain.enums import RequestPriority  # noqa: E402
from app.models.auth import ClientPortalUser, Permission, Role, User  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.models.clients import Client  # noqa: E402
from app.models.leads import Lead  # noqa: E402
from app.models.masters import BusinessHoursRow, InsuranceType, Insurer, LostReason, SlaPolicy  # noqa: E402
from app.models.requests import RequestNumberCounter  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

PASSWORD = "Test-Pass-2026"


@pytest_asyncio.fixture
async def engine():
    if IS_POSTGRES:
        # the schema is rebuilt from `Base.metadata` on every run; RLS policies are a migration-only
        # concern and are exercised against a migrated database in tests/test_rls_postgres.py
        eng = create_async_engine(TEST_URL, future=True)
        async with eng.begin() as conn:
            # `drop_all` cannot order `leads`↔`clients` (they reference each other, and PostgreSQL
            # enforces the cycle that SQLite ignores) — so the schema is reset instead.
            await conn.exec_driver_sql("DROP SCHEMA public CASCADE")
            await conn.exec_driver_sql("CREATE SCHEMA public")
            await conn.run_sync(Base.metadata.create_all)
    else:
        if TEST_DB.exists():
            TEST_DB.unlink()
        eng = create_async_engine(f"sqlite+aiosqlite:///{TEST_DB}", future=True)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    from app.services import auth as auth_service

    auth_service.reset_rate_limits()
    maker = async_sessionmaker(eng, expire_on_commit=False, autoflush=False)
    set_engine(eng)
    set_sessionmaker(maker)
    async with maker() as session:
        await bootstrap(session)
    yield eng
    await eng.dispose()
    set_sessionmaker(None)
    set_engine(None)
    if not IS_POSTGRES and TEST_DB.exists():
        TEST_DB.unlink()


@pytest_asyncio.fixture
async def session(engine) -> AsyncIterator[AsyncSession]:
    maker = get_sessionmaker()
    async with maker() as session:
        yield session


@pytest_asyncio.fixture
async def client(engine) -> AsyncIterator[AsyncClient]:
    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def bootstrap(session: AsyncSession) -> None:
    perms = {code: Permission(code=code, group=code.split(".")[0]) for code in P.ALL_PERMISSIONS}
    session.add_all(perms.values())
    roles: dict[str, Role] = {}
    for code, name in P.DEFAULT_ROLES.items():
        role = Role(code=code, name=name, is_system=True)
        role.permissions = [perms[c] for c in P.ROLE_PERMISSIONS[code]]
        session.add(role)
        roles[code] = role
    await session.flush()  # roles must have PKs before users reference them

    users: dict[str, User] = {}
    for key, role_code in (
        ("admin", "system_admin"),
        ("manager", "sales_manager"),
        ("rep_a", "sales_rep"),
        ("rep_b", "sales_rep"),
        ("cs", "customer_service"),
        ("owner", "client_owner"),
    ):
        user = User(
            email=f"{key}@brokerhub.test",
            password_hash=hash_password(PASSWORD),
            full_name={"admin": "Admin", "manager": "Manager", "rep_a": "Rep A", "rep_b": "Rep B", "cs": "CS Agent", "owner": "Owner"}[key],
            role_id=roles[role_code].id,
        )
        session.add(user)
        users[key] = user
    await session.flush()

    for priority, (first, resolution) in {
        RequestPriority.URGENT: (30, 120),
        RequestPriority.HIGH: (60, 240),
        RequestPriority.NORMAL: (240, 1440),
        RequestPriority.LOW: (600, 2880),
    }.items():
        session.add(SlaPolicy(priority=priority, first_response_minutes=first, resolution_minutes=resolution))
    for weekday in range(7):
        session.add(
            BusinessHoursRow(
                weekday=weekday,
                open_time=__import__("datetime").time(9, 0),
                close_time=__import__("datetime").time(18, 0),
                is_open=weekday not in {4, 5},
            )
        )
    session.add(Insurer(code="axa", name="أكسا", is_active=True))
    session.add(Insurer(code="zalako", name="وثرة", is_active=True))
    medical = InsuranceType(code="medical", name="طبي")
    session.add(medical)
    session.add(LostReason(name="سعر أعلى", sort_order=0))
    session.add(RequestNumberCounter(key="requests", last_value=1000))

    client_a = Client(name="شركة ألف", account_owner_id=users["rep_a"].id, type="company", status="active")
    client_b = Client(name="شركة باء", account_owner_id=users["rep_b"].id, type="company", status="active")
    session.add_all([client_a, client_b])
    await session.flush()
    session.add(ClientPortalUser(user_id=users["owner"].id, client_id=client_a.id))
    lead_a = Lead(name="ليد ألف", type="company", status="new", assigned_to=users["rep_a"].id, mobile="+201000000001")
    lead_b = Lead(name="ليد باء", type="company", status="new", assigned_to=users["rep_b"].id, mobile="+201000000002")
    session.add_all([lead_a, lead_b])
    await session.commit()


@pytest_asyncio.fixture
async def ids(client) -> dict[str, dict[str, str]]:
    """Stable ids for the fixture graph. `client` is requested so bootstrap has already run."""
    from sqlalchemy import select

    maker = get_sessionmaker()
    async with maker() as session:
        users = {
            key: str((await session.execute(select(User).where(User.email == f"{key}@brokerhub.test"))).scalar_one().id)
            for key in ("admin", "manager", "rep_a", "rep_b", "cs", "owner")
        }
        leads = {
            name: str((await session.execute(select(Lead).where(Lead.name == f"ليد {name}"))).scalar_one().id) for name in ("ألف", "باء")
        }
        clients = {
            name: str((await session.execute(select(Client).where(Client.name == f"شركة {name}"))).scalar_one().id)
            for name in ("ألف", "باء")
        }
    return {"users": users, "leads": leads, "clients": clients}


@pytest.fixture(scope="session", autouse=True)
def _quiet_env() -> None:
    os.environ.setdefault("TZ", "UTC")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """`postgres_only` tests are skipped, never silently passed, when the run is on SQLite."""
    if IS_POSTGRES:
        return
    skip = pytest.mark.skip(reason="requires DATABASE_URL_TEST pointing at PostgreSQL")
    for item in items:
        if item.get_closest_marker("postgres_only"):
            item.add_marker(skip)
