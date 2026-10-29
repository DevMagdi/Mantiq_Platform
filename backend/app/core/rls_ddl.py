"""The single source of truth for the row-level-security DDL (spec §6, ADR-003, ADR-014).

Migration `0003_rls_portal_and_role` executes these statements when a database is upgraded.
The PostgreSQL tests execute the *same* statements against a `create_all` database — which is the only way
to test the net on a test database that never ran Alembic, and the reason this module exists instead of the
SQL living inside a migration file: what is asserted is what production installs.

Shape of a policy:

* one policy per table, `FOR ALL`, so that `SELECT`, `UPDATE`, `DELETE` **and `INSERT`** are all covered.
  A table with a SELECT policy but no INSERT policy is not "loose on writes" — PostgreSQL *refuses* the
  write for a `FORCE`'d role (`new row violates row-level security policy`), which would have broken lead
  creation the moment the application connected as a non-superuser. That is only visible on a real server;
  SQLite never enforces it.
* permissive by construction for anything that is not a scoped request: `current_setting(..., true) IS NULL`
  means "no scope installed" (system jobs, migrations, seeding, health checks) and passes.
* `app.bypass_rls = 'on'` is what a manager/admin scope publishes (see `Scope.session_settings`), so
  cross-office work keeps working without weakening the owner's net.
* the owner column is compared as `::text` because the GUC is text by nature; `nullif(..., 'null')` turns
  the literal string `"null"` (what `session_settings` sends when there is no user) back into SQL NULL,
  which matches nothing.
"""

from __future__ import annotations

#: (table, owner column, extra portal clause) — the tables the second net polices.
_GUARDED: tuple[tuple[str, str, bool], ...] = (
    ("leads", "assigned_to", False),
    ("clients", "account_owner_id", True),
)

APP_ROLE = "brokerhub_app"

#: policy names installed by 0002, dropped before the single `FOR ALL` policy replaces them
_SUPERSEDED: tuple[str, ...] = ("scope_read", "scope_write", "scope_select", "scope_update", "scope_delete")


def _predicate(table: str, owner_col: str, portal_clause: bool) -> str:
    lines = [
        "current_setting('app.bypass_rls', true) = 'on'",
        "current_setting('app.bypass_rls', true) IS NULL",
        f"{owner_col}::text = nullif(current_setting('app.scope_user_id', true), 'null')",
    ]
    if portal_clause:
        # a portal session has no staff owner, but is pinned to exactly one client row (ADR-003)
        lines.append("id::text = nullif(current_setting('app.scope_client_id', true), 'null')")
    return "\n                OR ".join(lines)


def statements(*, app_role_grants: bool = True, database: str | None = None) -> list[str]:
    """Every statement, in the order a database needs them."""
    out: list[str] = []
    for table, owner_col, portal_clause in _GUARDED:
        out.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        out.append(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        for suffix in _SUPERSEDED:
            out.append(f"DROP POLICY IF EXISTS {table}_{suffix} ON {table}")
        out.append(f"DROP POLICY IF EXISTS {table}_scope ON {table}")
        predicate = _predicate(table, owner_col, portal_clause)
        out.append(
            f"CREATE POLICY {table}_scope ON {table}\n"
            "            FOR ALL\n"
            f"            USING (\n                {predicate}\n            )\n"
            f"            WITH CHECK (\n                {predicate}\n            )"
        )
    if app_role_grants:
        out.extend(role_statements(database))
    return out


def role_statements(database: str | None = None) -> list[str]:
    """The role `FORCE ROW LEVEL SECURITY` actually applies to.

    A superuser (or any `BYPASSRLS` member) skips row security even on a `FORCE`'d table, so a deployment
    that connects as the database owner has policies that never fire. `brokerhub_app` is the opposite: no
    login, no bypass. Grants are data-only and never include TRUNCATE/REFERENCES.
    """
    out = [
        f"""DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} NOLOGIN NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE;
            END IF;
        END $$"""
    ]
    if database:
        out.append(f'GRANT CONNECT ON DATABASE "{database}" TO {APP_ROLE}')
    out += [
        f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}",
    ]
    return out


def drop_statements() -> list[str]:
    """What 0003's downgrade owes the database: the policies go, the tables stay."""
    out: list[str] = []
    for table, owner_col, _portal in _GUARDED:
        out.append(f"DROP POLICY IF EXISTS {table}_scope ON {table}")
        predicate = _predicate(table, owner_col, portal_clause=False)
        for command in ("SELECT", "UPDATE", "DELETE"):
            check = f"\n            WITH CHECK (\n                {predicate}\n            )" if command == "UPDATE" else ""
            out.append(
                f"CREATE POLICY {table}_scope_{command.lower()} ON {table}\n"
                f"            FOR {command}\n            USING (\n                {predicate}\n            ){check}"
            )
    return out
