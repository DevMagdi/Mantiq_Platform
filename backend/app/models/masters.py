"""Reference data: insurers, insurance types, document requirements, lost reasons, SLA + hours, flags."""

from __future__ import annotations

from datetime import time as time_t
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import RequestPriority
from app.models.base import Base, TimestampMixin, UUIDPkMixin, enum_col


class Insurer(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "insurers"

    name: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    code: Mapped[str] = mapped_column(sa.String(32), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    name_en: Mapped[str | None] = mapped_column(sa.String(160))


class InsuranceType(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "insurance_types"

    code: Mapped[str] = mapped_column(sa.String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    name_en: Mapped[str | None] = mapped_column(sa.String(80))
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)

    document_requirements: Mapped[list[DocumentRequirement]] = relationship(
        back_populates="insurance_type", lazy="selectin", cascade="all, delete-orphan"
    )


class DocumentRequirement(UUIDPkMixin, TimestampMixin, Base):
    """Per insurance-type issuing checklist (screen 13)."""

    __tablename__ = "document_requirements"
    __table_args__ = (sa.Index("ix_doc_req_type", "insurance_type_id"),)

    insurance_type_id: Mapped[UUID] = mapped_column(sa.Uuid(), sa.ForeignKey("insurance_types.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    is_required: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)

    insurance_type: Mapped[InsuranceType] = relationship(back_populates="document_requirements")


class LostReason(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "lost_reasons"

    name: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    sort_order: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)


class SlaPolicy(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "sla_policies"
    __table_args__ = (sa.UniqueConstraint("priority", name="uq_sla_priority"),)

    priority: Mapped[RequestPriority] = mapped_column(enum_col(RequestPriority), nullable=False)
    first_response_minutes: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    resolution_minutes: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    warn_before_minutes: Mapped[int] = mapped_column(sa.Integer, default=60, nullable=False)


class BusinessHoursRow(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "business_hours"
    __table_args__ = (sa.UniqueConstraint("weekday", name="uq_business_hours_weekday"),)

    weekday: Mapped[int] = mapped_column(sa.Integer, nullable=False)  # Mon=0 … Sun=6
    open_time: Mapped[time_t] = mapped_column(sa.Time, nullable=False)
    close_time: Mapped[time_t] = mapped_column(sa.Time, nullable=False)
    is_open: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)


class FeatureFlag(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "feature_flags"

    key: Mapped[str] = mapped_column(sa.String(64), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
