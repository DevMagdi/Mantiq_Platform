from __future__ import annotations

from app.workers._runtime import run_async
from app.workers.celery_app import celery_app


@celery_app.task(name="broker_hub.run_insured_import")
def run_insured_import(job_id: str) -> dict[str, int]:
    """Async path for big workbooks; the HTTP API commits synchronously for small files."""

    async def body() -> dict[str, int]:
        from uuid import UUID

        from app.core.db import get_sessionmaker
        from app.core.scope import build_scope
        from app.models.auth import User
        from app.models.clients import Client
        from app.services import clients as svc

        async with get_sessionmaker()() as session:
            from app.models.clients import ImportJob

            job = await session.get(ImportJob, UUID(job_id))
            if job is None:
                return {"created": 0, "updated": 0}
            actor = await session.get(User, job.created_by) if job.created_by else None
            client = await session.get(Client, job.client_id)
            if client is None or actor is None:
                return {"created": 0, "updated": 0}
            committed = await svc.commit_import(session, actor, build_scope(actor), job.id)
            await session.commit()
            return {"created": committed.stats.get("created", 0), "updated": committed.stats.get("updated", 0)}

    return run_async(body)()
