"""The money tables of R10: one ledger, one settlement record per commission (ADR-030).

Two rules shape this file:

* **A posted entry is a fact, not a row.** Nothing here deletes: the API has no `DELETE`, voiding flips
  `status` to `void` and keeps the amount, the author and the reason. A reader that sums `status = posted`
  therefore gets the truth for free, and "لماذا رصيد الصندوق ناقص؟" is answerable a year later.
* **One payout per commission.** `commission_settlements.commission_id` is unique, so a double-click
  cannot pay a rep twice even if two requests race: the second insert loses at the database, not in a
  service that forgot to look.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import (
    LedgerDirection,
    LedgerEntryType,
    LedgerStatus,
    PaymentMethod,
    SettlementStatus,
)
from app.models.base import Base, TimestampMixin, TZDateTime, UUIDPkMixin, enum_col

if TYPE_CHECKING:
    from app.models.auth import User
    from app.models.clients import Client
    from app.models.phase2 import Commission, Installment, Policy


class LedgerEntry(UUIDPkMixin, TimestampMixin, Base):
    """One money movement. Append-only in the API; `void` is a state, never a row count change."""

    __tablename__ = "ledger_entries"
    __table_args__ = (
        sa.CheckConstraint("amount > 0", name="ck_ledger_amount_positive"),
        sa.Index("ix_ledger_posted_at", "posted_at"),
        sa.Index("ix_ledger_status_type", "status", "entry_type"),
        sa.Index("ix_ledger_client", "client_id"),
        sa.Index("ix_ledger_policy", "policy_id"),
        sa.Index("ix_ledger_search", "entry_type", "direction", "client_id"),
        # the guard against a second receipt for the same installment: SQLite and PostgreSQL both honour a
        # partial unique index, and voiding frees the slot because the voided row no longer matches it
        sa.Index(
            "uq_ledger_installment_posted",
            "entry_type",
            "installment_id",
            unique=True,
            sqlite_where=sa.text("status = 'posted'"),
            postgresql_where=sa.text("status = 'posted'"),
        ),
    )

    entry_no: Mapped[str] = mapped_column(sa.String(32), unique=True, nullable=False)
    entry_type: Mapped[LedgerEntryType] = mapped_column(enum_col(LedgerEntryType), nullable=False)
    direction: Mapped[LedgerDirection] = mapped_column(enum_col(LedgerDirection), nullable=False)
    amount: Mapped[Decimal] = mapped_column(sa.Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(8), default="EGP", nullable=False)
    status: Mapped[LedgerStatus] = mapped_column(enum_col(LedgerStatus), default=LedgerStatus.POSTED, nullable=False)

    client_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("clients.id", ondelete="SET NULL"))
    policy_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("policies.id", ondelete="SET NULL"))
    installment_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("installments.id", ondelete="SET NULL"))
    #: the rep the money belongs to (a commission payee, or whoever collected a receipt)
    user_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"))
    #: `cash | bank | wallet | other` — `None` where the row is a receivable that was never "received"
    method: Mapped[PaymentMethod | None] = mapped_column(enum_col(PaymentMethod, nullable=True))

    note: Mapped[str | None] = mapped_column(sa.Text)
    posted_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    posted_by: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"))

    voided_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    voided_by: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"))
    void_reason: Mapped[str | None] = mapped_column(sa.Text)

    client: Mapped[Client | None] = relationship(lazy="joined")
    policy: Mapped[Policy | None] = relationship(lazy="joined")
    user: Mapped[User | None] = relationship(foreign_keys=[user_id], lazy="joined")
    # `selectin` rather than `joined`: the list screen needs the sequence number and the author's name, but
    # three more LEFT JOINs on every scope-checked count would be paid for even when nobody prints them
    installment: Mapped[Installment | None] = relationship(lazy="selectin")
    posted_by_user: Mapped[User | None] = relationship(foreign_keys=[posted_by], lazy="selectin")
    voided_by_user: Mapped[User | None] = relationship(foreign_keys=[voided_by], lazy="selectin")


class CommissionSettlement(UUIDPkMixin, TimestampMixin, Base):
    """The payout lifecycle of one accrued commission: accrued → approved → paid (or cancelled).

    Kept apart from `commissions` on purpose: `CommissionsStatus` describes the *accrual* the policy
    wrote (accrued/settled/clawed_back) and is read by the policies screen, while this row is what the
    finance screen works with. Paying writes both — the settlement row for the audit trail, and
    `commissions.status = settled` so the older screens do not have to learn a new table (ADR-030 §4).
    """

    __tablename__ = "commission_settlements"
    __table_args__ = (
        sa.CheckConstraint("amount > 0", name="ck_settlement_amount_positive"),
        sa.Index("ix_settlements_status", "status"),
        sa.Index("ix_settlements_commission", "commission_id", unique=True),
    )

    commission_id: Mapped[UUID] = mapped_column(sa.Uuid(), sa.ForeignKey("commissions.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[SettlementStatus] = mapped_column(enum_col(SettlementStatus), default=SettlementStatus.ACCRUED, nullable=False)
    amount: Mapped[Decimal] = mapped_column(sa.Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(8), default="EGP", nullable=False)
    #: who is being paid: the policy owner (the rep who closed the client) unless finance names someone else
    payee_user_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"))

    approved_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    approved_by: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"))
    paid_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    paid_by: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"))
    cancelled_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    cancel_reason: Mapped[str | None] = mapped_column(sa.Text)
    #: the outgoing ledger row this payout produced — one payout, one movement, both directions auditable
    ledger_entry_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("ledger_entries.id", ondelete="SET NULL"))
    method: Mapped[PaymentMethod | None] = mapped_column(enum_col(PaymentMethod, nullable=True))
    note: Mapped[str | None] = mapped_column(sa.Text)

    commission: Mapped[Commission] = relationship(lazy="joined")
    payee: Mapped[User | None] = relationship(foreign_keys=[payee_user_id], lazy="joined")
    ledger_entry: Mapped[LedgerEntry | None] = relationship(foreign_keys=[ledger_entry_id], lazy="joined")


class CashboxOpening(UUIDPkMixin, TimestampMixin, Base):
    """The counted drawer at a moment in time (ADR-033). A control record, never a ledger movement."""

    __tablename__ = "cashbox_openings"
    __table_args__ = (
        sa.CheckConstraint("amount >= 0", name="ck_cashbox_opening_nonnegative"),
        sa.Index("ix_cashbox_openings_effective", "effective_at"),
    )

    amount: Mapped[Decimal] = mapped_column(sa.Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(8), default="EGP", nullable=False)
    effective_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    note: Mapped[str | None] = mapped_column(sa.Text)
    created_by: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"))


class FinancePeriodClose(UUIDPkMixin, TimestampMixin, Base):
    """A closed finance month (`YYYY-MM`). Posting into it needs `finance.period_close` (ADR-033)."""

    __tablename__ = "finance_period_closes"

    period_key: Mapped[str] = mapped_column(sa.String(7), unique=True, nullable=False)
    note: Mapped[str | None] = mapped_column(sa.Text)
    closed_by: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"))


__all__ = ["CashboxOpening", "CommissionSettlement", "FinancePeriodClose", "LedgerEntry"]
