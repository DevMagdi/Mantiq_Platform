"""Lead pool import (Excel/CSV) + the contact's job title

Revision ID: 0005_lead_import_contact_title
Revises: 0004_integration_settings

Two things the team-management round needs and the schema did not have:

* ``lead_import_jobs`` — one row per uploaded workbook, holding the dry-run preview and the exact rows
  that commit will insert. It is a sibling of ``import_jobs``, not a reuse of it: that table is keyed by
  ``client_id NOT NULL`` and its commit path authorises through the *client* scope, which a pool of
  not-yet-owned leads has no part of. Making it nullable would have left every existing query on
  ``import_jobs`` having to remember a new shape, for no shared behaviour.
* ``leads.contact_title`` — the spreadsheet column "البوزيشن" had no home. ``contact_name`` holds a
  person, and folding a title into ``needs_text`` would save it where nothing can read it.

Like 0002/0003 the enum columns are portable ``VARCHAR + CHECK`` (``native_enum=False``), so the new
table reuses the existing ``importjobstatus_v`` constraint by name rather than inventing a second set of
states for the same lifecycle. Nothing here is inside the RLS net: ``lead_import_jobs`` holds a manager's
upload, not a rep's pipeline — the *leads* it creates are policed the moment they exist.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_lead_import_contact_title"
down_revision: str | None = "0004_integration_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("contact_title", sa.String(length=120), nullable=True))

    op.create_table(
        "lead_import_jobs",
        sa.Column(
            "status",
            sa.Enum(
                "dry_run",
                "pending",
                "running",
                "completed",
                "failed",
                name="importjobstatus_v",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("error_report", sa.JSON(), nullable=False),
        sa.Column("rows_payload", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("committed_by", sa.Uuid(), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["committed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lead_import_jobs_created", "lead_import_jobs", ["created_by", "created_at"], unique=False)


def downgrade() -> None:
    # reverse order of `upgrade()`, as 0001 learned the hard way (ADR-014)
    op.drop_index("ix_lead_import_jobs_created", table_name="lead_import_jobs")
    op.drop_table("lead_import_jobs")
    op.drop_column("leads", "contact_title")
