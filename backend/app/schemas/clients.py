"""Client-file wire models (spec §5 clients/contacts/insured).

The service layer keeps accepting plain dicts (it is shared with the importer and the portal),
so these models are the validated front door: the endpoint hands them over with
`model_dump(exclude_unset=True)`.
"""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import ClientName, ContactName, EmailAddress

ClientTypeValue = Literal["company", "individual"]
ClientStatusValue = Literal["active", "inactive", "archived"]
CoverageStatusValue = Literal["active", "pending", "ended"]


class ClientCreate(BaseModel):
    name: ClientName
    type: ClientTypeValue = "company"
    status: ClientStatusValue = "active"
    account_owner_id: UUID | None = None
    since_date: date | None = None
    tax_number: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=400)
    notes: str | None = Field(default=None, max_length=4000)


class ClientUpdate(BaseModel):
    name: ClientName | None = None
    status: ClientStatusValue | None = None
    address: str | None = Field(default=None, max_length=400)
    tax_number: str | None = Field(default=None, max_length=40)
    notes: str | None = Field(default=None, max_length=4000)
    since_date: date | None = None


class ContactCreate(BaseModel):
    name: ContactName
    title: str | None = Field(default=None, max_length=120)
    mobile: str | None = None
    email: EmailAddress | None = None
    is_primary: bool = False


class InsuredCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    mobile: str | None = None
    national_id: str | None = Field(default=None, max_length=40)
    department: str | None = Field(default=None, max_length=120)
    job_title: str | None = Field(default=None, max_length=120)
    coverage_status: CoverageStatusValue = "active"
    coverage_start: date | None = None
    coverage_end: date | None = None
    external_ref: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=2000)


class InsuredUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    mobile: str | None = None
    national_id: str | None = Field(default=None, max_length=40)
    department: str | None = Field(default=None, max_length=120)
    job_title: str | None = Field(default=None, max_length=120)
    coverage_status: CoverageStatusValue | None = None
    coverage_start: date | None = None
    coverage_end: date | None = None
    external_ref: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=2000)
