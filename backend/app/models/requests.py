"""Service desk: unified request/ticket, messages, calls, attachments, notes, notifications, audit."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import (
    AttachmentOwner,
    CallDirection,
    Channel,
    DeliveryStatus,
    MessageDirection,
    NotificationType,
    RequestPriority,
    RequestStatus,
)
from app.models.base import Base, TimestampMixin, TZDateTime, UUIDPkMixin, enum_col, utcnow

if TYPE_CHECKING:
    from app.models.auth import User
    from app.models.clients import Client, InsuredPerson
    from app.models.masters import InsuranceType, Insurer


class ServiceRequest(UUIDPkMixin, TimestampMixin, Base):
    """A ticket from any channel. `number` is the human id shown as #2841."""

    __tablename__ = "requests"
    __table_args__ = (
        sa.UniqueConstraint("number", name="uq_requests_number"),
        sa.Index("ix_requests_status_sla", "status", "sla_due_at"),
        sa.Index("ix_requests_client", "client_id"),
        sa.Index("ix_requests_assigned", "assigned_to"),
        sa.Index("ix_requests_mobile", "contact_mobile"),
        sa.Index("ix_requests_last_activity", "last_activity_at"),
    )

    number: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    client_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("clients.id", ondelete="SET NULL"))
    insured_person_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("insured_persons.id", ondelete="SET NULL"))
    policy_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("policies.id", ondelete="SET NULL"))
    insurer_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("insurers.id", ondelete="SET NULL"))
    insurance_type_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("insurance_types.id", ondelete="SET NULL"))

    channel: Mapped[Channel] = mapped_column(enum_col(Channel), default=Channel.MANUAL, nullable=False)
    subject: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)

    # Caller identity when it is not (yet) matched to an insured person — spec §1 "رقم الموبايل".
    contact_name: Mapped[str | None] = mapped_column(sa.String(160))
    contact_mobile: Mapped[str | None] = mapped_column(sa.String(20))

    status: Mapped[RequestStatus] = mapped_column(enum_col(RequestStatus), default=RequestStatus.NEW, nullable=False)
    priority: Mapped[RequestPriority] = mapped_column(enum_col(RequestPriority), default=RequestPriority.NORMAL, nullable=False)
    assigned_to: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"))

    sla_due_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    first_response_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    is_overdue: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    last_activity_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, nullable=False)

    # external ids for idempotent webhook intake
    external_thread_id: Mapped[str | None] = mapped_column(sa.String(128))

    client: Mapped[Client | None] = relationship(lazy="joined", backref="requests")
    insured_person: Mapped[InsuredPerson | None] = relationship(lazy="joined", backref="requests")
    insurer: Mapped[Insurer | None] = relationship(lazy="joined")
    insurance_type: Mapped[InsuranceType | None] = relationship(lazy="joined")
    assignee: Mapped[User | None] = relationship(foreign_keys=[assigned_to], lazy="joined")

    messages: Mapped[list[RequestMessage]] = relationship(
        back_populates="request",
        lazy="noload",
        cascade="all, delete-orphan",
        order_by="RequestMessage.created_at",
    )
    calls: Mapped[list[CallRecord]] = relationship(back_populates="request", lazy="noload", cascade="all, delete-orphan")
    notes: Mapped[list[InternalNote]] = relationship(back_populates="request", lazy="noload", cascade="all, delete-orphan")
    attachments: Mapped[list[Attachment]] = relationship(back_populates="request", lazy="noload", cascade="all, delete-orphan")


