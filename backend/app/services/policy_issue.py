"""Policy issuance: the state move, the file, and the row that proves the file exists (ADR-021).

What "issue" means here, precisely, because three different things used to be called by that name:

* the **record** moves `draft|pending_insurer → issued` exactly once (`409` afterwards — an insurer does
  not want the same policy mailed twice, and a second `issued` row is how duplicates start);
* a **policy number** is assigned if the draft has none, because a document without one is not a document;
* the **PDF** is rendered, stored, and linked from `policy_documents`, in the same transaction as the
  status change. If rendering or storage fails the status does not move: a policy marked `issued` with no
  file is the worst of the three states, because every screen believes it is done.

The file is rendered from data the caller can already see (their own client's policy), so nothing here
widens the scope: `DocumentData` is built after the policy passed `row_visible`.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.scope import Scope
from app.domain.enums import PolicyStatus
from app.integrations.storage import storage
from app.models.auth import User
from app.models.clients import Client
from app.models.phase2 import Policy, PolicyDocument
from app.repositories.scope_filters import with_scope
from app.services import audit
from app.services.documents import PolicyDocumentData, render_policy_pdf

#: statuses a policy may be issued *from*. `expired`/`cancelled` are terminal, and `issued` is the target.
_ISSUABLE = {PolicyStatus.DRAFT, PolicyStatus.PENDING_INSURER}


async def _visible_policy(session: AsyncSession, scope: Scope, policy_id: Any) -> Policy:
    # `.unique()` because `Policy.insurer`/`insurance_type` are eager-joined: without it SQLAlchemy refuses
    # the single-row read, and the failure reads like a bug in the scope filter rather than in this line.
    stmt = with_scope(select(Policy).where(Policy.id == policy_id), scope, "policy").limit(1)
    rows = (await session.execute(stmt)).unique().scalars().all()
    if not rows:
        raise NotFound("Policy not found")  # a foreign policy is not a permission problem (spec §6)
    policy: Policy = rows[0]
    return policy


async def next_policy_no(session: AsyncSession, year: int) -> str:
    """`BH-<year>-NNNN`, derived from the numbers actually stored — no counter row to fall out of sync."""
    prefix = f"BH-{year}-"
    rows = (await session.execute(select(Policy.policy_no).where(Policy.policy_no.like(f"{prefix}%")))).scalars().all()
    highest = 0
    for value in rows:
        tail = str(value)[len(prefix) :]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return f"{prefix}{highest + 1:04d}"


def _broker_contact(client: Client) -> str | None:
    owner = client.account_owner
    if owner is None:
        return None
    # a bare `+` at the start of a phone number is a bidi-neutral character and lands at the wrong end of
    # the line on the rendered page, so the document carries the digits without it
    reachable = (owner.mobile or "").lstrip("+") or owner.email
    return f"{owner.full_name} · {reachable}" if reachable else owner.full_name


def _document_data(policy: Policy, client: Client, *, reference: str | None) -> PolicyDocumentData:
    settings = get_settings()
    return PolicyDocumentData(
        policy_no=str(policy.policy_no or ""),
        client_name=client.name,
        insurance_type=None if policy.insurance_type is None else policy.insurance_type.name,
        insurer=None if policy.insurer is None else policy.insurer.name,
        start_date=policy.start_date,
        end_date=policy.end_date,
        issue_date=date.today(),
        premium=policy.premium_amount,
        currency=policy.currency,
        headcount=policy.headcount,
        payment_plan=policy.payment_plan.value if policy.payment_plan else None,
        broker_name=settings.APP_NAME,
        # the contact line is the person who answers about this client, not the marketing URL: a policy
        # document is what the owner holds at renewal time, and "who do I call" is the question it answers
        broker_contact=_broker_contact(client),
        reference=reference,
    )


async def issue(
    session: AsyncSession,
    actor: User,
    scope: Scope,
    policy_id: Any,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Issue one policy: status, number, PDF, storage, `policy_documents` row, audit — one transaction."""
    policy = await _visible_policy(session, scope, policy_id)
    if policy.status == PolicyStatus.ISSUED:
        raise Conflict(
            "هذه الوثيقة صادرة بالفعل",
            details={"policy_no": policy.policy_no, "status": policy.status.value},
        )
    if policy.status not in _ISSUABLE:
        raise ValidationFailed(
            "لا يمكن إصدار وثيقة في هذه الحالة",
            details=[{"field": "status", "message": policy.status.value, "type": "invalid_transition"}],
        )
    client = await session.get(Client, policy.client_id)
    if client is None or client.deleted_at is not None:
        raise NotFound("Client not found")

    if not policy.policy_no:
        policy.policy_no = await next_policy_no(session, date.today().year)

    payload = render_policy_pdf(_document_data(policy, client, reference=None))
    key = f"policies/{policy.id}/{policy.policy_no}.pdf"
    stored = storage().put_object(key, payload, "application/pdf")

    document = PolicyDocument(
        policy_id=policy.id,
        filename=f"{policy.policy_no}.pdf",
        object_key=key,
        mime="application/pdf",
        size=stored.get("size", len(payload)),
        checksum=stored.get("checksum") or hashlib.sha256(payload).hexdigest(),
        issued_by=actor.id,
        policy_no=policy.policy_no,
    )
    session.add(document)

    before = {"status": policy.status.value, "policy_no": policy.policy_no}
    policy.status = PolicyStatus.ISSUED
    # R10: the executive dashboard counts "issued this month" from this column, not from `created_at` — a
    # draft that waited across a month boundary was not issued when it was booked (ADR-030 §3).
    policy.issued_at = datetime.now(UTC)
    await session.flush()
    await audit.write(
        session,
        actor=actor,
        action="policy.issued",
        entity_type="policy",
        entity_id=policy.id,
        before=before,
        after={"status": policy.status.value, "policy_no": policy.policy_no, "document_id": str(document.id)},
        ip=ip,
        user_agent=user_agent,
    )
    return {
        "id": str(policy.id),
        "status": policy.status.value,
        "policy_no": policy.policy_no,
        "issued_at": datetime.now(UTC).isoformat(),
        "document": {
            "id": str(document.id),
            "filename": document.filename,
            "size": document.size,
            "mime": document.mime,
            "checksum": document.checksum,
            "download_url": f"/api/v1/policies/{policy.id}/document",
        },
    }


async def latest_document(session: AsyncSession, scope: Scope, policy_id: Any) -> tuple[Policy, PolicyDocument]:
    """The most recent rendered file for a policy the caller may see, or a 404 that says which half failed."""
    policy = await _visible_policy(session, scope, policy_id)
    document = (
        await session.execute(
            select(PolicyDocument).where(PolicyDocument.policy_id == policy.id).order_by(PolicyDocument.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if document is None:
        raise NotFound("No document has been rendered for this policy yet")
    return policy, document


async def documents_for(session: AsyncSession, policy_ids: list[Any]) -> dict[str, PolicyDocument]:
    """Latest document per policy, for list screens. One query, not one per row."""
    if not policy_ids:
        return {}
    rows = (
        (
            await session.execute(
                select(PolicyDocument)
                .where(PolicyDocument.policy_id.in_(policy_ids))
                .order_by(PolicyDocument.policy_id, PolicyDocument.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    out: dict[str, PolicyDocument] = {}
    for row in rows:
        out.setdefault(str(row.policy_id), row)
    return out


async def issued_count(session: AsyncSession, scope: Scope) -> int:
    stmt = select(func.count(Policy.id)).where(Policy.status == PolicyStatus.ISSUED)
    return int((await session.execute(with_scope(stmt, scope, "policy"))).scalar_one())
