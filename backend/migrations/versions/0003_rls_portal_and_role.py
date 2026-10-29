"""The RLS net, made usable: portal clause, INSERT coverage, and the role it applies to

Revision ID: 0003_rls_portal_and_role
Revises: 0002_pg_rls_and_partial_indexes

Why this migration exists (spec §6, ADR-003, ADR-014):

`app/core/rls.py` now publishes `app.bypass_rls` / `app.scope_user_id` / `app.scope_client_id` on every
request transaction, from the same `Scope` the repositories use. Three things had to change for that to be
a safety net rather than a outage generator:

1. **portal sessions** are pinned to one client and have no `account_owner_id` match, so 0002's predicate
   would have hidden a company's own row from its own portal login — `app.scope_client_id` is added;
2. **writes**: 0002 defined policies for SELECT/UPDATE/DELETE only. On a `FORCE`'d table PostgreSQL then
   rejects every `INSERT` for a non-superuser, so lead creation would fail the moment the app stopped
   connecting as the owner. One `FOR ALL` policy per table covers reads *and* writes;
3. **the role**: RLS never applies to a superuser, and every environment so far connected as one, which is
   why the policies were invisible. `brokerhub_app` is `NOLOGIN NOBYPASSRLS` with data grants only, and is
   the role a deployment (and the tests) connects as.

The DDL lives in `app/core/rls_ddl.py` so the PostgreSQL tests install the identical statements on a
`create_all` database. PostgreSQL-only and idempotent: a partially hardened database still upgrades.

.. important::
   A deployment that has already run the old 0002-only chain under the owner role must create the
   `brokerhub_app` role and point `DATABASE_URL` at it for any of this to take effect — docs/runbook.md
   §5 "الصلاحيات والعزل" has the exact two commands. The migration creates the role, not the DSN.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core import rls_ddl

revision: str = "0003_rls_portal_and_role"
down_revision: str | None = "0002_pg_rls_and_partial_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: `pg_create_role` as a *membership* check only exists from PostgreSQL 17, and the deployment target is
#: 16 (ADR-001), so the capability is read from the role's own attributes with a superuser shortcut.
_CAN_CREATE_ROLES = """
    SELECT current_setting('is_superuser') = 'on'
        OR EXISTS (
            SELECT 1 FROM pg_roles WHERE rolname = current_user AND (rolsuper OR rolcreaterole)
        )
"""


def _can_create_roles(bind: sa.Connection) -> bool:
    """Without `CREATEROLE` the policies still land and only the role is skipped — never abort an upgrade."""
    return bool(bind.scalar(sa.text(_CAN_CREATE_ROLES)))


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    database = bind.scalar(sa.text("SELECT current_database()"))
    for statement in rls_ddl.statements(app_role_grants=_can_create_roles(bind), database=str(database)):
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for statement in rls_ddl.drop_statements():
        op.execute(statement)
