"""Score-8 (ADR-033): cashbox opening balances, finance period closes, hot-sort indexes.

New tables only — the ledger itself is untouched (an opening balance is a control record, not a
movement), plus composite indexes for the two sorts the load reports measured: ledger list
(`posted_at desc, entry_no desc`) and the inbox (`last_activity_at desc`).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_score8_finance_controls"
down_revision: str | None = "0010_policy_issue_date"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cashbox_openings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="EGP"),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount >= 0", name="ck_cashbox_opening_nonnegative"),
    )
    op.create_index("ix_cashbox_openings_effective", "cashbox_openings", ["effective_at"])
    op.create_table(
        "finance_period_closes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("period_key", sa.String(7), nullable=False, unique=True),
        sa.Column("note", sa.Text()),
        sa.Column("closed_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ledger_posted_entry", "ledger_entries", ["posted_at", "entry_no"])
    op.create_index("ix_requests_status_activity", "requests", ["status", "last_activity_at"])


def downgrade() -> None:
    op.drop_index("ix_requests_status_activity", table_name="requests")
    op.drop_index("ix_ledger_posted_entry", table_name="ledger_entries")
    op.drop_table("finance_period_closes")
    op.drop_index("ix_cashbox_openings_effective", table_name="cashbox_openings")
    op.drop_table("cashbox_openings")
