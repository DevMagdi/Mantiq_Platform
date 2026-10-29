"""Declarative base + mixins. UUIDs are generated in Python and enums are stored as VARCHAR
so the same schema runs on PostgreSQL (prod/CI) and SQLite (laptop dev without Docker)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import DateTime, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class TZDateTime(sa.TypeDecorator[datetime]):
    """Always tz-aware UTC in and out.

    PostgreSQL stores `timestamptz`, SQLite does not: without this a `next_followup_at` read back
    on SQLite is naive and every "is it overdue" comparison raises. One decorator, both dialects.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class Base(DeclarativeBase):
    type_annotation_map = {
        UUID: Uuid(as_uuid=True),
        datetime: TZDateTime(),
    }


def enum_col(enum_cls: type[StrEnum], *, name: str | None = None, nullable: bool = False) -> Any:
    return sa.Enum(
        enum_cls,
        native_enum=False,
        validate_strings=True,
        create_constraint=True,
        length=40,
        values_callable=lambda e: [m.value for m in e],
        name=name or f"{enum_cls.__name__.lower()}_v",
        nullable=nullable,
    )


class UUIDPkMixin:
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, onupdate=utcnow, nullable=False)


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(TZDateTime(), default=None)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None
