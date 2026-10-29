"""Async task runtime: one event loop + session per task invocation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")


def run_async(fn: Callable[[], Awaitable[T]]) -> Callable[..., T]:
    """Wrap an async service function into a Celery task body."""
    import asyncio
    from functools import wraps

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        async def invoke() -> T:
            return await fn(*args, **kwargs)  # type: ignore[call-arg]

        if loop is not None and loop.is_running():  # pragma: no cover - only under eager+asyncio
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(lambda: asyncio.run(invoke())).result()
        return asyncio.run(invoke())

    return wrapper


async def with_session() -> Any:
    from app.core.db import get_sessionmaker

    return get_sessionmaker()()
