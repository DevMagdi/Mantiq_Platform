"""`webhook_events.status` was 16 characters and the telephony path writes 18 ("recording_attached").

Found by CI, not by the suite: SQLite ignores `VARCHAR(n)` and PostgreSQL rejects it, so every local run
stored the value happily and a real `recording_ready` webhook answered 500 on the only backend that
matters. The failure also arrived *after* the request had been ingested, which is worse than a rejected
webhook: HMAC verification passed, the recording was downloaded and stored, and then the audit insert
aborted the transaction — the caller would retry forever and the event log would stay empty.

Revision ID: 0008_webhook_status_width
Revises: 0007_portal_username
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_webhook_status_width"
down_revision: str | None = "0007_portal_username"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: the longest status the intake returns today is "recording_attached" (18); 32 leaves room for the next one
WIDTH = 32


def _widen(from_width: int, to_width: int) -> None:
    # `batch_alter_table` because SQLite has no `ALTER COLUMN`: it rebuilds the table, which is also what
    # makes the chain testable on the same engine the tests use (ADR-014). PostgreSQL takes the plain form.
    with op.batch_alter_table("webhook_events") as batch:
        batch.alter_column(
            "status",
            type_=sa.String(length=to_width),
            existing_type=sa.String(length=from_width),
            existing_nullable=False,
        )


def upgrade() -> None:
    _widen(16, WIDTH)


def downgrade() -> None:
    _widen(WIDTH, 16)
