"""Clients, contacts and insured persons (incl. Excel import with dry-run)."""

from __future__ import annotations

import io
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.scope import Scope
from app.domain.enums import (
    ClientStatus,
    ClientType,
    CoverageStatus,
    ImportJobStatus,
    ImportJobType,
)
from app.domain.phone import normalize_mobile
from app.models.auth import User
from app.models.clients import Client, ClientContact, ImportJob, InsuredPerson
from app.models.phase2 import Policy
from app.repositories import clients as clients_repo
from app.services import audit

_HEADERS = {
    "name": ("name", "الاسم", "الموظف", "employee", "full_name"),
    "mobile": ("mobile", "الموبايل", "موبايل", "phone", "الرقم"),
    "department": ("department", "القسم", "section"),
    "job_title": ("job_title", "المسمى", "title"),
    "national_id": ("national_id", "الرقم القومي", "nid"),
    "coverage_start": ("coverage_start", "بداية التغطية", "start"),
    "coverage_end": ("coverage_end", "نهاية التغطية", "end"),
    "external_ref": ("external_ref", "مرجع", "ref"),
}


async def create(session: AsyncSession, actor: User, scope: Scope, payload: dict[str, Any], *, ip: str | None = None) -> Client:
    if not scope.can_any("clients.manage", "clients.view_all"):
        raise Forbidden("Only managers may create clients directly")
    owner_id = payload.get("account_owner_id")
    if owner_id is not None and await session.get(User, UUID(str(owner_id))) is None:
        # same reason as `leads.create`: SQLite does not police the foreign key, so a typo would be
        # stored as a dangling owner and only PostgreSQL would notice — as a server error.
        raise ValidationFailed(
            "A referenced record does not exist",
            details=[{"field": "account_owner_id", "message": "غير موجود", "type": "unknown_reference"}],
        )
    client = Client(
        type=ClientType(payload.get("type", "company")),
        name=str(payload["name"]).strip(),
        status=ClientStatus(payload.get("status", "active")),
        account_owner_id=payload.get("account_owner_id") or actor.id,
        since_date=payload.get("since_date") or date.today(),
        tax_number=payload.get("tax_number"),
        address=payload.get("address"),
        notes=payload.get("notes"),
    )
    session.add(client)
    await session.flush()
    await audit.write(
        session,
        actor=actor,
        action="client.created",
        entity_type="client",
        entity_id=client.id,
        after=audit.snapshot(client, ("name", "type", "status", "account_owner_id")),
        ip=ip,
    )
    return client


async def update(
    session: AsyncSession, actor: User, scope: Scope, client_id: UUID, payload: dict[str, Any], *, ip: str | None = None
) -> Client:
    client = await get(session, client_id, scope)
    before = audit.snapshot(client, ("name", "status", "notes", "account_owner_id"))
    for field, value in payload.items():
        if value is None:
            continue
        # the wire model carries Literal[str]; the columns are enums, so coerce before setattr or the
        # next read of `client.status.value` explodes inside the serializer
        if field == "status":
            value = ClientStatus(value)
        elif field == "type":
            value = ClientType(value)
        setattr(client, field, value)
    await session.flush()
    await audit.write(
        session,
        actor=actor,
        action="client.updated",
        entity_type="client",
        entity_id=client.id,
        before=before,
        after=audit.snapshot(client, ("name", "status", "notes", "account_owner_id")),
        ip=ip,
    )
    return client


async def get(session: AsyncSession, client_id: UUID, scope: Scope) -> Client:
    client = await clients_repo.get_by_id(session, client_id, scope)
    if client is None:
        raise NotFound("Client not found")
    return client


