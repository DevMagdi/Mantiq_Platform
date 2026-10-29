"""Audit trail writer. Rows are append-only (spec §5); there is deliberately no update/delete API."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.models.requests import AuditLog

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.auth import User


async def write(
    session: AsyncSession,
    *,
    actor: User | None,
    action: str,
    entity_type: str,
    entity_id: UUID | None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> AuditLog:
    row = AuditLog(
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=_jsonable(before),
        after=_jsonable(after),
        ip=ip,
        user_agent=(user_agent or "")[:255] or None,
        occurred_at=datetime.now(UTC),
    )
    session.add(row)
    return row


def snapshot(obj: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {name: _jsonable(getattr(obj, name, None)) for name in fields}


def _jsonable(value: Any) -> Any:
    """`before`/`after` land in a JSON column, so anything the encoder refuses is flattened here.

    Dates were the trigger: an insured edit carrying `coverage_end` used to 500 the audit write, and
    an audit failure must never take a mutation down with it.
    """
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(item) for item in value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
