"""Clients (converted leads or directly created), their contacts and insured employees."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import (
    ClientStatus,
    ClientType,
    CoverageStatus,
    ImportJobStatus,
    ImportJobType,
)
from app.models.base import Base, SoftDeleteMixin, TimestampMixin, TZDateTime, UUIDPkMixin, enum_col

if TYPE_CHECKING:
    from app.models.auth import User
    from app.models.leads import Lead


class Client(UUIDPkMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "clients"
    __table_args__ = (
        sa.Index("ix_clients_owner", "account_owner_id"),
        sa.Index("ix_clients_status", "status"),
        sa.Index("ix_clients_name", "name"),
    )

    type: Mapped[ClientType] = mapped_column(enum_col(ClientType), default=ClientType.COMPANY, nullable=False)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    status: Mapped[ClientStatus] = mapped_column(enum_col(ClientStatus), default=ClientStatus.ACTIVE, nullable=False)
    account_owner_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id"))
    source_lead_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("leads.id"))
    converted_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    since_date: Mapped[date | None] = mapped_column(sa.Date)
    tax_number: Mapped[str | None] = mapped_column(sa.String(32))
    address: Mapped[str | None] = mapped_column(sa.Text)
    notes: Mapped[str | None] = mapped_column(sa.Text)

    account_owner: Mapped[User | None] = relationship(lazy="joined")
    source_lead: Mapped[Lead | None] = relationship(foreign_keys=[source_lead_id], lazy="selectin")
    contacts: Mapped[list[ClientContact]] = relationship(back_populates="client", lazy="selectin", cascade="all, delete-orphan")

    @property
    def is_converted_from_lead(self) -> bool:
        return self.source_lead_id is not None


class ClientContact(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "client_contacts"
    __table_args__ = (sa.Index("ix_client_contacts_client", "client_id"), sa.Index("ix_client_contacts_mobile", "mobile"))

    client_id: Mapped[UUID] = mapped_column(sa.Uuid(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    title: Mapped[str | None] = mapped_column(sa.String(120))
    mobile: Mapped[str | None] = mapped_column(sa.String(20))
    email: Mapped[str | None] = mapped_column(sa.String(255))
    is_primary: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)

    client: Mapped[Client] = relationship(back_populates="contacts")


class InsuredPerson(UUIDPkMixin, TimestampMixin, SoftDeleteMixin, Base):
    """Employees covered under a client's group policy (screen 11)."""

    __tablename__ = "insured_persons"
    __table_args__ = (
        sa.Index("ix_insured_client_mobile", "client_id", "mobile"),
        sa.Index("ix_insured_mobile", "mobile"),
        sa.Index("ix_insured_client_status", "client_id", "coverage_status"),
    )

    client_id: Mapped[UUID] = mapped_column(sa.Uuid(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    mobile: Mapped[str | None] = mapped_column(sa.String(20))
    national_id: Mapped[str | None] = mapped_column(sa.String(20))
    department: Mapped[str | None] = mapped_column(sa.String(120))
    job_title: Mapped[str | None] = mapped_column(sa.String(120))
    coverage_status: Mapped[CoverageStatus] = mapped_column(enum_col(CoverageStatus), default=CoverageStatus.ACTIVE, nullable=False)
    coverage_start: Mapped[date | None] = mapped_column(sa.Date)
    coverage_end: Mapped[date | None] = mapped_column(sa.Date)
    external_ref: Mapped[str | None] = mapped_column(sa.String(64))
    notes: Mapped[str | None] = mapped_column(sa.Text)

    client: Mapped[Client] = relationship(lazy="joined")

    @classmethod
    def effective_status(cls) -> Any:
        """Coverage status derived from dates when they exist (used by the import + lists)."""
        today = func.date(func.now())
        return sa.case(
            (cls.coverage_end.is_not(None) & (cls.coverage_end < today), CoverageStatus.ENDED.value),
            (cls.coverage_start.is_not(None) & (cls.coverage_start > today), CoverageStatus.PENDING.value),
            else_=cls.coverage_status,
        )


class ImportJob(UUIDPkMixin, TimestampMixin, Base):
    """Excel import of insured persons: dry-run first, then commit (screen 11)."""

    __tablename__ = "import_jobs"
    __table_args__ = (sa.Index("ix_import_jobs_client", "client_id"),)

    client_id: Mapped[UUID] = mapped_column(sa.Uuid(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[ImportJobType] = mapped_column(enum_col(ImportJobType), default=ImportJobType.INSURED_PERSONS, nullable=False)
    status: Mapped[ImportJobStatus] = mapped_column(enum_col(ImportJobStatus), default=ImportJobStatus.DRY_RUN, nullable=False)
    filename: Mapped[str | None] = mapped_column(sa.String(255))
    total_rows: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    stats: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict, nullable=False)
    error_report_key: Mapped[str | None] = mapped_column(sa.String(255))
    error_report: Mapped[list[dict[str, Any]]] = mapped_column(sa.JSON, default=list, nullable=False)
    rows_payload: Mapped[list[dict[str, Any]]] = mapped_column(sa.JSON, default=list, nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id"))
