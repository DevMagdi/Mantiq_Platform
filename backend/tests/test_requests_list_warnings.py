"""`GET /api/v1/requests` must not emit an SAWarning (item 6).

The warning was:

    SAWarning: Multiple rows returned with uselist=False for eagerly-loaded attribute 'Client.account_owner'

It appeared once per request on the busiest screen in the product, and it was **not** silenceable noise:
the cause was that the page query reached `clients` twice — `requests.client` and
`requests.insured_person.client` (the latter is `lazy="joined"`) — while `Client.account_owner` is eager too,
so the same `users` row arrived twice and SQLAlchemy had to pick one without being able to say which.

The fix prunes both client paths (`noload` on contacts / account_owner / source_lead, which the list
serializer never reads). These tests pin it with warnings as *errors*, so a future option added on one path
only fails here instead of flooding the log.
"""

from __future__ import annotations

import warnings
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.exc import SAWarning

from tests.helpers import login

ADMIN = "admin@mantiq.test"


async def _seed_request_with_both_clients(client: Any, ids: dict[str, dict[str, str]]) -> str:
    """A request that reaches `clients` twice: its own client *and* its insured person's client.

    Both clients carry an `account_owner`, which is the eager many-to-one that SQLAlchemy could not resolve
    once the two paths produced the same users row twice.
    """
    from app.core.db import get_sessionmaker
    from app.models.clients import InsuredPerson
    from sqlalchemy import select

    maker = get_sessionmaker()
    async with maker() as session:
        existing = (
            await session.execute(select(InsuredPerson).where(InsuredPerson.external_ref == "r17-saw-insured"))
        ).scalar_one_or_none()
        if existing is None:
            existing = InsuredPerson(client_id=UUID(ids["clients"]["ألف"]), name="موظف للتغطية", external_ref="r17-saw-insured")
            session.add(existing)
            await session.commit()
        insured_id = str(existing.id)

    async with login(client, ADMIN) as headers:
        created = await client.post(
            "/api/v1/requests",
            json={"subject": "طلب على مسارَي العميل", "client_id": ids["clients"]["ألف"], "insured_person_id": insured_id},
            headers=headers,
        )
    assert created.status_code in (200, 201), created.text
    return str(created.json()["id"])


@pytest.mark.filterwarnings("error::sqlalchemy.exc.SAWarning")
async def test_the_requests_list_does_not_warn_about_a_duplicated_eager_row(client: Any, ids: dict[str, dict[str, str]]) -> None:
    await _seed_request_with_both_clients(client, ids)
    async with login(client, ADMIN) as headers:
        response = await client.get("/api/v1/requests", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["items"], "the list must actually return rows — an empty page proves nothing"


async def test_the_warning_reproduces_without_the_pruned_second_path(
    monkeypatch: pytest.MonkeyPatch, client: Any, ids: dict[str, dict[str, str]]
) -> None:
    """The regression guard has teeth: with the insured-person path un-pruned, the warning comes back.

    This is the *before* half of the proof, kept as a test so the claim "it was really that join" stays true
    even after the database grows. If SQLAlchemy ever stops warning here, this test fails — and that is the
    signal to look at the guard above rather than trust it.
    """
    from app.repositories import requests_repo

    def unpruned() -> Any:
        from app.models.auth import Role, User
        from app.models.clients import Client
        from app.models.requests import ServiceRequest
        from sqlalchemy import select
        from sqlalchemy.orm import joinedload

        return select(ServiceRequest).options(
            joinedload(ServiceRequest.client).noload(Client.contacts),
            joinedload(ServiceRequest.client).noload(Client.account_owner),
            joinedload(ServiceRequest.client).noload(Client.source_lead),
            # the bug, verbatim: the insured person's client is joined with its eager owner left in place
            joinedload(ServiceRequest.insured_person),
            joinedload(ServiceRequest.insurer),
            joinedload(ServiceRequest.insurance_type),
            joinedload(ServiceRequest.assignee).noload(User.scopes),
            joinedload(ServiceRequest.assignee).noload(User.portal_link),
            joinedload(ServiceRequest.assignee).joinedload(User.role).noload(Role.permissions),
        )

    await _seed_request_with_both_clients(client, ids)
    monkeypatch.setattr(requests_repo, "_list_base", unpruned)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        async with login(client, ADMIN) as headers:
            response = await client.get("/api/v1/requests", headers=headers)

    assert response.status_code == 200, response.text
    messages = [str(item.message) for item in caught if issubclass(item.category, SAWarning)]
    assert any("account_owner" in message for message in messages), (
        f"the un-pruned second client path must still produce the warning this round fixed; got {messages}"
    )


@pytest.mark.filterwarnings("error::sqlalchemy.exc.SAWarning")
async def test_pruning_did_not_empty_the_columns_the_list_renders(client: Any, ids: dict[str, dict[str, str]]) -> None:
    """`noload` must remove *unused* relations, not the names on the row.

    A fix that made the warning disappear by nulling `client` or `insured_person` would pass the warning test
    and break the screen, so the serializer's inputs are asserted here.
    """
    await _seed_request_with_both_clients(client, ids)
    async with login(client, ADMIN) as headers:
        response = await client.get("/api/v1/requests", headers=headers)
    assert response.status_code == 200, response.text
    rows = response.json()["items"]
    # `serialize_request` reads exactly these four off the two joined rows; they are what `noload` could break
    assert any(row.get("client_name") and row["client_name"] != "غير مصنّف" for row in rows), rows
    assert any(row.get("employee_name") and row["employee_name"] != "موظف غير معروف" for row in rows), rows
    assert any(row.get("client_id") for row in rows), "the id the row links to must survive too"
