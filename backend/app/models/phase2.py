"""Phase 2 tables: policies, installments, commissions, issuing cases (built early, flag-gated)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import (
    CommissionStatus,
    InstallmentStatus,
    IssuingDocumentStatus,
    IssuingStage,
    PaymentPlan,
    PolicyStatus,
)
from app.models.base import Base, TimestampMixin, TZDateTime, UUIDPkMixin, enum_col

if TYPE_CHECKING:
    from app.models.clients import Client
    from app.models.leads import Lead
    from app.models.masters import DocumentRequirement, InsuranceType, Insurer


class Policy(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "policies"
    __table_args__ = (
        sa.Index("ix_policies_client", "client_id"),
        sa.Index("ix_policies_end", "end_date"),
        sa.Index("ix_policies_status", "status"),
    )

    client_id: Mapped[UUID] = mapped_column(sa.Uuid(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    insurer_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("insurers.id"))
    insurance_type_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("insurance_types.id"))
    policy_no: Mapped[str | None] = mapped_column(sa.String(64))
    start_date: Mapped[date | None] = mapped_column(sa.Date)
    end_date: Mapped[date | None] = mapped_column(sa.Date)
    premium_amount: Mapped[Decimal | None] = mapped_column(sa.Numeric(14, 2))
    currency: Mapped[str] = mapped_column(sa.String(8), default="EGP", nullable=False)
    payment_plan: Mapped[PaymentPlan] = mapped_column(enum_col(PaymentPlan), default=PaymentPlan.MONTHLY, nullable=False)
    commission_rate: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 2), default=Decimal("5.00"))
    status: Mapped[PolicyStatus] = mapped_column(enum_col(PolicyStatus), default=PolicyStatus.DRAFT, nullable=False)
    #: when the office actually issued it (R10). `created_at` is when the draft was booked, which is a
    #: different month whenever a policy sits in `pending_insurer` across a month boundary — and the
    #: executive dashboard counts *issued this month*.
    issued_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    headcount: Mapped[int | None] = mapped_column(sa.Integer)
    notes: Mapped[str | None] = mapped_column(sa.Text)

    insurer: Mapped[Insurer | None] = relationship(lazy="joined")
    insurance_type: Mapped[InsuranceType | None] = relationship(lazy="joined")
    #: the row the office actually recognises ("شركة النيل للأدوية"), not a UUID in a column. `joined` so the
    #: list costs one query: screen 12 printed an em dash in the client column for every policy (R7).
    client: Mapped[Client | None] = relationship(lazy="joined")
    documents: Mapped[list[PolicyDocument]] = relationship(
        back_populates="policy", lazy="selectin", cascade="all, delete-orphan", order_by="PolicyDocument.created_at.desc()"
    )


class PolicyDocument(UUIDPkMixin, TimestampMixin, Base):
    """The rendered file of one issued policy (ADR-021).

    A separate table rather than a column on `policies`: issuing twice is a conflict, but re-rendering a
    lost file must stay possible, and the old bytes are what a client may already hold. Keeping the row
    also keeps `policies` readable without the storage layer — the list screen never touches a file.
    """

    __tablename__ = "policy_documents"
    __table_args__ = (sa.Index("ix_policy_documents_policy", "policy_id"),)

    policy_id: Mapped[UUID] = mapped_column(sa.Uuid(), sa.ForeignKey("policies.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(sa.String(512), nullable=False)
    mime: Mapped[str] = mapped_column(sa.String(128), default="application/pdf", nullable=False)
    size: Mapped[int] = mapped_column(sa.BigInteger, default=0, nullable=False)
    checksum: Mapped[str | None] = mapped_column(sa.String(64))
    issued_by: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"))
    #: the number printed on the page; kept on the row so a re-render after a number change is explainable
    policy_no: Mapped[str] = mapped_column(sa.String(64), nullable=False)

    policy: Mapped[Policy] = relationship(back_populates="documents", lazy="joined")


class Installment(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "installments"
    __table_args__ = (sa.Index("ix_installments_policy_due", "policy_id", "due_date"),)

    policy_id: Mapped[UUID] = mapped_column(sa.Uuid(), sa.ForeignKey("policies.id", ondelete="CASCADE"), nullable=False)
    sequence_no: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    due_date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(sa.Numeric(14, 2), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    status: Mapped[InstallmentStatus] = mapped_column(enum_col(InstallmentStatus), default=InstallmentStatus.UPCOMING, nullable=False)

    policy: Mapped[Policy] = relationship(backref=sa.orm.backref("installments", lazy="selectin"))


class Commission(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "commissions"
    __table_args__ = (sa.Index("ix_commissions_policy", "policy_id"),)

    policy_id: Mapped[UUID] = mapped_column(sa.Uuid(), sa.ForeignKey("policies.id", ondelete="CASCADE"), nullable=False)
    installment_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("installments.id", ondelete="SET NULL"))
    amount: Mapped[Decimal] = mapped_column(sa.Numeric(14, 2), nullable=False)
    status: Mapped[CommissionStatus] = mapped_column(enum_col(CommissionStatus), default=CommissionStatus.ACCRUED, nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(TZDateTime())


class IssuingCase(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "issuing_cases"
    __table_args__ = (sa.Index("ix_issuing_stage", "stage"), sa.Index("ix_issuing_client", "client_id"))

    client_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("clients.id", ondelete="CASCADE"))
    lead_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("leads.id", ondelete="SET NULL"))
    insurer_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("insurers.id"))
    insurance_type_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("insurance_types.id"))
    stage: Mapped[IssuingStage] = mapped_column(enum_col(IssuingStage), default=IssuingStage.AGREEMENT, nullable=False)
    assigned_to: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id"))
    headcount: Mapped[int | None] = mapped_column(sa.Integer)
    premium_amount: Mapped[Decimal | None] = mapped_column(sa.Numeric(14, 2))
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    issued_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    delivered_at: Mapped[datetime | None] = mapped_column(TZDateTime())

    documents: Mapped[list[IssuingDocument]] = relationship(back_populates="case", lazy="selectin", cascade="all, delete-orphan")
    insurer: Mapped[Insurer | None] = relationship(lazy="joined")
    client: Mapped[Client | None] = relationship(lazy="joined")
    lead: Mapped[Lead | None] = relationship(lazy="joined")


class IssuingDocument(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "issuing_documents"
    __table_args__ = (sa.Index("ix_issuing_docs_case", "case_id"),)

    case_id: Mapped[UUID] = mapped_column(sa.Uuid(), sa.ForeignKey("issuing_cases.id", ondelete="CASCADE"), nullable=False)
    requirement_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("document_requirements.id"))
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    status: Mapped[IssuingDocumentStatus] = mapped_column(
        enum_col(IssuingDocumentStatus), default=IssuingDocumentStatus.MISSING, nullable=False
    )
    attachment_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("attachments.id", ondelete="SET NULL"))
    updated_by: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id"))

    case: Mapped[IssuingCase] = relationship(back_populates="documents")
    requirement: Mapped[DocumentRequirement | None] = relationship(lazy="joined")
