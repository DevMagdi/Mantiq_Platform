from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.serializers import client_brief, contact_brief, import_job_brief, insured_brief
from app.api.v1.deps import (
    ClientsManage,
    ClientsView,
    DbSession,
    InsuredManage,
    PageQuery,
    ScopeDep,
)
from app.core.errors import NotFound, TooLarge, ValidationFailed
from app.core.pagination import Page
from app.domain.enums import ClientStatus, CoverageStatus
from app.middleware_ip import ip_of
from app.models.clients import ImportJob
from app.repositories import clients as repo
from app.schemas.clients import ClientCreate, ClientUpdate, ContactCreate, InsuredCreate, InsuredUpdate
from app.schemas.common import EmailAddress
from app.schemas.out import (
    AttachmentOut,
    CallOut,
    ClientKpiRowOut,
    ClientOut,
    ClientOverviewOut,
    ClientPageOut,
    ContactOut,
    ImportJobOut,
    InsuredListOut,
    InsuredOut,
)
from app.services import clients as svc
from app.services import portal_accounts

router = APIRouter(tags=["clients"])

IMPORT_MAX_BYTES = 5_000_000


@router.get("/clients", response_model=ClientPageOut)
async def list_clients(
    session: DbSession,
    scope: ScopeDep,
    params: PageQuery,
    actor: ClientsView,
    q: str | None = None,
    status: ClientStatus | None = None,
    only_converted: bool | None = None,
) -> Page[Any]:
    items, total = await repo.list_page(session, scope, q=q, status=status, only_converted=only_converted, params=params)
    counts = await repo.counts_for(session, [c.id for c in items])
    return Page.build(
        [client_brief(c, counts=counts.get(c.id, {})) for c in items],
        total,
        params,  # type: ignore[list-item]
    )


@router.get("/clients/kpis", summary="Screen 8 KPI row", response_model=ClientKpiRowOut)
async def client_kpis(session: DbSession, scope: ScopeDep, actor: ClientsView) -> dict[str, Any]:
    return await repo.kpis(session, scope)


@router.post("/clients", status_code=201)
async def create_client(body: ClientCreate, session: DbSession, scope: ScopeDep, actor: ClientsManage) -> dict[str, Any]:
    client = await svc.create(session, actor, scope, body.model_dump(exclude_unset=True))
    await session.commit()
    return client_brief(client)


@router.get("/clients/{client_id}", response_model=ClientOut)
async def get_client(client_id: UUID, session: DbSession, scope: ScopeDep, actor: ClientsView) -> dict[str, Any]:
    client = await svc.get(session, client_id, scope)
    counts = await repo.counts_for(session, [client.id])
    return client_brief(client, counts=counts.get(client.id, {}))


@router.patch("/clients/{client_id}")
async def patch_client(client_id: UUID, body: ClientUpdate, session: DbSession, scope: ScopeDep, actor: ClientsManage) -> dict[str, Any]:
    client = await svc.update(session, actor, scope, client_id, body.model_dump(exclude_unset=True))
    await session.commit()
    return client_brief(client)


@router.get("/clients/{client_id}/overview", summary="Client file: KPIs + contract card", response_model=ClientOverviewOut)
async def overview(client_id: UUID, session: DbSession, scope: ScopeDep, actor: ClientsView) -> dict[str, Any]:
    client = await svc.get(session, client_id, scope)
    data = await svc.overview(session, client, scope)
    return {
        "client": client_brief(client),
        "contacts": [contact_brief(c) for c in client.contacts],
        **data,
    }


@router.get("/clients/{client_id}/contacts", response_model=list[ContactOut])
async def contacts(client_id: UUID, session: DbSession, scope: ScopeDep, actor: ClientsView) -> list[dict[str, Any]]:
    client = await svc.get(session, client_id, scope)
    return [contact_brief(c) for c in client.contacts]


@router.post("/clients/{client_id}/contacts", status_code=201)
async def add_contact(client_id: UUID, body: ContactCreate, session: DbSession, scope: ScopeDep, actor: ClientsManage) -> dict[str, Any]:
    contact = await svc.add_contact(session, actor, scope, client_id, body.model_dump())
    await session.commit()
    return contact_brief(contact)


