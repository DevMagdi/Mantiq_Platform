"""AI service (spec M10): flag-gated, scope-respecting, never authoritative.

Three rules hold this feature together, and each one exists because of a concrete failure mode:

1. **Flag first.** `ai.enabled` is read from the database on every call, so switching the provider
   off in production stops these endpoints without a redeploy. `GET /ai/status` is the only route
   that answers without the flag — the UI must be able to learn that the feature is off.
2. **Scope by the parent.** A call or a thread is never fetched directly into a response; the
   request that owns it goes through `requests_svc.get`, which applies the data scope. So an AI call
   can never widen what the caller is allowed to read.
3. **Nothing is applied automatically.** The transcript and the summary are written where a human
   reads them (a `call_records` transcript and an internal note) and are labelled as machine-made in
   the note body itself. There is no endpoint that changes a status, an SLA or a message from an AI
   answer — the model cannot act, only suggest.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select

from app.integrations.registry import ai_provider
from app.models.requests import CallRecord
from app.services import audit
from app.services.feature_flags import AI_FLAG, flag_enabled

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.scope import Scope
    from app.models.auth import User

NOTE_MARKER = "ملخّص آلي (مزوّد: {provider}) — يحتاج مراجعة موظف قبل أي إجراء:"


async def ai_available(session: AsyncSession) -> bool:
    return await flag_enabled(session, AI_FLAG)


async def _call_in_scope(session: AsyncSession, scope: Scope, call_id: UUID) -> CallRecord:
    """404 for a call the caller may not see — same rule as `recording_url`."""
    record = (await session.execute(select(CallRecord).where(CallRecord.id == call_id))).scalar_one_or_none()
    if record is None:
        from app.core.errors import NotFound

        raise NotFound("Call not found")
    from app.services import requests_svc

    if record.request_id is not None:
        await requests_svc.get(session, record.request_id, scope)
    elif record.lead_id is not None:
        from app.repositories import leads as leads_repo

        if await leads_repo.get_by_id(session, record.lead_id, scope) is None:
            from app.core.errors import NotFound

            raise NotFound("Call not found")
    else:
        from app.core.errors import NotFound

        raise NotFound("Call not found")
    return record


async def transcribe_call(
    session: AsyncSession, actor: User, scope: Scope, call_id: UUID, *, force: bool = False, ip: str | None = None
) -> dict[str, Any]:
    """Write a transcript + a short summary onto the call record. Consent is mandatory."""
    from app.core.errors import Conflict, Forbidden, NotFound

    if not scope.can("calls.listen"):
        raise Forbidden("Not allowed to read call recordings")
    record = await _call_in_scope(session, scope, call_id)
    if not record.recording_object_key:
        raise NotFound("Recording is not available yet")
    if not record.recording_consent:
        raise Forbidden("Recording was stored without consent, so it may not be transcribed")
    if record.transcript and not force:
        raise Conflict("This call already has a transcript; pass force=true to overwrite it")

    provider = ai_provider()
    transcript = await provider.transcribe(record.recording_object_key)
    record.transcript = transcript
    record.summary = await provider.summarize(transcript)
    await session.flush()
    await audit.write(
        session,
        actor=actor,
        action="ai.transcribe",
        entity_type="call_record",
        entity_id=record.id,
        after={"provider": provider.name, "chars": len(transcript), "requires_review": True},
        ip=ip,
    )
    return {
        "call_id": str(record.id),
        "provider": provider.name,
        "transcript": transcript,
        "summary": record.summary,
        "requires_review": True,
    }


async def summarize_request(session: AsyncSession, actor: User, scope: Scope, request_id: UUID, *, ip: str | None = None) -> dict[str, Any]:
    """Summarise the thread into an internal note (never into a client-visible message)."""
    from app.core.errors import ValidationFailed
    from app.models.requests import InternalNote
    from app.repositories import requests_repo as repo
    from app.services import requests_svc

    request = await requests_svc.get(session, request_id, scope)
    messages = await repo.messages(session, request.id)
    notes = await repo.notes(session, request.id)
    corpus = "\n".join(
        [f"{request.subject} — حالة: {request.status.value}"]
        + [f"{'العميل' if m.direction.value == 'inbound' else 'الموظف'}: {m.body}" for m in messages]
        + [f"ملاحظة داخلية: {n.body}" for n in notes]
    )
    if not corpus.strip():
        raise ValidationFailed("Nothing to summarise yet")

    provider = ai_provider()
    summary = await provider.summarize(corpus)
    marker = NOTE_MARKER.format(provider=provider.name)
    body = f"{marker}\n{summary or '(لا يوجد نص كافٍ للتُلخيص)'}"
    note = InternalNote(request_id=request.id, author_id=actor.id, body=body)
    session.add(note)
    await session.flush()
    await audit.write(
        session,
        actor=actor,
        action="ai.summarize",
        entity_type="request",
        entity_id=request.id,
        after={"provider": provider.name, "chars": len(summary), "note_id": str(note.id)},
        ip=ip,
    )
    return {
        "request_id": str(request.id),
        "provider": provider.name,
        "summary": summary,
        "note_id": str(note.id),
        "requires_review": True,
    }


async def classify(text: str) -> dict[str, Any]:
    """Suggested triage only: nothing is written, nothing is re-triaged automatically."""
    provider = ai_provider()
    result: dict[str, Any] = await provider.classify(text)
    return result


def status_payload(enabled: bool) -> dict[str, Any]:
    provider = ai_provider()
    return {
        "enabled": enabled,
        "driver": provider.name,
        "capabilities": ["transcribe", "summarize", "classify"] if enabled else [],
        "note": "المخرجات مقترحة فقط وتُحفظ كملاحظة داخلية بحاجة لمراجعة",
    }
