"""Correct `policies.issued_at` from the audit trail (R10.1, ADR-030 §3).

0009 added the column and backfilled it with

    UPDATE policies SET issued_at = created_at WHERE status = 'issued' AND issued_at IS NULL

`created_at` is *when the draft was booked*, not when it was issued — the very confusion the column exists
to remove. For every policy that already existed when 0009 ran, a draft booked on 28 September and issued on
2 October was therefore counted in September by `policies_issued_mtd`, and the executive screen printed a
month it had not earned.

The truth was already on hand: `policy_issue.py` has written `policy.issued` to `audit_logs` with
`occurred_at` since issuance existed. This revision re-derives `issued_at` from that row, and — importantly —
**removes the guess** for issued policies that have no audit row at all (rows written by a seed or imported
by hand): those go back to `issued_at = NULL`. `services/executive.py` no longer coalesces to `created_at`;
it counts known issue dates only and reports the unknown ones as their own alert, so the KPI is never a date
the office did not record.

Why a new revision instead of editing 0009: merged migrations are immutable (ADR-001, ADR-014) — the CI gate
`scripts/check_migrations.py` fails the build on an edited one, and every environment that already ran 0009
would keep the wrong dates forever.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010_policy_issue_date"
down_revision: str | None = "0009_finance_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. the real issue date, where the audit trail has one (`MIN` because a policy can only be issued once,
    #    but an audit table is append-only and a retried call must not turn into two different dates)
    op.execute(
        """
        UPDATE policies
           SET issued_at = (
                SELECT MIN(a.occurred_at)
                  FROM audit_logs a
                 WHERE a.entity_type = 'policy'
                   AND a.action = 'policy.issued'
                   AND a.entity_id = policies.id
           )
         WHERE status = 'issued'
           AND EXISTS (
                SELECT 1
                  FROM audit_logs a
                 WHERE a.entity_type = 'policy'
                   AND a.action = 'policy.issued'
                   AND a.entity_id = policies.id
           )
        """
    )
    # 2. no audit row ⇒ no known issue date. `created_at` was a guess, and a guess in this column is a wrong
    #    number on the executive screen; NULL is the honest value and the counter reports it separately.
    op.execute(
        """
        UPDATE policies
           SET issued_at = NULL
         WHERE status = 'issued'
           AND NOT EXISTS (
                SELECT 1
                  FROM audit_logs a
                 WHERE a.entity_type = 'policy'
                   AND a.action = 'policy.issued'
                   AND a.entity_id = policies.id
           )
        """
    )


def downgrade() -> None:
    """Back to 0009's state: every issued policy carries *some* timestamp, `created_at` when nothing better."""
    op.execute("UPDATE policies SET issued_at = created_at WHERE status = 'issued' AND issued_at IS NULL")
