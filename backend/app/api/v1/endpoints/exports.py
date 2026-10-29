"""`POST /api/v1/exports/{kind}` — scoped table dumps as XLSX (spec §7, §6 "التصدير داخل النطاق").

The file is generated in-process: the row count is capped, the same scope predicates that the list
endpoints use are applied, and every export is written to `audit_logs`. PDF is deliberately absent —
see docs/decisions/010-exports.md.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Path, Response
from fastapi.responses import JSONResponse

from app.api.v1.deps import DbSession, ReportsExport, ScopeDep
from app.core.errors import FeatureDisabled
from app.domain.enums import RequestStatus
from app.schemas.exports import ExportRequest
from app.services import audit, exports
from app.services.feature_flags import PHASE2_FLAG, flag_enabled

router = APIRouter(tags=["exports"])

_MEDIA_TYPE = exports.XLSX_MEDIA_TYPE
_FLAGGED = {exports.ExportKind.INSURER_PERFORMANCE}


@router.post(
    "/{kind}",
    summary="Export a scoped table as XLSX (audited)",
    response_class=Response,
    responses={
        200: {
            "description": "The workbook itself.",
            "content": {_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def export_table(
    kind: Annotated[exports.ExportKind, Path(description="which table to write")],
    session: DbSession,
    scope: ScopeDep,
    actor: ReportsExport,
    body: ExportRequest | None = None,
) -> Response:
    if kind in _FLAGGED and not await flag_enabled(session, PHASE2_FLAG):
        raise FeatureDisabled("This report belongs to Phase 2, which is not enabled yet", details={"flag": PHASE2_FLAG})

    payload = body or ExportRequest()
    result = await exports.build(
        kind,
        session,
        scope,
        statuses=payload.statuses,
        only_open=payload.only_open,
        only_overdue=payload.only_overdue,
        q=payload.q,
        client_id=payload.client_id,
    )

    from app.middleware_ip import ip_of

    await audit.write(
        session,
        actor=actor,
        action=f"export.{kind.value}",
        entity_type="export",
        entity_id=None,
        after={"rows": result.rows, "truncated": result.truncated, "sheet": result.sheet, "filters": _filters_of(payload)},
        ip=ip_of(),
    )
    await session.commit()

    headers = {
        "Content-Disposition": f'attachment; filename="{result.filename}"',
        "X-Export-Rows": str(result.rows),
        "X-Export-Truncated": "1" if result.truncated else "0",
        "Cache-Control": "no-store",
    }
    return Response(content=result.content, media_type=result.media_type, headers=headers)


def _filters_of(payload: ExportRequest) -> dict[str, Any]:
    """Only the filters the file was actually built with — the audit row must describe the artifact."""
    data: dict[str, Any] = {}
    if payload.statuses:
        data["statuses"] = [RequestStatus(s).value for s in payload.statuses]
    if payload.only_open:
        data["only_open"] = True
    if payload.only_overdue:
        data["only_overdue"] = True
    if payload.q:
        data["q"] = payload.q
    if isinstance(payload.client_id, UUID):
        data["client_id"] = str(payload.client_id)
    return data


@router.get(
    "",
    summary="Export catalogue: which kinds exist and what they accept",
    response_class=JSONResponse,
)
async def export_kinds() -> dict[str, Any]:
    return {
        "media_type": _MEDIA_TYPE,
        "row_cap": exports.EXPORT_CAP,
        "kinds": [
            {
                "kind": kind.value,
                "sheet": exports.SHEET_TITLE[kind],
                "columns": [column.header for column in exports.COLUMNS[kind]],
                "requires_flag": (kind in _FLAGGED and PHASE2_FLAG) or None,
            }
            for kind in exports.ExportKind
        ],
    }