@router.get("/clients/{client_id}/requests")
async def client_requests(client_id: UUID, session: DbSession, scope: ScopeDep, params: PageQuery, actor: ClientsView) -> Page[Any]:
    from app.repositories import requests_repo

    await svc.get(session, client_id, scope)
    items, total = await requests_repo.list_page(session, scope, client_id=client_id, params=params)
    from app.api.serializers import request_brief

    return Page.build([request_brief(r) for r in items], total, params)  # type: ignore[arg-type]


@router.get("/clients/{client_id}/insured", response_model=InsuredListOut)
async def list_insured(
    client_id: UUID,
    session: DbSession,
    scope: ScopeDep,
    params: PageQuery,
    actor: ClientsView,
    q: str | None = None,
    coverage_status: CoverageStatus | None = None,
) -> dict[str, Any]:
    await svc.get(session, client_id, scope)
    items, total = await repo.list_insured(session, client_id, scope, q=q, coverage_status=coverage_status, params=params)
    kpis = {
        "total": total,
        "active": sum(1 for i in items if i.coverage_status == CoverageStatus.ACTIVE),
        "pending": sum(1 for i in items if i.coverage_status == CoverageStatus.PENDING),
        "ended": sum(1 for i in items if i.coverage_status == CoverageStatus.ENDED),
    }
    return {"page": Page.build([insured_brief(i) for i in items], total, params).model_dump(), "kpis": kpis}


@router.post("/clients/{client_id}/insured", status_code=201)
async def add_insured(client_id: UUID, body: InsuredCreate, session: DbSession, scope: ScopeDep, actor: InsuredManage) -> dict[str, Any]:
    person = await svc.add_insured(session, actor, scope, client_id, body.model_dump())
    await session.commit()
    return insured_brief(person)


