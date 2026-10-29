from __future__ import annotations

import hashlib
import mimetypes
import uuid
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, File, Query, UploadFile

from app.api.serializers import request_brief, request_detail
from app.api.v1.deps import (
    CallsListen,
    DbSession,
    PageQuery,
    RequestsManage,
    RequestsView,
    ScopeDep,
)
from app.core.errors import Forbidden, NotFound, ValidationFailed
from app.core.pagination import Page
from app.domain.enums import Channel, RequestStatus
from app.integrations.storage import storage
from app.models.requests import Attachment
from app.repositories import requests_repo as repo
from app.schemas.out import CallOut, RecordingUrlOut, RequestDetailOut, RequestMessageOut, RequestNoteOut, RequestPageOut
from app.schemas.requests import NoteCreate, RequestAssign, RequestCreate, RequestMessageCreate, RequestStatusChange, RequestUpdate
from app.services import requests_svc

router = APIRouter(tags=["requests (service desk)"])

_ALLOWED_MIME = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/heic",
    "application/pdf",
    "audio/ogg",
    "audio/mpeg",
    "audio/mp4",
    "audio/wav",
    "audio/x-wav",
    "audio/webm",
    "text/plain",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_MAX_BYTES = 12 * 1024 * 1024


_StatusIn = RequestStatusChange
_AssignIn = RequestAssign
_NoteIn = NoteCreate


@router.get("/requests", summary="Unified inbox list", response_model=RequestPageOut)
async def list_requests(
    session: DbSession,
    scope: ScopeDep,
    params: PageQuery,
    actor: RequestsView,
    status: Annotated[list[RequestStatus] | None, Query(description="repeatable status filter")] = None,
    channel: Channel | None = None,
    client_id: UUID | None = None,
    assigned_to: UUID | None = None,
    only_open: bool = False,
    only_overdue: bool = False,
    q: str | None = None,
) -> Page[Any]:
    items, total = await repo.list_page(
        session,
        scope,
        statuses=list(status) if status else None,
        channel=channel.value if channel else None,
        client_id=client_id,
        assigned_to=assigned_to,
        only_open=only_open,
        only_overdue=only_overdue,
        q=q,
        params=params,
    )
    latest_by_request = await repo.latest_messages(session, [row.id for row in items])
    briefs = [
        request_brief(row, first_message=_snippet([latest_by_request[row.id]] if row.id in latest_by_request else [])) for row in items
    ]
    return Page.build(briefs, total, params)  # type: ignore[arg-type]


@router.post("/requests", status_code=201, summary="Log a request manually (dashboard '+')")
async def create_request(body: RequestCreate, session: DbSession, scope: ScopeDep, actor: RequestsManage) -> dict[str, Any]:
    from app.middleware_ip import ip_of

    request = await requests_svc.create(session, actor, scope, body, ip=ip_of())
    await session.commit()
    return request_brief(request)


@router.get("/requests/{request_id}", response_model=RequestDetailOut)
async def get_request(request_id: UUID, session: DbSession, scope: ScopeDep, actor: RequestsView) -> dict[str, Any]:
    request = await requests_svc.get(session, request_id, scope)
    return request_detail(
        request,
        messages=await repo.messages(session, request.id),
        notes=[] if scope.is_portal else await repo.notes(session, request.id),
        calls=await repo.calls(session, request.id),
        scope=scope,
        attachments=await repo.attachments(session, request.id),
    )


@router.patch("/requests/{request_id}")
async def patch_request(
    request_id: UUID, body: RequestUpdate, session: DbSession, scope: ScopeDep, actor: RequestsManage
) -> dict[str, Any]:
    request = await requests_svc.update(session, actor, scope, request_id, body)
    await session.commit()
    return request_brief(request)


@router.post("/requests/{request_id}/status")
async def change_status(request_id: UUID, body: _StatusIn, session: DbSession, scope: ScopeDep, actor: RequestsManage) -> dict[str, Any]:
    from app.middleware_ip import ip_of

    request = await requests_svc.change_status(
        session, actor, scope, request_id, status=RequestStatus(body.status), note=body.note, ip=ip_of()
    )
    await session.commit()
    return request_brief(request)


@router.post("/requests/{request_id}/assign")
async def assign(request_id: UUID, body: _AssignIn, session: DbSession, scope: ScopeDep, actor: RequestsManage) -> dict[str, Any]:
    request = await requests_svc.assign(session, actor, scope, request_id, to_user_id=body.assigned_to)
    await session.commit()
    return request_brief(request)


