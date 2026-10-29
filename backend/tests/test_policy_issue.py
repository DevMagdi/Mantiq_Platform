"""Policy issuance: the status move, the file, and who may hold it (ADR-021).

The gap this closes was not "an endpoint is missing" — it was that a policy could be `issued` in the
product with nothing to give the client. Every test here therefore checks the *file*, not only the status:
bytes come back from `/policies/{id}/document`, they are a real PDF (`%PDF-`), and the portal can fetch the
same document for its own company and nothing else.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from app.core.db import get_sessionmaker

from tests.helpers import login

pytestmark = pytest.mark.integration

MANAGER = "manager@brokerhub.test"
ADMIN = "admin@brokerhub.test"
REP = "rep_a@brokerhub.test"


async def _enable_phase2(admin_headers, client) -> None:
    """The flag row is seeded by the real system, not by the test bootstrap — create it, then flip it."""
    from app.models.masters import FeatureFlag
    from sqlalchemy import select

    async with get_sessionmaker()() as session:
        existing = (await session.execute(select(FeatureFlag).where(FeatureFlag.key == "phase2.enabled"))).scalar_one_or_none()
        if existing is None:
            session.add(FeatureFlag(key="phase2.enabled", enabled=False, description="Phase 2 screens"))
            await session.commit()
    response = await client.patch("/api/v1/feature-flags/phase2.enabled", headers=admin_headers, json={"enabled": True})
    assert response.status_code == 200, response.text


async def _new_policy(client, headers, *, client_id: str, **overrides) -> dict:
    payload = {
        "client_id": client_id,
        "start_date": date.today().isoformat(),
        "end_date": (date.today() + timedelta(days=365)).isoformat(),
        "premium_amount": 12000,
    }
    payload.update(overrides)
    response = await client.post("/api/v1/policies", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def test_issuing_a_policy_writes_a_real_pdf(client, ids) -> None:
    async with login(client, ADMIN) as admin, login(client, MANAGER) as mgr:
        await _enable_phase2(admin, client)
        policy = await _new_policy(client, mgr, client_id=ids["clients"]["ألف"])

        issued = await client.post(f"/api/v1/policies/{policy['id']}/issue", headers=mgr)
        assert issued.status_code == 200, issued.text
        body = issued.json()
        assert body["status"] == "issued"
        assert body["policy_no"], "a document without a number is not a document"
        assert body["document"]["filename"].endswith(".pdf")
        assert body["document"]["size"] > 1000, "a PDF of a few bytes is a header, not a document"
        assert body["document"]["checksum"]

        download = await client.get(f"/api/v1/policies/{policy['id']}/document", headers=mgr)
        assert download.status_code == 200
        assert download.headers["content-type"].startswith("application/pdf")
        assert download.content.startswith(b"%PDF-")
        assert download.headers.get("x-document-checksum")

        # the status is not only in the answer: the list agrees
        listed = (await client.get("/api/v1/policies", headers=mgr, params={"page_size": 50})).json()
        row = next(item for item in listed["items"] if item["id"] == policy["id"])
        assert row["status"] == "issued"
        assert row["policy_no"] == body["policy_no"]


async def test_issuing_twice_is_a_conflict_not_a_second_document(client, ids) -> None:
    async with login(client, ADMIN) as admin, login(client, MANAGER) as mgr:
        await _enable_phase2(admin, client)
        policy = await _new_policy(client, mgr, client_id=ids["clients"]["باء"])
        first = await client.post(f"/api/v1/policies/{policy['id']}/issue", headers=mgr)
        assert first.status_code == 200
        again = await client.post(f"/api/v1/policies/{policy['id']}/issue", headers=mgr)
        assert again.status_code == 409, again.text
        assert again.json()["code"] == "conflict"
        assert again.json()["details"]["policy_no"] == first.json()["policy_no"]


async def test_a_rep_cannot_issue_and_a_stranger_cannot_read_the_file(client, ids) -> None:
    async with login(client, ADMIN) as admin, login(client, MANAGER) as mgr:
        await _enable_phase2(admin, client)
        policy = await _new_policy(client, mgr, client_id=ids["clients"]["ألف"])
        assert (await client.post(f"/api/v1/policies/{policy['id']}/issue", headers=mgr)).status_code == 200
    async with login(client, REP) as rep:
        # issuing is policies.manage; a rep does not hold it (403 before the flag is even consulted)
        assert (await client.post(f"/api/v1/policies/{policy['id']}/issue", headers=rep)).status_code == 403
        # and the document of a client outside their scope is "not here"
        assert (await client.get(f"/api/v1/policies/{policy['id']}/document", headers=rep)).status_code in (403, 404)


async def test_the_document_paths_are_closed_while_phase2_is_off(client, ids) -> None:
    async with login(client, ADMIN) as admin, login(client, MANAGER) as mgr:
        await _enable_phase2(admin, client)
        policy = await _new_policy(client, mgr, client_id=ids["clients"]["ألف"])
        await client.patch("/api/v1/feature-flags/phase2.enabled", headers=admin, json={"enabled": False})
        try:
            blocked = await client.post(f"/api/v1/policies/{policy['id']}/issue", headers=mgr)
            assert blocked.status_code == 503
            assert blocked.json()["code"] == "feature_disabled"
            assert (await client.get(f"/api/v1/policies/{policy['id']}/document", headers=mgr)).status_code == 503
        finally:
            await client.patch("/api/v1/feature-flags/phase2.enabled", headers=admin, json={"enabled": True})


async def test_an_unknown_policy_is_a_404_on_both_paths(client) -> None:
    async with login(client, ADMIN) as admin, login(client, MANAGER) as mgr:
        await _enable_phase2(admin, client)
        ghost = str(uuid.uuid4())
        assert (await client.post(f"/api/v1/policies/{ghost}/issue", headers=mgr)).status_code == 404
        assert (await client.get(f"/api/v1/policies/{ghost}/document", headers=mgr)).status_code == 404


def test_arabic_values_are_shaped_and_latin_ones_are_not() -> None:
    """A regression guard for the first render of this document, which printed «شركة ألف» reversed.

    PDF text is not readable from the bytes without a parser, so the two pure helpers that decide *how* a
    value is drawn are tested directly: Arabic goes through shaping, and `BH-2026-0007` must not.
    """
    from app.services.documents import _plan, _value

    shaped = _value("شركة ألف للتوزيع")
    assert shaped != "شركة ألف للتوزيع", "Arabic values must be shaped + reordered before drawString"
    assert _value("BH-2026-0007") == "BH-2026-0007", "a policy number is not Arabic and must not be reshaped"
    assert _value("275,000.00 EGP") == "275,000.00 EGP"
    assert _plan("monthly") == "شهري"
    assert _plan("quarterly") == "ربع سنوي"
    assert _plan(None) == "—"
    assert _plan("something-new") == "something-new"  # an unknown plan is printed as it arrived, never dropped
