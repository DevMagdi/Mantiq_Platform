from __future__ import annotations

from datetime import UTC
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, File, Query, UploadFile

from app.api.serializers import lead_activity, lead_brief, lead_detail
from app.api.v1.deps import DbSession, LeadsAssign, LeadsConvert, LeadsImport, LeadsView, PageQuery, ScopeDep
from app.core.errors import Forbidden, NotFound, TooLarge
from app.core.pagination import Page
from app.domain.enums import ActivityType, LeadStatus
from app.middleware_ip import ip_of
from app.repositories import leads as repo
from app.schemas.leads import (
    LeadAssign,
    LeadBulkAssign,
    LeadCreate,
    LeadLose,
    LeadMeetingCreate,
    LeadNoteCreate,
    LeadStatusUpdate,
    LeadUpdate,
)
from app.schemas.out import (
    LeadActivityOut,
    LeadBoardOut,
    LeadBulkAssignOut,
    LeadConvertOut,
    LeadDetailOut,
    LeadImportOut,
    LeadPageOut,
    LeadStatsOut,
    RepWorkloadOut,
)
from app.services import lead_import
from app.services import leads as svc

router = APIRouter(tags=["leads"])


@router.get("/leads", summary="Lead list (status/assignee/search/overdue filters)", response_model=LeadPageOut)
async def list_leads(
    session: DbSession,
    scope: ScopeDep,
    params: PageQuery,
    actor: LeadsView,
    status: Annotated[list[LeadStatus] | None, Query(alias="status", description="repeatable")] = None,
    assigned_to: UUID | None = None,
    q: str | None = None,
    followup_overdue: bool = False,
    unassigned: bool = False,
) -> Page[Any]:
    items, total = await repo.list_page(
        session,
        scope,
        statuses=status or None,
        assigned_to=assigned_to,
        q=q,
        followup_overdue=followup_overdue,
        unassigned=unassigned,
        params=params,
    )
    return Page.build([lead_brief(r) for r in items], total, params)  # type: ignore[arg-type]


@router.post("/leads", status_code=201, summary="Create a lead (live duplicate detection on mobile)")
async def create_lead(body: LeadCreate, session: DbSession, scope: ScopeDep, actor: LeadsView) -> dict[str, Any]:
    """Any lead owner may create; assigning to *someone else* needs leads.assign (enforced in the service)."""
    result = await svc.create(session, actor, scope, LeadCreate.model_validate(body), ip=ip_of())
    await session.commit()
    return result


@router.post("/leads/{lead_id}/status", summary="Kanban drop / status change")
async def change_status(lead_id: UUID, body: LeadStatusUpdate, session: DbSession, scope: ScopeDep, actor: LeadsView) -> dict[str, Any]:
    payload = body
    lead = await svc.set_status(session, actor, scope, lead_id, payload)
    await session.commit()
    return lead_brief(lead)


@router.post("/leads/{lead_id}/assign", summary="Assign / re-assign (managers only)")
async def assign(lead_id: UUID, body: LeadAssign, session: DbSession, scope: ScopeDep, actor: LeadsAssign) -> dict[str, Any]:
    raw = body.assigned_to
    lead = await svc.assign(session, actor, scope, lead_id, to_user_id=UUID(str(raw)) if raw else None)
    await session.commit()
    return lead_brief(lead)


@router.post("/leads/{lead_id}/convert", summary="Convert to a real client (atomic + idempotent)", response_model=LeadConvertOut)
async def convert(lead_id: UUID, session: DbSession, scope: ScopeDep, actor: LeadsConvert) -> dict[str, Any]:
    result = await svc.convert(session, actor, scope, lead_id)
    await session.commit()
    return result


@router.post("/leads/{lead_id}/lose", summary="Mark lost (reason required)")
async def lose(lead_id: UUID, body: LeadLose, session: DbSession, scope: ScopeDep, actor: LeadsView) -> dict[str, Any]:
    payload = body
    lead = await svc.lose(session, actor, scope, lead_id, lost_reason_id=payload.lost_reason_id, note=payload.note)
    await session.commit()
    return lead_brief(lead)


@router.get("/leads/board", summary="Kanban payload (5 columns, scoped; manager can filter by rep)", response_model=LeadBoardOut)
async def board(
    session: DbSession,
    scope: ScopeDep,
    actor: LeadsView,
    assigned_to: UUID | None = None,
    q: str | None = None,
    unassigned: bool = False,
) -> dict[str, Any]:
    """`assigned_to` narrows to one rep, `unassigned=true` shows the pool. Neither can widen the scope."""
    grouped = await repo.board(session, scope, assigned_to=assigned_to, q=q, unassigned=unassigned)
    return {"columns": [{"status": status, "count": len(rows), "items": [lead_brief(r) for r in rows]} for status, rows in grouped.items()]}


@router.get("/leads/stats", summary="Kanban KPI row", response_model=LeadStatsOut)
async def stats(session: DbSession, scope: ScopeDep, actor: LeadsView, assigned_to: UUID | None = None) -> dict[str, Any]:
    return await repo.stats(session, scope, rep_id=assigned_to)