@router.get("/clients/{client_id}/documents", summary="Files attached to this client's requests", response_model=list[AttachmentOut])
async def client_documents(client_id: UUID, session: DbSession, scope: ScopeDep, actor: ClientsView) -> list[dict[str, Any]]:
    """Documents tab of the client file (screen 5)."""
    from app.core.security import make_signed_media_url
    from app.models.requests import Attachment, ServiceRequest

    client = await svc.get(session, client_id, scope)
    rows = list(
        (
            await session.execute(
                select(Attachment)
                .join(ServiceRequest, ServiceRequest.id == Attachment.request_id)
                .where(ServiceRequest.client_id == client.id)
                .order_by(Attachment.created_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    out: list[dict[str, Any]] = []
    for attachment in rows:
        url: str | None = None
        if attachment.object_key:
            url, _expires = make_signed_media_url(attachment.object_key, 300)
        out.append(
            {
                "id": str(attachment.id),
                "filename": attachment.filename,
                "mime": attachment.mime,
                "size": attachment.size,
                "created_at": attachment.created_at,
                "url": url,
            }
        )
    return out


@router.get("/clients/{client_id}/calls", summary="Call log of this client's requests", response_model=list[CallOut])
async def client_calls(client_id: UUID, session: DbSession, scope: ScopeDep, actor: ClientsView) -> list[dict[str, Any]]:
    """Calls tab of the client file (screen 5)."""
    from app.api.serializers import call_brief
    from app.models.requests import CallRecord, ServiceRequest

    client = await svc.get(session, client_id, scope)
    rows = list(
        (
            await session.execute(
                select(CallRecord)
                .join(ServiceRequest, ServiceRequest.id == CallRecord.request_id)
                .where(ServiceRequest.client_id == client.id)
                .order_by(CallRecord.started_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return [call_brief(row, scope=scope) for row in rows]


@router.patch("/insured/{insured_id}", response_model=InsuredOut)
async def patch_insured(insured_id: UUID, body: InsuredUpdate, session: DbSession, scope: ScopeDep, actor: InsuredManage) -> dict[str, Any]:
    person = await svc.update_insured(session, actor, scope, insured_id, body.model_dump(exclude_unset=True))
    await session.commit()
    return insured_brief(person)


@router.post(
    "/clients/{client_id}/insured/import",
    status_code=201,
    summary="Upload .xlsx → dry-run preview with row errors",
    response_model=ImportJobOut,
)
async def import_insured(
    client_id: UUID,
    session: DbSession,
    scope: ScopeDep,
    actor: InsuredManage,
    file: Annotated[UploadFile, File()],
    confirm: Annotated[bool, Form()] = False,
) -> dict[str, Any]:
    name = (file.filename or "import.xlsx").lower()
    if not name.endswith(".xlsx"):
        raise ValidationFailed("Upload a .xlsx workbook", details={"filename": file.filename})
    content = await file.read()
    if len(content) > IMPORT_MAX_BYTES:
        raise TooLarge(f"File too large (limit {IMPORT_MAX_BYTES // 1_000_000} MB)")
    rows = await svc.parse_insured_workbook(content, name)
    job = await svc.import_insured_dry_run(session, actor, scope, client_id, rows=rows, filename=file.filename)
    await session.commit()
    payload = import_job_brief(job)
    payload["confirm_required"] = True
    payload["commit_url"] = f"/api/v1/import-jobs/{job.id}/commit"
    return payload


@router.post("/import-jobs/{job_id}/commit", summary="Apply a previewed import", response_model=ImportJobOut)
async def commit_import(job_id: UUID, session: DbSession, scope: ScopeDep, actor: InsuredManage) -> dict[str, Any]:
    job = await svc.commit_import(session, actor, scope, job_id)
    await session.commit()
    return import_job_brief(job)


@router.get("/import-jobs/{job_id}", response_model=ImportJobOut)
async def get_import_job(job_id: UUID, session: DbSession, scope: ScopeDep, actor: ClientsView) -> dict[str, Any]:
    job = await session.get(ImportJob, job_id)
    if job is None or (await repo.get_by_id(session, job.client_id, scope)) is None:
        raise NotFound("Import job not found")
    return import_job_brief(job)


# ---------------------------------------------------------------------------------------------
# portal accounts (ADR-022): the client owner's own login, created from the client file
# ---------------------------------------------------------------------------------------------


class PortalAccountIn(BaseModel):
    """`email` or `username` identifies the login; a blank password means "generate and show it once"."""

    full_name: str | None = Field(default=None, min_length=2, max_length=160)
    email: EmailAddress | None = None
    username: str | None = Field(default=None, min_length=3, max_length=63)
    password: str = Field(default="", max_length=200)
    role_code: str = Field(default="client_owner", min_length=3, max_length=32)


class PortalPasswordIn(BaseModel):
    password: str = Field(default="", max_length=200)


class PortalUsernameIn(BaseModel):
    username: str = Field(min_length=3, max_length=63, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{2,62}$")


class PortalActiveIn(BaseModel):
    is_active: bool


@router.get("/clients/{client_id}/portal-accounts", summary="Portal logins of one client")
async def list_portal_accounts(client_id: UUID, session: DbSession, scope: ScopeDep, actor: ClientsManage) -> list[dict[str, Any]]:
    return await portal_accounts.list_accounts(session, scope, client_id)


@router.post("/clients/{client_id}/portal-accounts", status_code=201, summary="Create a portal login (temp password returned once)")
async def create_portal_account(
    client_id: UUID, body: PortalAccountIn, session: DbSession, scope: ScopeDep, actor: ClientsManage
) -> dict[str, Any]:
    result = await portal_accounts.create_account(session, actor, scope, client_id, body.model_dump(), ip=ip_of())
    await session.commit()
    return result


@router.post("/clients/{client_id}/portal-accounts/{user_id}/password", summary="Reset a portal password (returned once)")
async def reset_portal_password(
    client_id: UUID, user_id: UUID, body: PortalPasswordIn, session: DbSession, scope: ScopeDep, actor: ClientsManage
) -> dict[str, Any]:
    result = await portal_accounts.reset_password(session, actor, scope, client_id, user_id, body.model_dump(), ip=ip_of())
    await session.commit()
    return result


@router.patch("/clients/{client_id}/portal-accounts/{user_id}", summary="Enable/disable a portal login")
async def patch_portal_account(
    client_id: UUID, user_id: UUID, body: PortalActiveIn, session: DbSession, scope: ScopeDep, actor: ClientsManage
) -> dict[str, Any]:
    result = await portal_accounts.set_active(session, actor, scope, client_id, user_id, is_active=body.is_active, ip=ip_of())
    await session.commit()
    return result


@router.put("/clients/{client_id}/portal-accounts/{user_id}/username", summary="Change a portal username")
async def put_portal_username(
    client_id: UUID, user_id: UUID, body: PortalUsernameIn, session: DbSession, scope: ScopeDep, actor: ClientsManage
) -> dict[str, Any]:
    result = await portal_accounts.update_username(session, actor, scope, client_id, user_id, body.username, ip=ip_of())
    await session.commit()
    return result
