"""Issued-policy documents: the rendered file of one policy (ADR-021)

Revision ID: 0006_policy_documents
Revises: 0005_lead_import_contact_title

One table, no data. `policies` already held the *record*; what was missing is the artefact a client keeps
— a PDF whose bytes live in object storage and whose metadata (filename, size, checksum, the number that
was printed) lives next to the policy. A column on `policies` would have made "issue again after the file
was lost" unrepresentable, and the old bytes are exactly what a client may already be holding.

Not inside the RLS net of 0002/0003: a policy document is reachable only through a policy that is itself
scoped (`with_scope`), and the portal path checks the client id before it touches storage. Adding a policy
owner column here would be a third copy of the same rule with a third chance to drift.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_policy_documents"
down_revision: str | None = "0005_lead_import_contact_title"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "policy_documents",
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("mime", sa.String(length=128), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=True),
        sa.Column("issued_by", sa.Uuid(), nullable=True),
        sa.Column("policy_no", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["policy_id"], ["policies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["issued_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_policy_documents_policy", "policy_documents", ["policy_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_policy_documents_policy", table_name="policy_documents")
    op.drop_table("policy_documents")
