"""Portal usernames: a second login handle for client accounts (ADR-022)

Revision ID: 0007_portal_username
Revises: 0006_policy_documents

One nullable, unique column. Staff keep signing in with their e-mail; a client owner may sign in with the
username printed on their onboarding note, which is the string they actually remember. `NULL` is the normal
value for every staff seat, and both SQLite and PostgreSQL treat `NULL`s in a unique index as distinct — so
"unique username, optional" needs no partial index.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_portal_username"
down_revision: str | None = "0006_policy_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(length=64), nullable=True))
    op.create_index("ix_users_username", "users", ["username"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
    op.drop_column("users", "username")