async def overview(session: AsyncSession, client: Client, scope: Scope) -> dict[str, Any]:
    """Screen 5 KPIs + tabs payload (one batched pass, no N+1)."""
    from app.models.requests import CallRecord, ServiceRequest

    insured_total = int(
        (
            await session.execute(
                select(func.count(InsuredPerson.id)).where(InsuredPerson.client_id == client.id, InsuredPerson.deleted_at.is_(None))
            )
        ).scalar_one()
    )
    open_requests = int(
        (
            await session.execute(
                select(func.count(ServiceRequest.id)).where(
                    ServiceRequest.client_id == client.id,
                    ServiceRequest.status.notin_(["resolved", "closed"]),
                )
            )
        ).scalar_one()
    )
    policy = (
        await session.execute(select(Policy).where(Policy.client_id == client.id).order_by(Policy.created_at.desc()).limit(1))
    ).scalar_one_or_none()
    avg_minutes = await _avg_response_minutes(session, client.id)
    renewal_days = (policy.end_date - date.today()).days if policy and policy.end_date else None
    calls_count = int(
        (
            await session.execute(
                select(func.count(CallRecord.id))
                .join(ServiceRequest, ServiceRequest.id == CallRecord.request_id)
                .where(ServiceRequest.client_id == client.id)
            )
        ).scalar_one()
    )
    return {
        "kpis": {
            "insured": insured_total,
            "open_requests": open_requests,
            "avg_response_minutes": avg_minutes,
            "renewal_in_days": renewal_days,
            "calls": calls_count,
        },
        "policy": None
        if policy is None
        else {
            "id": str(policy.id),
            "policy_no": policy.policy_no,
            "status": policy.status.value,
            "payment_plan": policy.payment_plan.value,
            "start_date": policy.start_date.isoformat() if policy.start_date else None,
            "end_date": policy.end_date.isoformat() if policy.end_date else None,
            "premium_amount": float(policy.premium_amount) if policy.premium_amount else None,
            "commission_rate": float(policy.commission_rate) if policy.commission_rate is not None else None,
            "insurer": None if policy.insurer is None else {"id": str(policy.insurer.id), "name": policy.insurer.name},
            # `InsuranceTypeRefOut` is `{id, code, name}`: leaving `id` out made FastAPI refuse to serialise
            # the whole overview (500) for every client that actually has a policy — the screen the design
            # review photographs on screen 5.
            "insurance_type": None
            if policy.insurance_type is None
            else {"id": str(policy.insurance_type.id), "code": policy.insurance_type.code, "name": policy.insurance_type.name},
        },
    }


async def _avg_response_minutes(session: AsyncSession, client_id: UUID) -> float | None:
    from app.models.requests import ServiceRequest

    value = (
        await session.execute(
            select(
                func.avg(func.extract("epoch", ServiceRequest.first_response_at) - func.extract("epoch", ServiceRequest.created_at))
            ).where(ServiceRequest.client_id == client_id, ServiceRequest.first_response_at.is_not(None))
        )
    ).scalar_one_or_none()
    return round(float(value) / 60.0, 1) if value is not None else None


async def add_contact(session: AsyncSession, actor: User, scope: Scope, client_id: UUID, payload: dict[str, Any]) -> ClientContact:
    client = await get(session, client_id, scope)
    contact = ClientContact(
        client_id=client.id,
        name=payload["name"],
        title=payload.get("title"),
        mobile=normalize_mobile(payload.get("mobile")),
        email=payload.get("email"),
        is_primary=bool(payload.get("is_primary", False)),
    )
    if contact.is_primary:
        # exactly one primary per client: demote the previous holder first (an UPDATE, not a no-op SELECT)
        await session.execute(
            sa_update(ClientContact)
            .where(ClientContact.client_id == client.id, ClientContact.is_primary.is_(True))
            .values(is_primary=False)
        )
    session.add(contact)
    await session.flush()
    await audit.write(
        session, actor=actor, action="client.contact_added", entity_type="client", entity_id=client.id, after={"name": contact.name}
    )
    return contact


