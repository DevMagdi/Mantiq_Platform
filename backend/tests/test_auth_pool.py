"""R8 — a login must not sit on a pooled database connection while Argon2 runs.

The evidence this file pins down (raw/`s7-login-rate.json` + the API log): 25 concurrent readers plus a
10 logins/s arrival rate produced `QueuePool limit of size 10 overflow 20 reached, connection timed out,
timeout 30.00` and two `POST /auth/login` answers of **500** after 19–35 s.

The mechanism is not the pool size: SQLAlchemy keeps a connection checked out from a session's first
statement until its transaction ends, and `services.auth.login` verified the password *between* the query and
the commit. Every queued login therefore occupied one of the 30 slots while it was only waiting for CPU.
`login` now commits the read before hashing, so the connection goes back to the pool for the duration of the
verify.

The engine in the test has exactly **one** connection: if the hash still held it, the second session could
not even `SELECT 1` until the login finished. That is a fact about the code, not about tuning.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.core.db import get_engine, set_sessionmaker
from app.services import auth as auth_svc
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.conftest import IS_POSTGRES, PASSWORD, TEST_DB, TEST_URL
from tests.test_realtime import served_app

MANAGER = "manager@brokerhub.test"
#: long enough to be unmistakable next to a millisecond query, short enough to keep the suite quick
HASH_WINDOW_S = 1.0


async def test_a_login_does_not_hold_a_connection_while_hashing(engine) -> None:
    # `engine` (conftest) is what creates the schema and seeds the manager this test signs in as
    one = create_async_engine(
        TEST_URL if IS_POSTGRES else f"sqlite+aiosqlite:///{TEST_DB}",
        pool_size=1,
        max_overflow=0,
        pool_timeout=2,
        future=True,
    )
    set_sessionmaker(async_sessionmaker(one, expire_on_commit=False, autoflush=False))
    original_verify = auth_svc.verify_password_async
    seen: dict[str, Any] = {}

    async def slow_verify(password: str, hashed: str) -> bool:
        # what the pool looks like *while* the arithmetic-heavy part is running
        seen["checkedout"] = one.pool.checkedout()
        await asyncio.sleep(HASH_WINDOW_S)
        return await original_verify(password, hashed)

    auth_svc.verify_password_async = slow_verify  # type: ignore[assignment]
    try:
        async with served_app() as base:
            import httpx

            async with httpx.AsyncClient(base_url=base, timeout=30) as http:
                login_task = asyncio.create_task(http.post("/api/v1/auth/login", json={"email": MANAGER, "password": PASSWORD}))
                await asyncio.sleep(0.25)  # the request is now inside the hash

                started = time.perf_counter()
                async with async_sessionmaker(one, expire_on_commit=False)() as probe:
                    await probe.execute(text("SELECT 1"))
                elapsed = time.perf_counter() - started

                login = await login_task
                assert login.status_code == 200, login.text

        assert seen.get("checkedout") == 0, f"the login kept {seen.get('checkedout')} connection(s) while hashing"
        assert elapsed < HASH_WINDOW_S / 2, (
            f"another session waited {elapsed:.2f}s for the pool during a login — the hash is still holding the "
            "connection (see the docstring: this is the 500 in raw/s7-login-rate.json)"
        )
    finally:
        auth_svc.verify_password_async = original_verify  # type: ignore[assignment]
        await one.dispose()
        set_sessionmaker(async_sessionmaker(get_engine(), expire_on_commit=False, autoflush=False))
