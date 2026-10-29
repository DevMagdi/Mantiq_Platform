"""Identity, RBAC, scopes and portal accounts."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import PortalRole, ScopeType
from app.models.base import Base, SoftDeleteMixin, TimestampMixin, TZDateTime, UUIDPkMixin, enum_col

if TYPE_CHECKING:
    from app.models.clients import Client


role_permissions = sa.Table(
    "role_permissions",
    Base.metadata,
    sa.Column("role_id", sa.Uuid(), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("permission_id", sa.Uuid(), sa.ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)


class Role(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(sa.String(48), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    name_en: Mapped[str | None] = mapped_column(sa.String(120))
    is_system: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)

    permissions: Mapped[list[Permission]] = relationship(secondary=role_permissions, lazy="selectin", order_by="Permission.code")


class Permission(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(sa.String(64), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    group: Mapped[str] = mapped_column(sa.String(40), default="general", nullable=False)


class User(UUIDPkMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "users"
    __table_args__ = (sa.Index("ix_users_role", "role_id"),)

    email: Mapped[str] = mapped_column(sa.String(255), unique=True, nullable=False)
    #: login handle for portal accounts (`owner.mantiq`), because a company owner remembers a username,
    #: not the mailbox the brokerage invented for them. Unique when set, null for every staff seat.
    username: Mapped[str | None] = mapped_column(sa.String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    mobile: Mapped[str | None] = mapped_column(sa.String(20))
    role_id: Mapped[UUID] = mapped_column(sa.Uuid(), sa.ForeignKey("roles.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    mfa_enabled: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    # TOTP secret is stored encrypted at rest by the deployment (disk/bucket level) per spec §6;
    # never serialized. Kept nullable so MFA is opt-in.
    totp_secret_encrypted: Mapped[str | None] = mapped_column(sa.Text)
    refresh_token_version: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)

    role: Mapped[Role] = relationship(lazy="joined", backref="users")
    scopes: Mapped[list[UserScope]] = relationship(back_populates="user", lazy="selectin", cascade="all, delete-orphan")
    portal_link: Mapped[ClientPortalUser | None] = relationship(
        back_populates="user", lazy="selectin", uselist=False, foreign_keys="ClientPortalUser.user_id"
    )

    @property
    def permission_codes(self) -> set[str]:
        return {p.code for p in self.role.permissions}

    @property
    def is_portal_only(self) -> bool:
        return self.role.code in {"client_owner", "client_contact"}


class UserScope(UUIDPkMixin, TimestampMixin, Base):
    """Restricts a staff member to a set of clients and/or insurers (spec §5 user_scopes)."""

    __tablename__ = "user_scopes"
    __table_args__ = (
        sa.UniqueConstraint("user_id", "scope_type", "scope_id", name="uq_user_scope"),
        sa.Index("ix_user_scopes_user", "user_id"),
    )

    user_id: Mapped[UUID] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    scope_type: Mapped[ScopeType] = mapped_column(enum_col(ScopeType), nullable=False)
    scope_id: Mapped[UUID] = mapped_column(sa.Uuid(), nullable=False)

    user: Mapped[User] = relationship(back_populates="scopes")


class ClientPortalUser(UUIDPkMixin, TimestampMixin, Base):
    """Portal login for the client-company owner / contact. Sees one client only."""

    __tablename__ = "client_portal_users"
    __table_args__ = (
        sa.UniqueConstraint("user_id", name="uq_portal_user"),
        sa.Index("ix_portal_client", "client_id"),
    )

    user_id: Mapped[UUID] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    client_id: Mapped[UUID] = mapped_column(sa.Uuid(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    portal_role: Mapped[PortalRole] = mapped_column(enum_col(PortalRole), default=PortalRole.OWNER, nullable=False)
    full_name: Mapped[str | None] = mapped_column(sa.String(160))

    client: Mapped[Client] = relationship(lazy="joined")
    user: Mapped[User] = relationship(back_populates="portal_link", foreign_keys=[user_id])