async def add_insured(
    session: AsyncSession, actor: User, scope: Scope, client_id: UUID, payload: dict[str, Any], *, ip: str | None = None
) -> InsuredPerson:
    client = await get(session, client_id, scope)
    person = InsuredPerson(
        client_id=client.id,
        name=payload["name"].strip(),
        mobile=normalize_mobile(payload.get("mobile")),
        national_id=payload.get("national_id"),
        department=payload.get("department"),
        job_title=payload.get("job_title"),
        coverage_status=CoverageStatus(payload.get("coverage_status", "active")),
        coverage_start=_as_date(payload.get("coverage_start")),
        coverage_end=_as_date(payload.get("coverage_end")),
        external_ref=payload.get("external_ref"),
        notes=payload.get("notes"),
    )
    session.add(person)
    await session.flush()
    await audit.write(
        session,
        actor=actor,
        action="insured.created",
        entity_type="insured_person",
        entity_id=person.id,
        after={"name": person.name, "client_id": str(client.id)},
        ip=ip,
    )
    return person


async def update_insured(
    session: AsyncSession, actor: User, scope: Scope, insured_id: UUID, payload: dict[str, Any], *, ip: str | None = None
) -> InsuredPerson:
    person = await session.get(InsuredPerson, insured_id)
    if person is None or person.deleted_at is not None:
        raise NotFound("Insured person not found")
    if (await clients_repo.get_by_id(session, person.client_id, scope)) is None:
        raise NotFound("Insured person not found")  # out-of-scope parent => same 404
    for field, value in payload.items():
        if value is None:
            continue
        if field in {"coverage_start", "coverage_end"}:
            value = _as_date(value)
        elif field == "mobile":
            value = normalize_mobile(value)
        elif field == "coverage_status":
            value = CoverageStatus(value)
        setattr(person, field, value)
    await session.flush()
    await audit.write(
        session, actor=actor, action="insured.updated", entity_type="insured_person", entity_id=person.id, after=dict(payload), ip=ip
    )
    return person


# --------------------------------------------------------------------------------------------
# Excel import (screen 11): upload → dry-run preview with per-row errors → confirm → report
# --------------------------------------------------------------------------------------------
async def parse_insured_workbook(content: bytes, filename: str) -> list[dict[str, Any]]:
    """Accepts only a real .xlsx workbook; anything else is a 422, never a 500."""
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover
        raise ValidationFailed("Excel support is not installed") from exc
    if not content:
        raise ValidationFailed("The file is empty", details={"filename": filename})
    if not content[:2] == b"PK":
        raise ValidationFailed(
            "الملف ليس مصنف Excel بصيغة .xlsx",
            details={"filename": filename, "hint": "Save the sheet as .xlsx and upload again"},
        )
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises a family of zip/xml errors depending on the damage
        raise ValidationFailed("تعذّر قراءة مصنف Excel", details={"filename": filename, "reason": type(exc).__name__}) from exc
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise ValidationFailed("The workbook is empty")
    header = [str(c).strip().lower() if c is not None else "" for c in rows[0]]
    mapping: dict[str, int] = {}
    for key, aliases in _HEADERS.items():
        for idx, cell in enumerate(header):
            if cell in aliases:
                mapping[key] = idx
                break
    if "name" not in mapping:
        raise ValidationFailed("Column 'name' (الاسم) is required", details={"header": header, "mapped": sorted(mapping)})
    parsed: list[dict[str, Any]] = []
    for line_no, raw in enumerate(rows[1:], start=2):
        if raw is None or all(c is None or str(c).strip() == "" for c in raw):
            continue
        item: dict[str, Any] = {"_row": line_no}
        for key, idx in mapping.items():
            item[key] = raw[idx] if idx < len(raw) else None
        parsed.append(item)
    _ = filename
    return parsed


