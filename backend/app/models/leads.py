"""Leads: pipeline row, activity timeline, meetings."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import ActivityType, ClientType, ImportJobStatus, LeadStatus
from app.models.base import Base, SoftDeleteMixin, TimestampMixin, TZDateTime, UUIDPkMixin, enum_col

if TYPE_CHECKING:
    from app.models.auth import User
    from app.models.clients import Client
    from app.models.masters import InsuranceType, Insurer, LostReason


class Lead(UUIDPkMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "leads"
    __table_args__ = (
        # Partial (active-only) uniqueness on mobile is created PG-only in a migration; a plain
        # index here keeps lookups fast everywhere.
        sa.Index("ix_leads_mobile", "mobile"),
        sa.Index("ix_leads_assigned_status", "assigned_to", "status"),
        sa.Index("ix_leads_followup", "next_followup_at"),
        sa.Index("ix_leads_status", "status"),
    )

    type: Mapped[ClientType] = mapped_column(enum_col(ClientType), default=ClientType.COMPANY, nullable=False)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    contact_name: Mapped[str | None] = mapped_column(sa.String(160))
    #: job title of `contact_name` at the company. It exists because the Excel import carries a
    #: "البوزيشن" column and there was no honest place for it: `contact_name` holds a person, not a title,
    #: and stuffing it into `needs_text` would make it unqueryable and invisible in the detail sheet.
    contact_title: Mapped[str | None] = mapped_column(sa.String(120))
    mobile: Mapped[str | None] = mapped_column(sa.String(20))  # E.164, normalized
    email: Mapped[str | None] = mapped_column(sa.String(255))
    source: Mapped[str | None] = mapped_column(sa.String(80))
    insurance_type_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("insurance_types.id"))
    headcount: Mapped[int | None] = mapped_column(sa.Integer)
    current_insurer_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("insurers.id"))
    current_policy_expiry: Mapped[date | None] = mapped_column(sa.Date)
    budget: Mapped[Decimal | None] = mapped_column(sa.Numeric(14, 2))
    needs_text: Mapped[str | None] = mapped_column(sa.Text)

    status: Mapped[LeadStatus] = mapped_column(enum_col(LeadStatus), default=LeadStatus.NEW, nullable=False)
    assigned_to: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id"))
    next_followup_at: Mapped[datetime | None] = mapped_column(TZDateTime())

    lost_reason_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("lost_reasons.id"))
    lost_note: Mapped[str | None] = mapped_column(sa.Text)
    converted_client_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("clients.id"))
    converted_by: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id"))
    converted_at: Mapped[datetime | None] = mapped_column(TZDateTime())

    created_by: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id"))

    assignee: Mapped[User | None] = relationship(foreign_keys=[assigned_to], lazy="joined")
    insurance_type: Mapped[InsuranceType | None] = relationship(lazy="joined")
    current_insurer: Mapped[Insurer | None] = relationship(lazy="joined")
    lost_reason: Mapped[LostReason | None] = relationship(lazy="joined")
    # Two independent many-to-one links between leads/clients (leads.converted_client_id and
    # clients.source_lead_id); they cannot be a writable back_populates pair, so each side stands alone.
    converted_client: Mapped[Client | None] = relationship(foreign_keys=[converted_client_id], lazy="selectin")
    activities: Mapped[list[LeadActivity]] = relationship(back_populates="lead", lazy="noload", cascade="all, delete-orphan")


class LeadActivity(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "lead_activities"
    __table_args__ = (sa.Index("ix_lead_activities_lead_time", "lead_id", "occurred_at"),)

    lead_id: Mapped[UUID] = mapped_column(sa.Uuid(), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[ActivityType] = mapped_column(enum_col(ActivityType), nullable=False)
    body: Mapped[str | None] = mapped_column(sa.Text)
    meta: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    actor_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id"))
    occurred_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)

    lead: Mapped[Lead] = relationship(back_populates="activities")
    actor: Mapped[User | None] = relationship(lazy="joined")


class LeadMeeting(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "lead_meetings"
    __table_args__ = (sa.Index("ix_lead_meetings_when", "scheduled_at"),)

    lead_id: Mapped[UUID] = mapped_column(sa.Uuid(), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    location: Mapped[str | None] = mapped_column(sa.String(200))
    outcome: Mapped[str | None] = mapped_column(sa.Text)
    created_by: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id"))

    lead: Mapped[Lead] = relationship()


class LeadImportJob(UUIDPkMixin, TimestampMixin, Base):
    """One Excel/CSV upload of leads, previewed before it is applied (dry-run → commit → report).

    A separate table from `import_jobs`, deliberately: that one is keyed by `client_id NOT NULL` and its
    commit path authorises through the *client* scope, which a pool of not-yet-owned leads has no part of.
    Reusing it would mean dropping a NOT NULL and branching every existing query on `client_id IS NULL` —
    so the *pattern* is copied (status/total_rows/stats/error_report/rows_payload, `import_job_brief`
    shape) and the table is not. The same `importjobstatus_v` check constraint keeps the states identical.

    `rows_payload` is the prepared rows — the only thing commit reads, so a row cannot change shape between
    the preview a manager approved and the insert that follows it.
    """

    __tablename__ = "lead_import_jobs"
    __table_args__ = (sa.Index("ix_lead_import_jobs_created", "created_by", "created_at"),)

    status: Mapped[ImportJobStatus] = mapped_column(enum_col(ImportJobStatus), default=ImportJobStatus.DRY_RUN, nullable=False)
    filename: Mapped[str | None] = mapped_column(sa.String(255))
    total_rows: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    #: {"rows": n, "ready": n, "errors": n, "skipped_duplicate": n, "warnings": n, "created": n, ...}
    stats: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict, nullable=False)
    #: every row the file got wrong or read differently: {row, field, message, kind: error|warning}
    error_report: Mapped[list[dict[str, Any]]] = mapped_column(sa.JSON, default=list, nullable=False)
    rows_payload: Mapped[list[dict[str, Any]]] = mapped_column(sa.JSON, default=list, nullable=False)
    #: who uploaded it, and who pressed commit — not always the same manager
    created_by: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"))
    committed_by: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"))
    committed_at: Mapped[datetime | None] = mapped_column(TZDateTime())
