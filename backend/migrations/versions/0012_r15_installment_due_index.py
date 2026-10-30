"""R15: one index the planner actually asked for — installments by due date.

The «اليوم» board on screen 1 lists installments falling due in the next 14 days, ordered by `due_date`, and
counts the same set. The table's only index on the subject is `ix_installments_policy_due (policy_id,
due_date)`, whose leading column is `policy_id` — a range on `due_date` alone cannot use it, so PostgreSQL read
the whole table and sorted it, on every dashboard open, for every user.

This is the one migration R15 allows: no new table, no column, no data movement — an index that removes a
sequential scan from a route that runs on every return to the dashboard tab. The measurements that justified it
(and the two index ideas that were *not* added, because the plans already used existing indexes) are in
`docs/perf/r15-load-2026-10-30.md`.

Deliberately **not** a partial index on `paid_at IS NULL`: the predicate also filters `status <> 'paid'`, two
representations of the same fact, and a partial index would pin that duplication into the schema. A plain
`due_date` index serves the range scan and the ORDER BY, and stays correct if either flag changes meaning.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012_r15_installment_due_index"
down_revision: str | None = "0011_score8_finance_controls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_installments_due", "installments", ["due_date"])


def downgrade() -> None:
    op.drop_index("ix_installments_due", table_name="installments")
