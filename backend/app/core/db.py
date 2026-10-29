"""Engine/session factory. Works on PostgreSQL (prod/CI) and SQLite (dev/tests, no Docker)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.models.base import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def make_engine(url: str | None = None, *, echo: bool | None = None) -> AsyncEngine:
    settings = get_settings()
    target = url or settings.DATABASE_URL
    kwargs: dict[str, Any] = {"echo": settings.DB_ECHO if echo is None else echo, "future": True}
    if target.startswith("postgresql"):
        kwargs.update(pool_size=10, max_overflow=20, pool_pre_ping=True)
    elif target.startswith("sqlite") and ":memory:" not in target and "mode=memory" not in target:
        # SQLite file databases are pooled in SQLAlchemy 2.0, and the *dialect default* is 5 + 10 — a limit
        # nobody chose. The e2e/eval deployment runs on such a file, and parallel browser sessions exhausted
        # those 15 connections with `QueuePool limit of size 5 overflow 10 reached` (R7). The size is now the
        # same explicit decision it already was for PostgreSQL, instead of an inherited accident.
        kwargs.update(pool_size=10, max_overflow=20)
    return create_async_engine(target, **kwargs)


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = make_engine()
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False, autoflush=False)
    return _sessionmaker


def set_sessionmaker(sm: async_sessionmaker[AsyncSession] | None) -> None:
    """Point the app at a different database (tests use a throw-away file)."""
    global _sessionmaker
    _sessionmaker = sm


def set_engine(engine: AsyncEngine | None) -> None:
    global _engine
    _engine = engine


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    sm = get_sessionmaker()
    async with sm() as session:
        yield session


async def create_dev_schema() -> None:
    """create_all for dev/tests. Production uses Alembic (see migrations/)."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if engine.dialect.name == "sqlite":
            await conn.exec_driver_sql("PRAGMA foreign_keys=ON")


def supports_pg_features() -> bool:
    return get_settings().DATABASE_URL.startswith("postgresql")


_ = sa  # re-exported for typing convenience in migrations/env.py
