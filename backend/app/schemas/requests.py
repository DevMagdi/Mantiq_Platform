"""Service-desk wire models (spec §5/§7)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel

ChannelValue = Literal["whatsapp", "call", "email", "manual"]
StatusValue = Literal["new", "in_progress", "waiting_insurer", "replied", "resolved", "closed"]
PriorityValue = Literal["low", "normal", "high", "urgent"]


class RequestCreate(BaseModel):
    subject: str = Field(min_length=2, max_length=255)
    channel: ChannelValue = "manual"
    priority: PriorityValue = "normal"
    client_id: UUID | None = None
    insured_person_id: UUID | None = None
    policy_id: UUID | None = None
    insurer_id: UUID | None = None
    insurance_type_id: UUID | None = None
    contact_name: str | None = None
    contact_mobile: str | None = None
    description: str | None = None
    first_message: str | None = None
    assigned_to: UUID | None = None

    def model_post_init(self, _ctx: Any) -> None:
        if self.client_id is None and self.insured_person_id is None and not self.contact_mobile:
            raise ValueError("A request needs a client, an insured person, or a caller mobile")


class RequestUpdate(BaseModel):
    subject: str | None = Field(default=None, min_length=2, max_length=255)
    description: str | None = None
    priority: PriorityValue | None = None
    client_id: UUID | None = None
    insured_person_id: UUID | None = None
    insurer_id: UUID | None = None
    insurance_type_id: UUID | None = None


class RequestStatusChange(BaseModel):
    status: StatusValue
    note: str | None = Field(default=None, max_length=2000)


class RequestAssign(BaseModel):
    assigned_to: UUID | None = None


class RequestMessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=8000)
    channel: ChannelValue | None = None
    attachment_ids: list[UUID] = Field(default_factory=list)


class NoteCreate(BaseModel):
    body: str = Field(min_length=1, max_length=8000)


class MessageOut(ORMModel):
    id: UUID
    direction: str
    channel: str
    body: str | None = None
    media_kind: str | None = None
    duration_s: int | None = None
    waveform: list[float] | None = None
    sender_ref: str | None = None
    delivery_status: str
    created_at: datetime
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class CallOut(BaseModel):
    id: UUID
    provider_call_id: str
    direction: str
    from_number: str | None = None
    to_number: str | None = None
    caller_name: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    duration_s: int
    has_recording: bool = False
    recording_consent: bool = False
    listen_allowed: bool = False
    transcript: str | None = None
    summary: str | None = None


class RequestBrief(ORMModel):
    id: UUID
    number: int
    subject: str
    status: str
    priority: str
    channel: str
    is_overdue: bool = False
    sla_due_at: datetime | None = None
    sla_minutes_remaining: int | None = None
    sla_hours_late: int | None = None
    employee_name: str | None = None
    mobile: str | None = None
    client_id: UUID | None = None
    client_name: str | None = None
    insurance_type: str | None = None
    insurer: str | None = None
    assignee_name: str | None = None
    last_activity_at: datetime
    created_at: datetime
    first_message: str | None = None


class RequestDetail(RequestBrief):
    description: str | None = None
    assigned_to: UUID | None = None
    first_response_at: datetime | None = None
    resolved_at: datetime | None = None
    messages: list[MessageOut] = Field(default_factory=list)
    notes: list[dict[str, Any]] = Field(default_factory=list)
    calls: list[CallOut] = Field(default_factory=list)


class RecordingUrl(BaseModel):
    url: str
    expires_at: datetime
    duration_s: int
    recording_consent: bool
