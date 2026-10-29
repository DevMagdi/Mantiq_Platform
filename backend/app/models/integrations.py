"""Integration settings and the webhook event log (spec §8, ADR-016).

`integration_configs` is one row per provider — not one column per setting — because the setting set of a
provider belongs to the provider: a vendor swap adds a row and a driver, not a migration and a model
change. Non-secret values stay readable JSON (they are shown, edited and diffed as-is); secrets live in
`secrets_encrypted`, a Fernet token over the whole secret map, so one write cannot leave half a
credential rotated.

`webhook_events` exists because the settings screen has to show *something* real about inbound traffic:
before it, a rejected webhook left only a log line. Rows are append-only and deliberately carry **no**
foreign key to clients/requests — a rejected or unknown-sender event must survive, and a FK would either
cascade the evidence away or make the ingest path fail on a payload we already decided to ignore.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, TZDateTime, UUIDPkMixin, utcnow

#: the three providers the settings screen manages, in display order
PROVIDERS: tuple[str, ...] = ("whatsapp", "telephony", "email")


class IntegrationConfig(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "integration_configs"
    __table_args__ = (
        sa.UniqueConstraint("provider", name="uq_integration_configs_provider"),
        sa.Index("ix_integration_configs_provider", "provider"),
    )

    provider: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict, nullable=False)
    secrets_encrypted: Mapped[str | None] = mapped_column(sa.Text)
    #: the operator's switch. `True` only after a successful test and an explicit confirmation (ADR-016).
    is_live: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    last_tested_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    #: "ok" | "failed" | None (never tested)
    last_test_status: Mapped[str | None] = mapped_column(sa.String(16))
    last_test_detail: Mapped[str | None] = mapped_column(sa.Text)
    updated_by: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"))


class WebhookEvent(UUIDPkMixin, Base):
    __tablename__ = "webhook_events"
    __table_args__ = (sa.Index("ix_webhook_events_provider_at", "provider", "occurred_at"),)

    provider: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    #: `message` | `status` | `call_started` | `verification` | `rejected_signature` | …
    event_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    #: the provider's own id (wamid / call id) when it sent one — the handle a support ticket quotes
    external_id: Mapped[str | None] = mapped_column(sa.String(128))
    #: "ok" | "ignored" | "error" | "rejected" | the intake's own result
    #: ("started" | "ended" | "recording_attached" | "duplicate" …). 32, not 16: `recording_attached` is 18
    #: characters, and SQLite hides the overflow while PostgreSQL answers 500 *after* the call was saved
    #: (ADR-024). `record_event` also clamps, so a future provider-shaped string can never do this again.
    status: Mapped[str] = mapped_column(sa.String(32), default="ok", nullable=False)
    detail: Mapped[str | None] = mapped_column(sa.Text)
    occurred_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, nullable=False)
