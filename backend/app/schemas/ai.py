"""AI request/response shapes (spec M10)."""

from __future__ import annotations

import pydantic
from pydantic import Field

from app.schemas.out import Open


class AIClassifyIn(pydantic.BaseModel):
    """Strict on purpose: a typo in this body would otherwise be read as "classify this other text"."""

    model_config = pydantic.ConfigDict(extra="forbid")

    text: str = Field(min_length=12, max_length=4000)


class AIClassifyOut(Open):
    intent: str
    confidence: float
    queue: str
    urgency: str
    provider: str


class AIStatusOut(Open):
    enabled: bool
    driver: str
    capabilities: list[str]
    note: str


class AITranscribeOut(Open):
    call_id: str
    provider: str
    transcript: str
    summary: str | None = None
    requires_review: bool = True


class AISummarizeOut(Open):
    request_id: str
    provider: str
    summary: str
    note_id: str
    requires_review: bool = True
