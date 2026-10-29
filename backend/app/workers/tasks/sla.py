from __future__ import annotations

from app.workers._runtime import run_async
from app.workers.celery_app import celery_app


@celery_app.task(name="broker_hub.sla_sweep")
def sla_sweep() -> dict[str, int]:
    """Every minute: flip `is_overdue`, notify late + approaching (idempotent, self-healing)."""

    async def body() -> dict[str, int]:
        from app.core.db import get_sessionmaker
        from app.services import requests_svc

        async with get_sessionmaker()() as session:
            result = await requests_svc.sweep_overdue(session)
            await session.commit()
            return result

    return run_async(body)()
