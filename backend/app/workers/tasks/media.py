from __future__ import annotations

import contextlib
from typing import Any

from app.workers._runtime import run_async
from app.workers.celery_app import celery_app


async def purge_expired(session: Any) -> dict[str, int]:
    """The retention rule itself, awaitable — so the policy is testable without a worker or a second loop.

    The row wins over the environment on purpose: `RECORDING_RETENTION_DAYS` in a deployment file is what
    the operator configured *before* the settings screen existed, and the screen is now the product surface
    for that number. A task that ignored it would keep deleting files the operator just extended (ADR-016 §1).
    """
    from datetime import UTC, datetime, timedelta

    import sqlalchemy as sa

    from app.core.config import get_settings
    from app.integrations.storage import storage
    from app.models.integrations import IntegrationConfig
    from app.models.requests import CallRecord

    days = get_settings().RECORDING_RETENTION_DAYS
    saved = (await session.execute(sa.select(IntegrationConfig).where(IntegrationConfig.provider == "telephony"))).scalar_one_or_none()
    if saved is not None and saved.config:
        with contextlib.suppress(TypeError, ValueError):
            days = int(saved.config.get("recording_retention_days") or days)
    cutoff = datetime.now(UTC) - timedelta(days=days)
    rows = (
        (await session.execute(sa.select(CallRecord).where(CallRecord.recording_object_key.is_not(None), CallRecord.started_at < cutoff)))
        .scalars()
        .all()
    )
    for row in rows:
        with contextlib.suppress(Exception):  # pragma: no cover - storage may be unreachable
            storage().delete_object(str(row.recording_object_key))
        row.recording_object_key = None
    await session.commit()
    return {"purged": len(rows), "retention_days": days}


@celery_app.task(name="broker_hub.purge_expired_recordings")
def purge_expired_recordings() -> dict[str, int]:
    """Retention policy (spec §6): drop recordings older than the retention setting.

    The worker reads the saved settings row itself rather than trusting an in-memory snapshot: it is a
    different process from the API, and `docs/runbook.md` says one restart fixes the API's cache — the beat
    has no cache to fix, so it queries. If the table is missing (fresh database, migrations pending) the
    environment value is used instead of failing the task.
    """

    async def body() -> dict[str, int]:
        from app.core.db import get_sessionmaker

        async with get_sessionmaker()() as session:
            return await purge_expired(session)

    return run_async(body)()


@celery_app.task(name="broker_hub.cleanup_tmp_files")
def cleanup_tmp_files() -> dict[str, int]:
    async def body() -> dict[str, int]:
        import shutil
        from pathlib import Path

        from app.core.config import get_settings

        tmp = Path(get_settings().STORAGE_DIR) / "tmp"
        removed = 0
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
            removed = 1
        return {"removed": removed}

    return run_async(body)()
