from __future__ import annotations

import pytest

from tests.helpers import login

pytestmark = pytest.mark.integration


async def test_login_returns_access_token_and_profile(client, ids) -> None:
    async with login(client, "rep_a@brokerhub.test") as h:
        me = (await client.get("/api/v1/auth/me", headers=h)).json()
        assert me["role"] == "sales_rep"
        assert me["scope_mode"] == "owner"
        assert "leads.view_own" in me["permissions"]
        assert "leads.view_all" not in me["permissions"]


async def test_wrong_password_is_401(client) -> None:
    response = await client.post("/api/v1/auth/login", json={"email": "rep_a@brokerhub.test", "password": "nope"})
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


async def test_brute_force_lockout(client) -> None:
    from app.core.config import get_settings
    from app.services import auth

    auth.clear_failures("rep_a@brokerhub.test|local")
    limit = get_settings().LOGIN_MAX_ATTEMPTS
    for _ in range(limit):
        assert (await client.post("/api/v1/auth/login", json={"email": "rep_a@brokerhub.test", "password": "x"})).status_code == 401
    locked = await client.post("/api/v1/auth/login", json={"email": "rep_a@brokerhub.test", "password": "x"})
    assert locked.status_code == 429
    assert "locked" in locked.json()["message"].lower()
    auth.clear_failures("rep_a@brokerhub.test|local")


async def test_refresh_rotates_and_invalidates_reuse(client) -> None:
    login_response = await client.post("/api/v1/auth/login", json={"email": "manager@brokerhub.test", "password": PASSWORD})
    refresh_cookie = login_response.cookies.get("bh_refresh")
    assert refresh_cookie, "refresh token must be an HttpOnly cookie"
    first = await client.post("/api/v1/auth/refresh", cookies={"bh_refresh": refresh_cookie})
    assert first.status_code == 200
    replay = await client.post("/api/v1/auth/refresh", cookies={"bh_refresh": refresh_cookie})
    assert replay.status_code == 401, "a rotated refresh token must not be reusable"


async def test_health_and_readiness(client) -> None:
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    ready = await client.get("/readyz")
    assert ready.status_code == 200


from tests.conftest import PASSWORD  # noqa: E402


async def test_password_hashing_does_not_block_the_event_loop(client, monkeypatch) -> None:
    """A burst of sign-ins must not freeze every other request in the process.

    Argon2 costs ~110 ms per check, so running it inline means a handful of simultaneous logins is a
    self-inflicted outage: measured before the fix, 16 concurrent wrong passwords held `/healthz` for
    **1.24 s**, and a CI e2e run lost 21 mobile tests to 20 s timeouts in exactly that window
    (`settings.spec.ts` reported `login failed for manager@brokerhub.test`).

    Two assertions, neither of which measures how fast the machine is:

    * **overlap** — the patched hasher records how many checks run *at the same time*. On the event loop
      that number can only ever be 1, because a blocking check stops the loop from starting the next one.
    * **liveness** — `/healthz` must not queue behind the burst, measured against its own baseline.
    """
    import asyncio
    import threading
    import time

    from app.core import security
    from app.services import auth

    lock = threading.Lock()
    state = {"running": 0, "peak": 0}

    def slow_verify(password: str, hashed: str) -> bool:  # same signature as the real one
        with lock:
            state["running"] += 1
            state["peak"] = max(state["peak"], state["running"])
        time.sleep(0.3)
        with lock:
            state["running"] -= 1
        return False

    monkeypatch.setattr(security, "verify_password", slow_verify)
    auth.clear_failures("rep_a@brokerhub.test|local")
    # a baseline first: CI's runners are busy, so an absolute millisecond limit measures the machine,
    # not the code — what matters is the *extra* wait the hashes add on top of it.
    base_ms = 0.0
    for _ in range(3):
        started = time.perf_counter()
        assert (await client.get("/healthz")).status_code == 200
        base_ms = max(base_ms, (time.perf_counter() - started) * 1000)
    try:
        # 4 concurrent failures stay under LOGIN_MAX_ATTEMPTS (5)
        logins = [
            asyncio.create_task(client.post("/api/v1/auth/login", json={"email": "rep_a@brokerhub.test", "password": "x"}))
            for _ in range(4)
        ]
        await asyncio.sleep(0.05)  # let them all reach the hasher
        started = time.perf_counter()
        health = await asyncio.wait_for(client.get("/healthz"), timeout=5)
        waited_ms = (time.perf_counter() - started) * 1000
        assert [response.status_code for response in await asyncio.gather(*logins)] == [401] * 4
        assert health.status_code == 200
        assert state["peak"] > 1, "the password checks ran one at a time — they are on the event loop"
        added_ms = waited_ms - base_ms
        assert added_ms < 500, f"/healthz waited {waited_ms:.0f} ms (baseline {base_ms:.0f} ms) behind the password hashes"
    finally:
        auth.clear_failures("rep_a@brokerhub.test|local")
