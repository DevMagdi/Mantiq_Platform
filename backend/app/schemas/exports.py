"""Export request bodies (spec §7 `/exports/*`)."""

from __future__ import annotations

from uuid import UUID

from pydantic import Field

from app.domain.enums import RequestStatus
from app.schemas.out import Open


class ExportRequest(Open):
    """Narrowing options for row exports.

    The report kinds ignore them on purpose: an aggregation has no row filters, and accepting keys that
    change nothing would be a silent lie about the file the user just downloaded.
    """

    statuses: list[RequestStatus] | None = Field(default=None, description="repeatable status filter, same values as GET /requests")
    only_open: bool = Field(default=False, description="only new / in progress / waiting for the insurer")
    only_overdue: bool = Field(default=False, description="only rows past their SLA")
    q: str | None = Field(default=None, max_length=120, description="free text over company, employee, subject or request number")
    client_id: UUID | None = Field(default=None, description="limit the export to one client")