@router.get("/requests/{request_id}/messages", response_model=list[RequestMessageOut])
async def get_messages(request_id: UUID, session: DbSession, scope: ScopeDep, actor: RequestsView) -> list[dict[str, Any]]:
    request = await requests_svc.get(session, request_id, scope)
    rows = await repo.messages(session, request.id)
    # Attachments belong to a message (owner_id = message id) and to the request (request_id):
    # one query for the whole thread, grouped in memory — never one query per message.
    from sqlalchemy import select

    att_rows = (
        (await session.execute(select(Attachment).where(Attachment.request_id == request.id).order_by(Attachment.created_at)))
        .scalars()
        .all()
    )
    by_message: dict[str, list[dict[str, Any]]] = {}
    for att in att_rows:
        by_message.setdefault(str(att.owner_id), []).append(_attachment_brief(att))
    return [
        {
            "id": str(m.id),
            "direction": m.direction.value,
            "channel": m.channel.value,
            "body": m.body,
            "media_kind": m.media_kind,
            "duration_s": m.duration_s,
            "waveform": m.waveform,
            "sender_ref": m.sender_ref,
            "external_id": m.external_id,
            "delivery_status": m.delivery_status.value,
            "created_at": m.created_at.isoformat(),
            "attachments": by_message.get(str(m.id), []),
        }
        for m in rows
    ]


def _attachment_brief(att: Attachment) -> dict[str, Any]:
    return {
        "id": str(att.id),
        "filename": att.filename,
        "mime": att.mime,
        "size": att.size,
        "created_at": att.created_at.isoformat() if att.created_at else None,
        "url": None,  # signed URLs are issued (and audited) by the download-url endpoint, never by a list
    }


@router.get("/requests/{request_id}/attachments", summary="All attachments on a request (thread + uploads)")
async def list_attachments(request_id: UUID, session: DbSession, scope: ScopeDep, actor: RequestsView) -> list[dict[str, Any]]:
    from sqlalchemy import select

    request = await requests_svc.get(session, request_id, scope)
    rows = (
        (await session.execute(select(Attachment).where(Attachment.request_id == request.id).order_by(Attachment.created_at)))
        .scalars()
        .all()
    )
    return [_attachment_brief(att) for att in rows]


@router.get(
    "/requests/{request_id}/attachments/{attachment_id}/download-url",
    summary="Signed, audited download/play URL for one attachment",
    response_model=RecordingUrlOut,
)
async def attachment_download_url(
    request_id: UUID, attachment_id: UUID, session: DbSession, scope: ScopeDep, actor: RequestsView
) -> dict[str, Any]:
    from sqlalchemy import select

    from app.core.config import get_settings
    from app.core.security import make_signed_media_url
    from app.middleware_ip import ip_of
    from app.services import audit

    request = await requests_svc.get(session, request_id, scope)
    att = (
        await session.execute(select(Attachment).where(Attachment.id == attachment_id, Attachment.request_id == request.id))
    ).scalar_one_or_none()
    if att is None:
        raise NotFound("Attachment not found")
    url, expires_at = make_signed_media_url(att.object_key, get_settings().RECORDING_SIGNED_URL_TTL_SECONDS)
    await audit.write(
        session,
        actor=actor,
        action="attachment.accessed",
        entity_type="attachment",
        entity_id=att.id,
        after={"request_id": str(request.id), "filename": att.filename, "mime": att.mime},
        ip=ip_of(),
    )
    await session.commit()
    from datetime import UTC, datetime

    return {"url": url, "expires_at": datetime.fromtimestamp(expires_at, UTC).isoformat(), "duration_s": None, "recording_consent": False}


@router.post("/requests/{request_id}/messages", status_code=201, summary="Reply (sends via the channel provider)")
async def reply(request_id: UUID, body: RequestMessageCreate, session: DbSession, scope: ScopeDep, actor: RequestsManage) -> dict[str, Any]:
    if scope.is_portal:
        raise Forbidden("The portal posts through /portal/requests")
    message = await requests_svc.add_outbound_message(
        session,
        actor,
        scope,
        request_id,
        body=body.body,
        channel=Channel(body.channel) if body.channel else None,
        attachment_ids=body.attachment_ids,
        ip=None,
    )
    await session.commit()
    return {
        "id": str(message.id),
        "direction": message.direction.value,
        "channel": message.channel.value,
        "body": message.body,
        "external_id": message.external_id,
        "delivery_status": message.delivery_status.value,
        "created_at": message.created_at.isoformat(),
    }