class RequestMessage(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "request_messages"
    __table_args__ = (
        sa.UniqueConstraint("external_id", name="uq_request_messages_external_id"),
        sa.Index("ix_request_messages_request_time", "request_id", "created_at"),
    )

    request_id: Mapped[UUID] = mapped_column(sa.Uuid(), sa.ForeignKey("requests.id", ondelete="CASCADE"), nullable=False)
    direction: Mapped[MessageDirection] = mapped_column(enum_col(MessageDirection), nullable=False)
    channel: Mapped[Channel] = mapped_column(enum_col(Channel), default=Channel.MANUAL, nullable=False)
    body: Mapped[str | None] = mapped_column(sa.Text)
    media_kind: Mapped[str | None] = mapped_column(sa.String(24))  # audio|image|document|null
    duration_s: Mapped[int | None] = mapped_column(sa.Integer)
    waveform: Mapped[list[float] | None] = mapped_column(sa.JSON)
    sender_ref: Mapped[str | None] = mapped_column(sa.String(160))  # wa id / caller name
    external_id: Mapped[str | None] = mapped_column(sa.String(128))
    delivery_status: Mapped[DeliveryStatus] = mapped_column(enum_col(DeliveryStatus), default=DeliveryStatus.SENT, nullable=False)
    author_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"))

    request: Mapped[ServiceRequest] = relationship(back_populates="messages")
    # Message attachments are queried by (owner_type, owner_id) in the API; a relationship here
    # would need a non-FK join column, so it is intentionally not modeled.


class CallRecord(UUIDPkMixin, TimestampMixin, Base):
    """A call leg linked to a request or a lead; recording lives in MinIO/local storage."""

    __tablename__ = "call_records"
    __table_args__ = (
        sa.UniqueConstraint("provider_call_id", name="uq_call_provider_id"),
        sa.Index("ix_call_records_request", "request_id"),
        sa.Index("ix_call_records_lead", "lead_id"),
        sa.Index("ix_call_records_from", "from_number"),
    )

    request_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("requests.id", ondelete="CASCADE"))
    lead_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("leads.id", ondelete="CASCADE"))
    provider_call_id: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    provider: Mapped[str | None] = mapped_column(sa.String(48))
    direction: Mapped[CallDirection] = mapped_column(enum_col(CallDirection), default=CallDirection.INBOUND, nullable=False)
    from_number: Mapped[str | None] = mapped_column(sa.String(20))
    to_number: Mapped[str | None] = mapped_column(sa.String(20))
    caller_name: Mapped[str | None] = mapped_column(sa.String(160))
    started_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    duration_s: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)

    recording_object_key: Mapped[str | None] = mapped_column(sa.String(512))
    recording_size: Mapped[int | None] = mapped_column(sa.BigInteger)
    recording_consent: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    listen_count: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)

    transcript: Mapped[str | None] = mapped_column(sa.Text)  # Phase 2
    summary: Mapped[str | None] = mapped_column(sa.Text)  # Phase 2

    request: Mapped[ServiceRequest | None] = relationship(back_populates="calls")


class Attachment(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "attachments"
    __table_args__ = (sa.Index("ix_attachments_owner", "owner_type", "owner_id"),)

    owner_type: Mapped[AttachmentOwner] = mapped_column(enum_col(AttachmentOwner), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(sa.Uuid(), nullable=False)
    request_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("requests.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(sa.String(512), nullable=False)
    mime: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    size: Mapped[int] = mapped_column(sa.BigInteger, default=0, nullable=False)
    checksum: Mapped[str | None] = mapped_column(sa.String(64))
    uploaded_by: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"))

    request: Mapped[ServiceRequest | None] = relationship(back_populates="attachments")


class InternalNote(UUIDPkMixin, TimestampMixin, Base):
    """Never serialized to the client portal (spec §6)."""

    __tablename__ = "internal_notes"
    __table_args__ = (sa.Index("ix_internal_notes_request", "request_id"),)

    request_id: Mapped[UUID] = mapped_column(sa.Uuid(), sa.ForeignKey("requests.id", ondelete="CASCADE"), nullable=False)
    author_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(sa.Text, nullable=False)

    request: Mapped[ServiceRequest] = relationship(back_populates="notes")
    author: Mapped[User | None] = relationship(lazy="joined")


class Notification(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (sa.Index("ix_notifications_user_read", "user_id", "read_at"),)

    user_id: Mapped[UUID] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[NotificationType] = mapped_column(enum_col(NotificationType), nullable=False)
    title: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    body: Mapped[str | None] = mapped_column(sa.Text)
    entity_type: Mapped[str | None] = mapped_column(sa.String(48))
    entity_id: Mapped[UUID | None] = mapped_column(sa.Uuid())
    payload: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(TZDateTime())


class AuditLog(UUIDPkMixin, Base):
    """Append-only (spec §5: never updated or deleted)."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        sa.Index("ix_audit_entity", "entity_type", "entity_id"),
        sa.Index("ix_audit_actor_time", "actor_id", "occurred_at"),
    )

    actor_id: Mapped[UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"))
    actor_email: Mapped[str | None] = mapped_column(sa.String(255))
    action: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(sa.String(48), nullable=False)
    entity_id: Mapped[UUID | None] = mapped_column(sa.Uuid())
    before: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    after: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    ip: Mapped[str | None] = mapped_column(sa.String(64))
    user_agent: Mapped[str | None] = mapped_column(sa.String(255))
    occurred_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, nullable=False)


class RequestNumberCounter(Base):
    """Portable auto-increment counter for human request numbers (starts at 1000).

    A PG sequence would be faster but is not portable to the SQLite dev/test setup, and the
    number must be gap-free-ish and readable (`#2841`) rather than high-throughput.
    """

    __tablename__ = "request_number_counter"

    key: Mapped[str] = mapped_column(sa.String(16), primary_key=True, default="requests")
    last_value: Mapped[int] = mapped_column(sa.BigInteger, default=1000, nullable=False)
