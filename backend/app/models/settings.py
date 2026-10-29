"""Platform-level key/value configuration that is not a feature flag (spec §8, ADR-016).

`FeatureFlag` is a boolean switch; underwriting defaults are numbers and text, and storing them there
would misreport the type. Values are JSON so a new key needs no migration — and so this table can never
become where a secret lives: secrets belong to `integration_configs.secrets_encrypted` and are encrypted
there. Anything that must be masked on read does not belong in this table at all.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPkMixin


class SystemSetting(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(sa.String(64), unique=True, nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict, nullable=False)
    #: who last changed it — the same audit question `integration_configs` answers, on every row
    updated_by: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"))
