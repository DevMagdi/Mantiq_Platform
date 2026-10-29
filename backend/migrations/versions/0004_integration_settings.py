"""Integration settings, underwriting defaults, and the webhook event log (spec §8, ADR-016)

Revision ID: 0004_integration_settings
Revises: 0003_rls_portal_and_role

Two tables, no data. Rows are *not* seeded here on purpose: the service synthesizes the defaults of
`app/services/integrations_settings.py` when a provider has no row, so a default changed later does not
have to be chased through an already-applied migration. That also means the settings screen must be able
to say "not configured" honestly, which is the state it shows before anyone touches it.

Both tables are outside the RLS net of 0002/0003: they are configuration and diagnostics, not customer
data, and scoping them by an owner would hide the platform's own state from the manager who has to fix it.
`settings.manage` is the gate (see `app/api/v1/endpoints/settings.py`).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_integration_settings"
down_revision: str | None = "0003_rls_portal_and_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integration_configs",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("secrets_encrypted", sa.Text(), nullable=True),
        sa.Column("is_live", sa.Boolean(), nullable=False),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_status", sa.String(length=16), nullable=True),
        sa.Column("last_test_detail", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", name="uq_integration_configs_provider"),
    )
    op.create_index("ix_integration_configs_provider", "integration_configs", ["provider"], unique=False)

    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_system_settings_key", "system_settings", ["key"], unique=True)

    op.create_table(
        "webhook_events",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_webhook_events_provider_at", "webhook_events", ["provider", "occurred_at"], unique=False)


def downgrade() -> None:
    # reverse order of `upgrade()`, as 0001 learned the hard way (ADR-014)
    op.drop_index("ix_webhook_events_provider_at", table_name="webhook_events")
    op.drop_table("webhook_events")
    op.drop_index("ix_system_settings_key", table_name="system_settings")
    op.drop_table("system_settings")
    op.drop_index("ix_integration_configs_provider", table_name="integration_configs")
    op.drop_table("integration_configs")
