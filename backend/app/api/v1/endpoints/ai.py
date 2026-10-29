"""AI routes (spec M10): transcription, summarisation, triage suggestion.

Every route except `/ai/status` returns 503 while `ai.enabled` is off, requires the same permission as
the human action it mirrors (`calls.listen` / `requests.manage`) and is limited to the caller's data
scope. Outputs are suggestions: they are stored as an internal note and flagged for review.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter

from app.api.v1.deps import CallsListen, DbSession, RequestsManage, ScopeDep
from app.core.errors import FeatureDisabled
from app.schemas.ai import AIClassifyIn, AIClassifyOut, AIStatusOut, AISummarizeOut, AITranscribeOut
from app.services import ai_svc
from app.services.feature_flags import AI_FLAG

router = APIRouter(tags=["ai (phase 2)"])


async def _require_enabled(session: Any) -> None:
    if not await ai_svc.ai_available(session):
        raise FeatureDisabled("AI provider is disabled for this deployment", details={"flag": AI_FLAG})


@router.get("/ai/status", response_model=AIStatusOut, summary="Whether the AI features are on for this deployment")
async def status(session: DbSession) -> dict[str, Any]:
    return ai_svc.status_payload(await ai_svc.ai_available(session))


@router.post("/ai/calls/{call_id}/transcribe", response_model=AITranscribeOut)
async def transcribe(call_id: UUID, session: DbSession, scope: ScopeDep, actor: CallsListen, force: bool = False) -> dict[str, Any]:
    from app.middleware_ip import ip_of

    await _require_enabled(session)
    data = await ai_svc.transcribe_call(session, actor, scope, call_id, force=force, ip=ip_of())
    await session.commit()
    return data


@router.post("/ai/requests/{request_id}/summarize", response_model=AISummarizeOut)
async def summarize(request_id: UUID, session: DbSession, scope: ScopeDep, actor: RequestsManage) -> dict[str, Any]:
    from app.middleware_ip import ip_of

    await _require_enabled(session)
    data = await ai_svc.summarize_request(session, actor, scope, request_id, ip=ip_of())
    await session.commit()
    return data


@router.post("/ai/classify", response_model=AIClassifyOut, summary="Suggested triage for free text (nothing is stored)")
async def classify(body: AIClassifyIn, session: DbSession) -> dict[str, Any]:
    await _require_enabled(session)
    return await ai_svc.classify(body.text)
