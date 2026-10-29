from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.v1.deps import DbSession, ScopeDep
from app.core.errors import Forbidden, NotFound
from app.core.scope import Scope
from app.schemas.out import (
    PortalDocumentOut,
    PortalOverviewOut,
    PortalPolicyOut,
    PortalRequestOut,
    PortalRequestsOut,
)
from app.services import portal

router = APIRouter(tags=["client portal"])


class _OpenRequest(BaseModel):
    subject: str = Field(min_length=3, max_length=255)
    body: str | None = Field(default=None, max_length=4000)


def _require_portal(scope: Scope) -> None:
    if not scope.is_portal:
        raise Forbidden("Client-portal accounts only")


@router.get("/portal/overview", summary="Owner header, KPIs and contract summary", response_model=PortalOverviewOut)
async def overview(session: DbSession, scope: ScopeDep) -> dict[str, Any]:
    _require_portal(scope)
    return await portal.overview(session, scope)


@router.get("/portal/requests", summary="My employees' requests (no internal notes, no reps)", response_model=PortalRequestsOut)
async def requests(
    session: DbSession,
    scope: ScopeDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    _require_portal(scope)
    return await portal.my_requests(session, scope, page=page, page_size=page_size)


@router.post("/portal/requests", status_code=201, summary="Owner opens a request", response_model=PortalRequestOut)
async def open_request(body: _OpenRequest, session: DbSession, scope: ScopeDep) -> dict[str, Any]:
    _require_portal(scope)
    result = await portal.create_request(session, scope, subject=body.subject, body=body.body)
    await session.commit()
    return result


@router.get("/portal/documents", summary="Contract documents", response_model=list[PortalDocumentOut])
async def documents(session: DbSession, scope: ScopeDep) -> list[dict[str, Any]]:
    _require_portal(scope)
    return await portal.documents(session, scope)


@router.get("/portal/policies", summary="My contracts, with their real status and document", response_model=list[PortalPolicyOut])
async def policies(session: DbSession, scope: ScopeDep) -> list[dict[str, Any]]:
    _require_portal(scope)
    return await portal.my_policies(session, scope)


@router.get("/portal/policies/{policy_id}/document", summary="Download my issued policy PDF")
async def policy_file(policy_id: UUID, session: DbSession, scope: ScopeDep) -> Response:
    _require_portal(scope)
    filename, mime, payload = await portal.policy_document(session, scope, policy_id)
    return Response(
        content=payload,
        media_type=mime,
        headers={"content-disposition": f'inline; filename="{filename}"'},
    )


@router.get("/portal/attachments/{attachment_id}", summary="Download a document (portal-safe)")
async def download(attachment_id: UUID, session: DbSession, scope: ScopeDep) -> Response:
    from app.integrations.storage import storage
    from app.models.requests import Attachment

    _require_portal(scope)
    row = await session.get(Attachment, attachment_id)
    if row is None:
        raise NotFound("Document not found")
    # a document the owner is not allowed to see answers 404, never 403 (spec §6)
    allowed = {d["id"] for d in await portal.documents(session, scope)}
    if str(row.id) not in allowed:
        raise NotFound("Document not found")
    return Response(
        content=storage().get_object(row.object_key),
        media_type=row.mime,
        headers={"content-disposition": f'attachment; filename="{row.filename}"'},
    )
