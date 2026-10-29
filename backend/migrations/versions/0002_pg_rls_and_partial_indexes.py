"""PostgreSQL-only hardening: row-level security + partial unique indexes

Revision ID: 0002_pg_rls_and_partial_indexes
Revises: 0001_baseline

Why a separate migration: these are the second net over application-level scoping (spec §6) and
they only exist on PostgreSQL. Everything is guarded so the same chain still applies on SQLite
dev databases (ADR-001 §6). Never edit this file once merged — add a new revision; the one exception is
marked below and justified in ADR-014.
"""

# ALEMBIC-REWRITE-APPROVED: `CREATE POLICY … FOR UPDATE, DELETE` is not valid SQL (a policy names exactly
# one command), so this revision aborted on every PostgreSQL database and had therefore never been applied.
# The write half is now one policy per command, which is the same rule set; the `FOR ALL` replacement that
# also covers `INSERT` belongs to 0003, where adding it is legal. ADR-014.

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_pg_rls_and_partial_indexes"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, owner-column) pairs that RLS must police. `clients.account_owner_id` is the
# immutable owner frozen at conversion; `leads.assigned_to` follows re-assignment.
_GUARDED = (
    ("leads", "assigned_to"),
    ("clients", "account_owner_id"),
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # --- partial uniques: "no two active leads share a mobile" (spec §5) --------------
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_leads_active_mobile
        ON leads (mobile)
        WHERE mobile IS NOT NULL AND deleted_at IS NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_insured_client_active_mobile
        ON insured_persons (client_id, mobile)
        WHERE mobile IS NOT NULL AND deleted_at IS NULL
        """
    )

    # --- RLS -------------------------------------------------------------------------
    # `CREATE POLICY` accepts exactly one command per policy (there is no `FOR UPDATE, DELETE`),
    # so the write half is one policy per command. Postgres-only: everything below is skipped on
    # SQLite (ADR-001 §6), which is why this file never ran until `alembic upgrade head` was
    # exercised against a real PostgreSQL database — see ADR-014.
    for table, owner_col in _GUARDED:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        scope = f"""
                current_setting('app.bypass_rls', true) = 'on'
                OR current_setting('app.bypass_rls', true) IS NULL
                OR {owner_col}::text = nullif(current_setting('app.scope_user_id', true), 'null')
            """
        for command in ("SELECT", "UPDATE", "DELETE"):
            # `WITH CHECK` only exists for UPDATE/INSERT-style policies; DELETE takes USING alone.
            check = f"WITH CHECK ({scope})" if command == "UPDATE" else ""
            op.execute(
                f"""
            CREATE POLICY {table}_scope_{command.lower()} ON {table}
            FOR {command}
            USING ({scope})
            {check}
            """
            )

    # Audit rows are append-only at the DB level too (spec §5 "لا تُحذف ولا تُعدَّل").
    op.execute(
        """
        CREATE OR REPLACE FUNCTION broker_hub_audit_is_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_logs_no_update BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION broker_hub_audit_is_immutable()
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_update ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS broker_hub_audit_is_immutable()")
    for table, _col in _GUARDED:
        for command in ("update", "delete", "select"):
            op.execute(f"DROP POLICY IF EXISTS {table}_scope_{command} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP INDEX IF EXISTS uq_leads_active_mobile")
    op.execute("DROP INDEX IF EXISTS uq_insured_client_active_mobile")


_ = sa