async def import_insured_dry_run(
    session: AsyncSession, actor: User, scope: Scope, client_id: UUID, *, rows: list[dict[str, Any]], filename: str | None
) -> ImportJob:
    client = await get(session, client_id, scope)
    existing_mobiles = set(
        (
            await session.execute(
                select(InsuredPerson.mobile).where(
                    InsuredPerson.client_id == client.id,
                    InsuredPerson.deleted_at.is_(None),
                    InsuredPerson.mobile.is_not(None),
                )
            )
        ).scalars()
    )
    prepared: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        row_no = row.get("_row")
        name = str(row.get("name")).strip() if row.get("name") else ""
        mobile = normalize_mobile(str(row.get("mobile")) if row.get("mobile") is not None else None)
        record: dict[str, Any] = {k: v for k, v in row.items() if not str(k).startswith("_")}
        record["mobile"] = mobile
        record["coverage_start"] = _iso(_as_date(row.get("coverage_start")))
        record["coverage_end"] = _iso(_as_date(row.get("coverage_end")))
        if not name:
            errors.append({"row": row_no, "field": "name", "message": "missing required name"})
            continue
        if row.get("mobile") not in (None, "") and mobile is None:
            errors.append({"row": row_no, "field": "mobile", "message": "not a valid Egyptian mobile number"})
            continue
        if mobile and mobile in seen:
            errors.append({"row": row_no, "field": "mobile", "message": "duplicated inside the file"})
            continue
        if mobile and mobile in existing_mobiles:
            record["_action"] = "update"
        else:
            record["_action"] = "insert"
        seen.add(mobile or name)
        prepared.append(record)

    job = ImportJob(
        client_id=client.id,
        type=ImportJobType.INSURED_PERSONS,
        status=ImportJobStatus.DRY_RUN,
        filename=filename,
        total_rows=len(rows),
        stats={
            "will_create": sum(1 for r in prepared if r["_action"] == "insert"),
            "will_update": sum(1 for r in prepared if r["_action"] == "update"),
            "rejected": len(errors),
        },
        error_report=errors,
        rows_payload=prepared,
        created_by=actor.id,
    )
    session.add(job)
    await session.flush()
    return job


async def commit_import(session: AsyncSession, actor: User, scope: Scope, job_id: UUID, *, ip: str | None = None) -> ImportJob:
    job = await session.get(ImportJob, job_id)
    if job is None:
        raise NotFound("Import job not found")
    if job.status is not ImportJobStatus.DRY_RUN:
        raise Conflict(f"Import already {job.status.value}")
    if (await clients_repo.get_by_id(session, job.client_id, scope)) is None:
        raise NotFound("Import job not found")

    job.status = ImportJobStatus.RUNNING
    await session.flush()
    created = updated = 0
    for record in job.rows_payload:
        payload = {k: v for k, v in record.items() if not k.startswith("_") and v is not None}
        payload["coverage_start"] = _as_date(record.get("coverage_start"))
        payload["coverage_end"] = _as_date(record.get("coverage_end"))
        if record.get("_action") == "update" and record.get("mobile"):
            person = (
                await session.execute(
                    select(InsuredPerson).where(
                        InsuredPerson.client_id == job.client_id,
                        InsuredPerson.mobile == record["mobile"],
                        InsuredPerson.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if person is not None:
                for field, value in payload.items():
                    if field != "mobile" and value is not None:
                        setattr(person, field, value)
                person.coverage_status = CoverageStatus.ACTIVE
                updated += 1
                continue
        session.add(InsuredPerson(client_id=job.client_id, coverage_status=CoverageStatus.ACTIVE, **payload))
        created += 1
    job.status = ImportJobStatus.COMPLETED
    job.stats = {**job.stats, "created": created, "updated": updated}
    await session.flush()
    await audit.write(
        session,
        actor=actor,
        action="insured.import_committed",
        entity_type="import_job",
        entity_id=job.id,
        after={"created": created, "updated": updated, "client_id": str(job.client_id)},
        ip=ip,
    )
    return job


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None
