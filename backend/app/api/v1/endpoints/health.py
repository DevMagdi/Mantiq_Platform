from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from sqlalchemy import text

from app.api.v1.deps import DbSession, SettingsManage
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.metrics import CONTENT_TYPE_LATEST, exposition

router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Liveness")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness (DB + migrations at head)")
async def readyz(request: Request) -> Any:
    try:
        async with get_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover
        return request.app.state.json_error(503, "not_ready", str(exc))
    return {"status": "ready", "database": "ok"}


@router.get("/api/v1/ops/summary", summary="Operations health: DB, integrations, storage (B3)")
async def ops_summary(session: DbSession, actor: SettingsManage) -> Any:
    """One screen's worth of «is it alive»: readiness, each channel's connection state + last webhook,
    and object-storage usage. Staff-only (`settings.manage`) — it names integration errors."""
    import os

    import anyio

    from app.services import integrations_settings

    db_ok = True
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    states = await integrations_settings.all_states(session)

    def _usage() -> tuple[int, int]:
        files = 0
        total = 0
        root = get_settings().STORAGE_DIR
        if root.exists():
            for dirpath, _dirs, names in os.walk(root):
                for name in names:
                    try:
                        total += os.path.getsize(os.path.join(dirpath, name))
                        files += 1
                    except OSError:
                        continue
        return files, total

    files, bytes_used = await anyio.to_thread.run_sync(_usage)
    return {
        "database": "ok" if db_ok else "error",
        "integrations": [
            {
                "provider": s["provider"],
                "connection_status": s.get("connection_status"),
                "missing_fields": s.get("missing_fields", []),
                "last_webhook_at": s.get("last_webhook_at"),
                "last_error": s.get("last_error"),
                "last_test_status": s.get("last_test_status"),
            }
            for s in states
        ],
        "storage": {"files": files, "bytes": bytes_used},
    }


@router.get("/metrics", summary="Prometheus exposition (open in development, token-gated otherwise)")
async def metrics(request: Request) -> Response:
    """Scrape target for the runbook's monitoring table. Not part of the versioned API the SPA uses."""
    settings = get_settings()
    if settings.METRICS_TOKEN:
        supplied = request.headers.get("authorization", "")
        if supplied != f"Bearer {settings.METRICS_TOKEN}":
            return request.app.state.json_error(401, "metrics_unauthorized", "Send the metrics bearer token.")  # type: ignore[no-any-return]
    elif not settings.DEBUG:
        return request.app.state.json_error(403, "metrics_disabled", "Set METRICS_TOKEN to expose /metrics outside development.")  # type: ignore[no-any-return]
    return Response(content=exposition(), media_type=CONTENT_TYPE_LATEST)
