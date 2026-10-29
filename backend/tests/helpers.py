"""Login helper returning an Authorization header context manager."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from tests.conftest import PASSWORD


@asynccontextmanager
async def login(client: Any, email: str, password: str = PASSWORD) -> AsyncIterator[dict[str, str]]:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    yield {"Authorization": f"Bearer {response.json()['access_token']}"}
