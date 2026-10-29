"""The R10 money layer: one ledger, one settlement row per commission (ADR-030)

Revision ID: 0009_finance_ledger
Revises: 0008_webhook_status_width

Three changes, one idea: until now the office's money existed only as *derived facts* — an installment's
`paid_at`, a commission's `status` — so "إجمالي التحصيل هذا الشهر" could not be answered at all, and a void
had nowhere to live. This migration adds the two tables that make a movement a row:

* `ledger_entries` — append-only. `amount > 0` is a check constraint (a negative amount is a direction, not
  a minus sign), and `status = posted` is what every sum filters on, so a void reverses a balance by
  leaving the sum instead of by editing history. The partial unique index on
  `(entry_type, installment_id) WHERE status = 'posted'` is the database-level answer to "can paying this
  installment twice write two receipts?" — the service raises 409 first, this makes the race impossible.
* `commission_settlements` — `commission_id` is unique, so one commission cannot be paid twice even if two
  approvals race. Kept apart from `commissions.status` because that column is the *accrual* the policies
  screen already reads (accrued/settled/clawed_back); paying writes both rows (ADR-030 §4).

`policies.issued_at` is the third change and it is small but load-bearing: `created_at` is when the draft
was booked, and a policy that sat in `pending_insurer` across a month boundary would otherwise be counted
as "issued this month" in the wrong month — the exact class of wrong number the executive screen must not
show. Existing issued rows are backfilled from `created_at`, which is the best fact available for them.

None of these tables join the RLS net of 0002/0003: every read of a ledger row goes through
`with_scope(... , "ledger")`, which resolves visibility through the *client* of the entry, exactly like
policies and issuing cases. A second copy of the rule in SQL would be a second chance to drift.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_finance_ledger"
down_revision: str | None = "0008_webhook_status_width"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("policies", sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE policies SET issued_at = created_at WHERE status = 'issued' AND issued_at IS NULL")

    op.create_table(
        "ledger_entries",
        sa.Column("entry_no", sa.String(length=32), nullable=False),
        sa.Column(
            "entry_type",
            sa.Enum(
                "premium_receivable",
                "commission_receivable",
                "commission_payable",
                "expense",
                "adjustment",
                name="ledgerentrytype_v",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column(
            "direction",
            sa.Enum("in", "out", name="ledgerdirection_v", native_enum=False, create_constraint=True, length=40),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column(
            "status",
            sa.Enum("posted", "void", name="ledgerstatus_v", native_enum=False, create_constraint=True, length=40),
            nullable=False,
        ),
        sa.Column("client_id", sa.Uuid(), nullable=True),
        sa.Column("policy_id", sa.Uuid(), nullable=True),
        sa.Column("installment_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "method",
            sa.Enum(
                "cash",
                "bank",
                "wallet",
                "other",
                name="paymentmethod_v",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("posted_by", sa.Uuid(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_by", sa.Uuid(), nullable=True),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_ledger_amount_positive"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["policy_id"], ["policies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["installment_id"], ["installments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["posted_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["voided_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entry_no"),
    )
    op.create_index("ix_ledger_posted_at", "ledger_entries", ["posted_at"], unique=False)
    op.create_index("ix_ledger_status_type", "ledger_entries", ["status", "entry_type"], unique=False)
    op.create_index("ix_ledger_client", "ledger_entries", ["client_id"], unique=False)
    op.create_index("ix_ledger_policy", "ledger_entries", ["policy_id"], unique=False)
    op.create_index("ix_ledger_search", "ledger_entries", ["entry_type", "direction", "client_id"], unique=False)
    op.create_index(
        "uq_ledger_installment_posted",
        "ledger_entries",
        ["entry_type", "installment_id"],
        unique=True,
        sqlite_where=sa.text("status = 'posted'"),
        postgresql_where=sa.text("status = 'posted'"),
    )

    op.create_table(
        "commission_settlements",
        sa.Column("commission_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "accrued",
                "approved",
                "paid",
                "cancelled",
                name="settlementstatus_v",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("payee_user_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_by", sa.Uuid(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("ledger_entry_id", sa.Uuid(), nullable=True),
        sa.Column(
            "method",
            sa.Enum(
                "cash",
                "bank",
                "wallet",
                "other",
                name="paymentmethod_v",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_settlement_amount_positive"),
        sa.ForeignKeyConstraint(["commission_id"], ["commissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["payee_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["paid_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ledger_entry_id"], ["ledger_entries.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_settlements_status", "commission_settlements", ["status"], unique=False)
    op.create_index("ix_settlements_commission", "commission_settlements", ["commission_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_settlements_commission", table_name="commission_settlements")
    op.drop_index("ix_settlements_status", table_name="commission_settlements")
    op.drop_table("commission_settlements")
    op.drop_index("uq_ledger_installment_posted", table_name="ledger_entries")
    op.drop_index("ix_ledger_search", table_name="ledger_entries")
    op.drop_index("ix_ledger_policy", table_name="ledger_entries")
    op.drop_index("ix_ledger_client", table_name="ledger_entries")
    op.drop_index("ix_ledger_status_type", table_name="ledger_entries")
    op.drop_index("ix_ledger_posted_at", table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_column("policies", "issued_at")
