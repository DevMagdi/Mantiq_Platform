"""Shared response models + the uniform error envelope (spec §7)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Shape-only email validation. pydantic's EmailStr rejects reserved TLDs (.test/.local), which
# would lock out internal and demo accounts; syntax is checked here and deliverability is not our
# concern at the API boundary.
EmailAddress = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_lower=True, pattern=r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$", max_length=255),
]


#: A human name that is **stripped before it is measured**. `min_length` alone counts the spaces, so
#: `"   "` passed as a company name and was stored as an empty lead (`Lead.name = ""`), which then showed
#: up as a blank Kanban card and an unnamed client file after conversion — found in the Live QA sweep.
LeadName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]
ClientName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=220)]
ContactName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=160)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: Any | None = None


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int
    pages: int


class IdName(BaseModel):
    id: str
    name: str


class Badge(ORMModel):
    key: str
    tone: str = "b"
    label: str | None = None


class RefInput(BaseModel):
    id: str | None = None
    allow_duplicate: bool = Field(default=False, description="Force-create despite a visible duplicate")


_ = datetime
