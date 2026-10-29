from __future__ import annotations

from fastapi import APIRouter, File, UploadFile

from app.core.deps import CurrentUser, DbSession, ScopeDep
from app.core.errors import Forbidden
from app.domain import permissions as P
from app.schemas.out import InsuranceTypeOut, InsurerOut, LostReasonOut, SlaPolicyOut
from app.services import masters

router = APIRouter(tags=["masters"])


@router.get("/insurers", summary="Insurers (loaded from app/data/insurers_eg.csv — see ADR-009)", response_model=list[InsurerOut])
async def insurers(session: DbSession, _scope: ScopeDep) -> list[dict[str, object]]:
    return await masters.insurers(session)


@router.post("/insurers/import-csv", summary="Upsert insurers from CSV (code,name[,name_en])")
async def import_insurers(session: DbSession, user: CurrentUser, file: UploadFile = File(...)) -> dict[str, int]:
    if P.SETTINGS_MANAGE not in user.permission_codes:
        raise Forbidden("Only admins can change master data")
    content = await file.read()
    if len(content) > 2_000_000:
        raise Forbidden("CSV too large")
    result = await masters.import_insurers_csv(session, content)
    await session.commit()
    return result


@router.get("/insurance-types", response_model=list[InsuranceTypeOut])
async def insurance_types(session: DbSession, _scope: ScopeDep) -> list[dict[str, object]]:
    return await masters.insurance_types(session)


@router.get("/lost-reasons", response_model=list[LostReasonOut])
async def lost_reasons(session: DbSession, _scope: ScopeDep) -> list[dict[str, object]]:
    return await masters.lost_reasons(session)


@router.get("/sla-policies", summary="SLA matrix per priority", response_model=list[SlaPolicyOut])
async def sla_policies(session: DbSession, _scope: ScopeDep) -> list[dict[str, object]]:
    return await masters.sla_policies(session)