@router.get("/requests/{request_id}/notes", response_model=list[RequestNoteOut])
async def get_notes(request_id: UUID, session: DbSession, scope: ScopeDep, actor: RequestsView) -> list[dict[str, Any]]:
    if scope.is_portal:
        return []
    request = await requests_svc.get(session, request_id, scope)
    rows = await repo.notes(session, request.id)
    return [
        {
            "id": str(n.id),
            "body": n.body,
            "author_name": n.author.full_name if n.author else None,
            "created_at": n.created_at.isoformat(),
        }
        for n in rows
    ]


@router.post("/requests/{request_id}/notes", status_code=201)
async def add_note(request_id: UUID, body: _NoteIn, session: DbSession, scope: ScopeDep, actor: RequestsManage) -> dict[str, Any]:
    if scope.is_portal:
        raise Forbidden("Internal notes are staff-only")
    note = await requests_svc.add_note(session, actor, scope, request_id, body.body)
    await session.commit()
    return {"id": str(note.id), "created_at": note.created_at.isoformat()}


@router.get("/requests/{request_id}/calls", response_model=list[CallOut])
async def get_calls(request_id: UUID, session: DbSession, scope: ScopeDep, actor: RequestsView) -> list[dict[str, Any]]:
    from app.api.serializers import call_brief

    request = await requests_svc.get(session, request_id, scope)
    return [call_brief(c, scope=scope) for c in await repo.calls(session, request.id)]


@router.get("/calls/{call_id}/recording-url", response_model=RecordingUrlOut)
async def recording_url(call_id: UUID, session: DbSession, scope: ScopeDep, actor: CallsListen) -> dict[str, Any]:
    from app.middleware_ip import ip_of

    data = await requests_svc.recording_url(session, actor, scope, call_id, ip=ip_of())
    await session.commit()
    return data


@router.post("/requests/{request_id}/attachments", status_code=201, summary="Upload an attachment (size+type+extension checked)")
async def upload_attachment(
    request_id: UUID, session: DbSession, scope: ScopeDep, actor: RequestsManage, file: UploadFile = File(...)
) -> dict[str, Any]:
    request = await requests_svc.get(session, request_id, scope)
    content = await file.read()
    if len(content) > _MAX_BYTES:
        raise ValidationFailed("File is too large", details={"max_bytes": _MAX_BYTES, "received": len(content)})
    name = (file.filename or "attachment").rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in {"exe", "sh", "bat", "cmd", "js", "html", "svg", "php", "py"}:
        raise ValidationFailed("That file type is not allowed", details={"extension": ext})
    mime = file.content_type or "application/octet-stream"
    if mime not in _ALLOWED_MIME:
        raise ValidationFailed("Unsupported content type", details={"mime": mime, "allowed": sorted(_ALLOWED_MIME)})
    key = f"requests/{request.id}/{uuid.uuid4().hex}-{name}"
    storage().put_object(key, content, mime)
    row = Attachment(
        owner_type="request",
        owner_id=request.id,
        request_id=request.id,
        filename=name[:255],
        object_key=key,
        mime=mime,
        size=len(content),
        checksum=hashlib.sha256(content).hexdigest(),
        uploaded_by=actor.id,
    )
    session.add(row)
    await session.flush()
    await session.commit()
    return {
        "id": str(row.id),
        "filename": row.filename,
        "mime": row.mime,
        "size": row.size,
        "checksum": row.checksum,
        "created_at": row.created_at.isoformat(),
    }


@router.get("/media/{key:path}", summary="Stream a signed object (recording/attachment)")
async def media(key: str, expires: int, sig: str) -> Any:
    from fastapi.responses import Response

    from app.core.security import verify_signed_media_url

    if not verify_signed_media_url(key, expires, sig):
        raise Forbidden("Link expired or signature invalid")
    try:
        payload = storage().get_object(key)
    except (FileNotFoundError, ValueError) as exc:
        # `ValueError` is the storage driver refusing a key that escapes its root. A signed URL cannot
        # carry such a key (the signature covers the key), but a 500 on a media read is never the right
        # answer: the object is "not here", whatever the reason.
        raise NotFound("Object not found") from exc
    # The recording driver writes `.wav`; attestations and brochures are PDF/PNG. Hard-coding audio/wav
    # made a PDF download as an audio file (browsers refuse to preview it). Guess from the key instead.
    media_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
    return Response(content=payload, media_type=media_type, headers={"cache-control": "private, max-age=60"})


def _snippet(messages: list[Any]) -> str | None:
    for message in reversed(messages):
        if message.body:
            text = " ".join(message.body.split())
            return text[:120]
    return None