@router.post(
    "/leads/assign-bulk", summary="Redistribute a selection or the whole unassigned pool (managers only)", response_model=LeadBulkAssignOut
)
async def assign_bulk(body: LeadBulkAssign, session: DbSession, scope: ScopeDep, actor: LeadsAssign) -> dict[str, Any]:
    """One transaction for the redistribute dialog: `lead_ids` for a chosen set, `unassigned_only` for the pool.

    The pool is selected server-side on purpose: a client-side loop over one page of `/leads` could only
    ever move `page_size` rows while telling the manager it moved the lot.
    """
    result = await svc.assign_bulk(
        session,
        actor,
        scope,
        lead_ids=body.lead_ids,
        unassigned_only=body.unassigned_only,
        to=body.to,
        reason=body.reason,
        ip=ip_of(),
    )
    await session.commit()
    return result


@router.post("/leads/import", status_code=201, summary="Upload .xlsx/.csv of leads → dry-run preview", response_model=LeadImportOut)
async def import_leads(
    session: DbSession,
    actor: LeadsImport,
    file: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    """Preview only. Nothing becomes a lead until the same job is committed (screen 11's rule, ADR-017)."""
    name = file.filename or "import.xlsx"
    content = await file.read()
    if len(content) > lead_import.MAX_FILE_BYTES:
        raise TooLarge(f"File too large (limit {lead_import.MAX_FILE_BYTES // 1_000_000} MB)")
    rows = lead_import.parse_workbook(content, name)
    job = await lead_import.dry_run(session, actor, rows=rows, filename=file.filename)
    await session.commit()
    payload = lead_import.brief(job)
    payload["confirm_required"] = True
    payload["commit_url"] = f"/api/v1/leads/import/{job.id}/commit"
    return payload  # type: ignore[no-any-return]


@router.post("/leads/import/{job_id}/commit", summary="Apply a previewed lead import", response_model=LeadImportOut)
async def commit_leads_import(job_id: UUID, session: DbSession, scope: ScopeDep, actor: LeadsImport) -> dict[str, Any]:
    job = await lead_import.commit(session, actor, scope, job_id, ip=ip_of())
    await session.commit()
    return lead_import.brief(job)


@router.get("/leads/import/{job_id}", summary="Read back a lead import's report", response_model=LeadImportOut)
async def get_leads_import(job_id: UUID, session: DbSession, actor: LeadsImport) -> dict[str, Any]:
    return lead_import.brief(await lead_import.get_job(session, job_id))


@router.get("/sales/team", summary="Per-rep load, meetings and conversion (manager only)", response_model=list[RepWorkloadOut])
async def team(session: DbSession, scope: ScopeDep) -> list[dict[str, Any]]:
    # 403, not 409: this is a permission rule, and the error code is what the UI branches on (§6)
    if not scope.can("leads.view_all") or not scope.can("leads.assign"):
        raise Forbidden("Manager access required", details={"required": ["leads.view_all", "leads.assign"]})
    rows = await repo.rep_workload(session)
    for row in rows:
        total = sum(row["stages"].values()) or 1
        won = row["won"]
        row["conversion_rate"] = round((won / (won + row["open_leads"] or 1)) * 100.0, 1)
        row["stages_pct"] = {k: round((v / total) * 100.0) for k, v in row["stages"].items()}
        row["load"] = "high" if row["open_leads"] > 20 else "balanced"
    return rows


@router.get("/leads/{lead_id}", response_model=LeadDetailOut)
async def get_lead(lead_id: UUID, session: DbSession, scope: ScopeDep, actor: LeadsView) -> dict[str, Any]:
    lead = await repo.get_by_id(session, lead_id, scope)
    if lead is None:
        raise NotFound("Lead not found")
    return lead_detail(lead)


@router.patch("/leads/{lead_id}", response_model=LeadDetailOut)
async def patch_lead(lead_id: UUID, body: LeadUpdate, session: DbSession, scope: ScopeDep, actor: LeadsView) -> dict[str, Any]:
    lead = await svc.update(session, actor, scope, lead_id, body)
    await session.commit()
    return lead_brief(lead)


@router.get("/leads/{lead_id}/activities", response_model=list[LeadActivityOut])
async def activities(lead_id: UUID, session: DbSession, scope: ScopeDep, actor: LeadsView) -> list[dict[str, Any]]:
    if await repo.get_by_id(session, lead_id, scope) is None:
        raise NotFound("Lead not found")
    rows = await repo.list_activities(session, lead_id, scope)
    return [lead_activity(r) for r in rows]


@router.post("/leads/{lead_id}/activities", status_code=201)
async def add_activity(lead_id: UUID, body: LeadNoteCreate, session: DbSession, scope: ScopeDep, actor: LeadsView) -> dict[str, Any]:
    note = body
    result = await svc.add_note(session, actor, scope, lead_id, note.body)
    await session.commit()
    return result


@router.post("/leads/{lead_id}/meetings", status_code=201)
async def add_meeting(lead_id: UUID, body: LeadMeetingCreate, session: DbSession, scope: ScopeDep, actor: LeadsView) -> dict[str, Any]:
    payload = body
    when = payload.scheduled_at
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    meeting = await svc.add_meeting(session, actor, scope, lead_id, scheduled_at=when, location=payload.location)
    await session.commit()
    return {
        "id": str(meeting.id),
        "scheduled_at": meeting.scheduled_at.isoformat(),
        "location": meeting.location,
        "type": ActivityType.MEETING.value,
    }
