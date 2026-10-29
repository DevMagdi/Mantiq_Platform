"""Issuing case detail + document checklist (spec §7 `/issuing-cases/{id}/documents`).

These two routes exist so the pipeline screen can ask for one case instead of pulling 100 rows, and so
the same scope rule covers both. The scoped-manager case below is the important one: a user whose net is
one client must not read the other client's checklist — 404, not 403, so existence is not leaked (§6).
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.db import get_sessionmaker
from app.core.security import hash_password
from app.domain.enums import IssuingDocumentStatus, IssuingStage, ScopeType
from app.models.auth import Role, User, UserScope
from app.models.clients import Client
from app.models.masters import FeatureFlag
from app.models.phase2 import IssuingCase, IssuingDocument
from sqlalchemy import select

from tests.conftest import PASSWORD
from tests.helpers import login

ADMIN = "admin@brokerhub.test"
SCOPED = "scoped@brokerhub.test"


async def _enable_phase2() -> None:
    async with get_sessionmaker()() as session:
        row = (await session.execute(select(FeatureFlag).where(FeatureFlag.key == "phase2.enabled"))).scalar_one_or_none()
        if row is None:
            session.add(FeatureFlag(key="phase2.enabled", enabled=True, description="Phase 2 screens"))
        else:
            row.enabled = True
        await session.commit()


async def _seed_cases() -> tuple[str, str]:
    """One case per fixture client, plus a manager whose scope is pinned to شركة باء."""
    maker = get_sessionmaker()
    async with maker() as session:
        clients = {c.name: c.id for c in (await session.execute(select(Client))).scalars()}
        case_a = IssuingCase(
            client_id=clients["شركة ألف"],
            stage=IssuingStage.DOCS_REVIEW,
            headcount=25,
            premium_amount=180_000,
            started_at=datetime(2026, 10, 1, tzinfo=UTC),
        )
        case_b = IssuingCase(client_id=clients["شركة باء"], stage=IssuingStage.ISSUED, headcount=8)
        session.add_all([case_a, case_b])
        await session.flush()
        session.add_all(
            [
                IssuingDocument(case_id=case_a.id, name="كشف أسماء المؤمَّن عليهم", status=IssuingDocumentStatus.APPROVED),
                IssuingDocument(case_id=case_a.id, name="صورة رخصة النشاط", status=IssuingDocumentStatus.MISSING),
                IssuingDocument(case_id=case_b.id, name="إقرار الحالة الصحية", status=IssuingDocumentStatus.UPLOADED),
            ]
        )
        if (await session.execute(select(User).where(User.email == SCOPED))).scalar_one_or_none() is None:
            role = (await session.execute(select(Role).where(Role.code == "sales_manager"))).scalar_one()
            scoped = User(email=SCOPED, password_hash=hash_password(PASSWORD), full_name="Scoped Manager", role_id=role.id)
            session.add(scoped)
            await session.flush()
            session.add(UserScope(user_id=scoped.id, scope_type=ScopeType.CLIENT, scope_id=clients["شركة باء"]))
        await session.commit()
        return str(case_a.id), str(case_b.id)


async def test_phase2_flag_gates_both_new_routes(client) -> None:
    """A disabled phase answers 503 on every read of the module, including the new ones (ADR-008)."""
    case_id, _ = await _seed_cases()
    async with login(client, ADMIN) as auth:
        for path in (f"/api/v1/issuing-cases/{case_id}", f"/api/v1/issuing-cases/{case_id}/documents"):
            response = await client.get(path, headers=auth)
            if response.status_code == 200:  # another test already opened the flag
                break
            assert response.status_code == 503, path
            assert response.json()["code"] == "feature_disabled"


async def test_detail_and_checklist_after_enabling_the_flag(client) -> None:
    await _enable_phase2()
    case_id, _ = await _seed_cases()
    async with login(client, ADMIN) as auth:
        detail = await client.get(f"/api/v1/issuing-cases/{case_id}", headers=auth)
        docs = await client.get(f"/api/v1/issuing-cases/{case_id}/documents", headers=auth)
        missing = await client.get(f"/api/v1/issuing-cases/{_UNKNOWN}", headers=auth)
        missing_docs = await client.get(f"/api/v1/issuing-cases/{_UNKNOWN}/documents", headers=auth)

    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["stage"] == "docs_review"
    assert body["headcount"] == 25
    assert body["client"] == "شركة ألف"
    assert len(body["documents"]) == 2
    assert docs.status_code == 200, docs.text
    checklist = {row["name"]: row["status"] for row in docs.json()}
    assert checklist["صورة رخصة النشاط"] == "missing"
    assert checklist["كشف أسماء المؤمَّن عليهم"] == "approved"
    assert all("id" in row for row in docs.json())
    assert missing.status_code == 404
    assert missing_docs.status_code == 404


_UNKNOWN = "00000000-0000-0000-0000-000000000000"


async def test_checklist_follows_the_same_scope_as_the_pipeline(client) -> None:
    await _enable_phase2()
    case_a, case_b = await _seed_cases()
    async with login(client, SCOPED) as auth:
        listed = await client.get("/api/v1/issuing-cases", headers=auth)
        own = await client.get(f"/api/v1/issuing-cases/{case_b}/documents", headers=auth)
        foreign_detail = await client.get(f"/api/v1/issuing-cases/{case_a}", headers=auth)
        foreign_docs = await client.get(f"/api/v1/issuing-cases/{case_a}/documents", headers=auth)

    assert listed.status_code == 200
    ids = {row["id"] for row in listed.json()}
    assert case_b in ids
    assert case_a not in ids
    assert own.status_code == 200
    assert [row["name"] for row in own.json()] == ["إقرار الحالة الصحية"]
    assert foreign_detail.status_code == 404
    assert foreign_docs.status_code == 404
