"""Redistribution: the pool is drained server-side, in one transaction, with the timeline to prove it.

These tests exist because the first version of the button looped over one page of `/leads` on the client —
which silently meant "at most 50", while the card had counted every unassigned lead.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.core.db import get_sessionmaker
from app.core.security import hash_password
from app.domain.enums import ActivityType, ClientType, LeadStatus
from app.models.auth import Role, User
from app.models.leads import Lead, LeadActivity
from sqlalchemy import select

from tests.helpers import login

MANAGER = "manager@brokerhub.test"
REP_A = "rep_a@brokerhub.test"
PASSWORD = "Test-Pass-2026"


async def _pool(size: int = 3) -> list[dict[str, Any]]:
    """Unassigned leads, written straight to the DB — the API is what the rest of this file tests."""
    from app.core.db import get_sessionmaker as _maker

    async with _maker()() as session:
        rows = [
            Lead(
                name=f"في القائمة {n}",
                type=ClientType.COMPANY,
                status=LeadStatus.NEW,
                mobile=f"+201099{n:06d}",
                assigned_to=None,
            )
            for n in range(size)
        ]
        session.add_all(rows)
        # and one finished lead with no owner: the pool must not touch it, and must not list it
        session.add(Lead(name="منتهية", type=ClientType.COMPANY, status=LeadStatus.WON, mobile="+201099990000"))
        await session.commit()
        return [{"id": str(row.id), "name": row.name} for row in rows]


async def _unassigned(client: Any, auth: dict[str, str]) -> list[str]:
    body = (await client.get("/api/v1/leads?unassigned=true&page_size=100", headers=auth)).json()
    return [row["name"] for row in body["items"]]


async def test_the_whole_pool_moves_at_once_and_the_timeline_records_it(client, ids: dict[str, dict[str, str]]) -> None:
    await _pool(3)
    rep_a = ids["users"]["rep_a"]
    async with login(client, MANAGER) as auth:
        before = len(await _unassigned(client, auth))
        assert before == 3

        # the workload moved with the leads: the card grows by exactly what the pool handed over
        # (rep_a already owns leads in the fixture graph, so the claim is a delta and not a magic number)
        before = next(row for row in (await client.get("/api/v1/sales/team", headers=auth)).json() if row["rep_id"] == rep_a)
        assert before["open_leads"] == 1

        result = await client.post("/api/v1/leads/assign-bulk", json={"unassigned_only": True, "to": [rep_a]}, headers=auth)
        assert result.status_code == 200, result.text
        moved = result.json()
        assert moved["assigned"] == 3
        assert moved["per_rep"] == {rep_a: 3}
        assert moved["skipped"] == []

        assert await _unassigned(client, auth) == []  # the pool is empty, not "one page shorter"
        mine = (await client.get(f"/api/v1/leads?assigned_to={rep_a}&page_size=100", headers=auth)).json()["items"]
        assert sum(1 for row in mine if row["name"].startswith("في القائمة")) == 3

        team = (await client.get("/api/v1/sales/team", headers=auth)).json()
        card = next(row for row in team if row["rep_id"] == rep_a)
        assert card["open_leads"] == before["open_leads"] + 3, "the redistribution did not reach the workload card"

        for lead in mine:
            if not lead["name"].startswith("في القائمة"):
                continue
            activities = (await client.get(f"/api/v1/leads/{lead['id']}/activities", headers=auth)).json()
            entry = next(row for row in activities if row["type"] == "assignment")
            assert entry["meta"]["to"] == rep_a
            assert entry["meta"]["via"] == "redistribute"
            assert entry["actor_name"] == "Manager"
            # the line a manager reads, not two uuids they have to look up — the ids stay in `meta`
            assert entry["body"] == "redistributed: unassigned → Rep A", entry["body"]
            assert str(rep_a) not in entry["body"]


async def test_finished_leads_are_not_part_of_the_pool(client, ids: dict[str, dict[str, str]]) -> None:
    await _pool(1)
    async with login(client, MANAGER) as auth:
        names = await _unassigned(client, auth)
        assert names == ["في القائمة 0"]
        await client.post("/api/v1/leads/assign-bulk", json={"unassigned_only": True, "to": [ids["users"]["rep_a"]]}, headers=auth)
        won = (await client.get("/api/v1/leads?q=منتهية", headers=auth)).json()["items"]
        assert won[0]["assignee_id"] is None, "a won lead was handed to a rep by the redistribution"


async def test_two_reps_split_the_pool_in_the_order_they_were_picked(client, ids: dict[str, dict[str, str]]) -> None:
    await _pool(3)
    async with login(client, MANAGER) as auth:
        result = await client.post(
            "/api/v1/leads/assign-bulk",
            json={"unassigned_only": True, "to": [ids["users"]["rep_b"], ids["users"]["rep_a"]], "reason": "توزيع متوازن"},
            headers=auth,
        )
        moved = result.json()
        assert moved["assigned"] == 3
        assert sorted(moved["per_rep"].values()) == [1, 2]
        first = (await client.get(f"/api/v1/leads?assigned_to={ids['users']['rep_b']}&page_size=100", headers=auth)).json()["items"]
        note = next(row for row in first if row["name"].startswith("في القائمة"))
        activities = (await client.get(f"/api/v1/leads/{note['id']}/activities", headers=auth)).json()
        entry = next(row for row in activities if row["type"] == "assignment")
        assert "توزيع متوازن" in entry["body"], entry["body"]
        assert entry["body"].startswith("redistributed: unassigned → "), entry["body"]
        assert any(name in entry["body"] for name in ("Rep A", "Rep B")), "the round-robin line does not say who got the lead"


async def test_a_selection_can_be_reassigned_and_repetition_is_reported_not_repeated(client, ids: dict[str, dict[str, str]]) -> None:
    rows = await _pool(2)
    async with login(client, MANAGER) as auth:
        picked = [row["id"] for row in rows]
        first = await client.post("/api/v1/leads/assign-bulk", json={"lead_ids": picked, "to": [ids["users"]["rep_a"]]}, headers=auth)
        assert first.json()["assigned"] == 2
        again = await client.post("/api/v1/leads/assign-bulk", json={"lead_ids": picked, "to": [ids["users"]["rep_a"]]}, headers=auth)
        second = again.json()
        assert second["assigned"] == 0
        assert {row["reason"] for row in second["skipped"]} == {"already_assigned"}


async def test_a_stranger_in_the_list_is_skipped_without_failing_the_batch(client, ids: dict[str, dict[str, str]]) -> None:
    rows = await _pool(2)
    ghost = str(uuid4())
    async with login(client, MANAGER) as auth:
        result = await client.post(
            "/api/v1/leads/assign-bulk", json={"lead_ids": [rows[0]["id"], ghost], "to": [ids["users"]["rep_a"]]}, headers=auth
        )
        body = result.json()
        assert body["assigned"] == 1
        assert body["skipped"] == [{"lead_id": ghost, "reason": "not_found"}]


async def test_an_unknown_rep_moves_nothing_at_all(client, ids: dict[str, dict[str, str]]) -> None:
    """The rep list is validated first: a typo must not leave half the pool on the wrong desk."""
    rows = await _pool(2)
    async with login(client, MANAGER) as auth:
        before = sorted(await _unassigned(client, auth))
        result = await client.post(
            "/api/v1/leads/assign-bulk", json={"lead_ids": [row["id"] for row in rows], "to": [str(uuid4())]}, headers=auth
        )
        assert result.status_code == 422, result.text
        assert sorted(await _unassigned(client, auth)) == before
        inactive = await _make_rep("ghost@brokerhub.test", active=False)
        refused = await client.post("/api/v1/leads/assign-bulk", json={"lead_ids": [rows[0]["id"]], "to": [inactive]}, headers=auth)
        assert refused.status_code == 422, refused.text
        assert sorted(await _unassigned(client, auth)) == before


async def test_an_inactive_rep_card_is_named_in_the_error(client, ids: dict[str, dict[str, str]]) -> None:
    ghost = await _make_rep(f"inactive-{uuid4().hex[:8]}@brokerhub.test", active=False)
    await _pool(1)
    async with login(client, MANAGER) as auth:
        refused = await client.post("/api/v1/leads/assign-bulk", json={"unassigned_only": True, "to": [ghost]}, headers=auth)
        assert refused.status_code == 422
        assert refused.json()["details"]["user_id"] == ghost


async def test_a_rep_cannot_redistribute_anything(client, ids: dict[str, dict[str, str]]) -> None:
    await _pool(1)
    async with login(client, REP_A) as auth:
        refused = await client.post(
            "/api/v1/leads/assign-bulk", json={"unassigned_only": True, "to": [ids["users"]["rep_a"]]}, headers=auth
        )
        assert refused.status_code == 403, refused.text
        assert set(refused.json()["details"]["missing"]) == {"leads.assign"}
    assert sorted(await _unassigned_names()) == ["في القائمة 0"]


async def _unassigned_names() -> list[str]:
    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(Lead.name).where(Lead.assigned_to.is_(None), Lead.status != LeadStatus.WON))).scalars().all()
        return list(rows)


async def _make_rep(email: str, *, active: bool, deleted: bool = False) -> str:
    async with get_sessionmaker()() as session:
        role_id = (await session.execute(select(Role.id).where(Role.code == "sales_rep"))).scalar_one()
        user = User(
            email=email,
            full_name=f"Ghost {email[:6]}",
            password_hash=hash_password(PASSWORD),
            role_id=role_id,
            is_active=active,
            deleted_at=datetime.now(UTC) if deleted else None,
        )
        session.add(user)
        await session.commit()
        return str(user.id)


async def test_the_board_keeps_a_suspended_seat_and_marks_it(client, ids: dict[str, dict[str, str]]) -> None:
    """`is_active` is on the workload row so the picker can drop a seat the server would refuse, while the
    board itself still counts the leads that seat is holding — hiding them would hide the work."""
    ghost = await _make_rep(f"suspended-{uuid4().hex[:8]}@brokerhub.test", active=False)
    gone = await _make_rep(f"deleted-{uuid4().hex[:8]}@brokerhub.test", active=True, deleted=True)
    async with login(client, MANAGER) as auth:
        rows = (await client.get("/api/v1/sales/team", headers=auth)).json()
        card = next(row for row in rows if row["rep_id"] == ghost)
        assert card["is_active"] is False
        assert card["open_leads"] == 0
        seeded = next(row for row in rows if row["rep_id"] == ids["users"]["rep_a"])
        assert seeded["is_active"] is True
        assert gone not in {row["rep_id"] for row in rows}, "a deleted account is not a workload card"


async def test_the_two_selectors_are_exclusive(client, ids: dict[str, dict[str, str]]) -> None:
    """`lead_ids` *or* `unassigned_only` — never both, so a page boundary cannot decide silently."""
    rows = await _pool(1)
    async with login(client, MANAGER) as auth:
        both = await client.post(
            "/api/v1/leads/assign-bulk",
            json={"lead_ids": [rows[0]["id"]], "unassigned_only": True, "to": [ids["users"]["rep_a"]]},
            headers=auth,
        )
        assert both.status_code == 422, both.text
        neither = await client.post("/api/v1/leads/assign-bulk", json={"to": [ids["users"]["rep_a"]]}, headers=auth)
        assert neither.status_code == 422
        empty_targets = await client.post("/api/v1/leads/assign-bulk", json={"unassigned_only": True, "to": []}, headers=auth)
        assert empty_targets.status_code == 422


async def test_bulk_assignment_writes_one_audit_row_per_lead(client, ids: dict[str, dict[str, str]]) -> None:
    from app.models.requests import AuditLog

    await _pool(2)
    async with login(client, MANAGER) as auth:
        await client.post("/api/v1/leads/assign-bulk", json={"unassigned_only": True, "to": [ids["users"]["rep_a"]]}, headers=auth)
    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(AuditLog).where(AuditLog.action == "lead.assigned"))).scalars().all()
        assert len(rows) == 2
        assert {row.after["assigned_to"] for row in rows} == {ids["users"]["rep_a"]}
        summary = (await session.execute(select(AuditLog).where(AuditLog.action == "lead.assigned_bulk"))).scalars().one()
        assert summary.after["assigned"] == 2
        activities = (await session.execute(select(LeadActivity).where(LeadActivity.type == ActivityType.ASSIGNMENT))).scalars().all()
        assert len(activities) == 2


async def test_a_customer_service_seat_cannot_receive_a_lead(client, ids: dict[str, dict[str, str]]) -> None:
    """The redistribute dialog lists office seats; only a rep seat may actually take a lead.

    R3 finding: `_require_rep` checked existence and `is_active` only, so a lead could be parked on a
    customer-service desk. It then left the unassigned pool, the manager was told "1 moved", and no sales
    board (scope: `assigned_to == me`) and no CS queue showed it again.
    """
    await _pool(2)
    cs = ids["users"]["cs"]
    async with login(client, MANAGER) as auth:
        before = (await client.get("/api/v1/leads?unassigned=true&page_size=50", headers=auth)).json()["total"]
        refused = await client.post("/api/v1/leads/assign-bulk", headers=auth, json={"unassigned_only": True, "to": [cs]})
        assert refused.status_code == 422, refused.text
        assert refused.json()["code"] == "validation_failed"
        assert refused.json()["details"]["required"] == "leads.view_own"
        after = (await client.get("/api/v1/leads?unassigned=true&page_size=50", headers=auth)).json()["total"]
        assert after == before, "a refused target must not move half the pool"
        # a mixed target list is refused before anything moves as well
        mixed = await client.post(
            "/api/v1/leads/assign-bulk",
            headers=auth,
            json={"unassigned_only": True, "to": [ids["users"]["rep_a"], cs]},
        )
        assert mixed.status_code == 422, mixed.text
        assert (await client.get("/api/v1/leads?unassigned=true&page_size=50", headers=auth)).json()["total"] == before


async def test_the_roster_says_which_seats_can_hold_leads(client) -> None:
    """One rule, served: the picker filters on `accepts_leads` instead of guessing from the role name."""
    async with login(client, MANAGER) as auth:
        body = (await client.get("/api/v1/sales-team/members", headers=auth)).json()
    flags = {row["role"]: row["accepts_leads"] for row in body["members"]}
    assert flags["sales_rep"] is True
    assert flags["customer_service"] is False
    assert all("accepts_leads" in row for row in body["members"])


async def test_the_redistribution_targets_are_listed_in_a_stable_order(client) -> None:
    """`/sales/team` had no ORDER BY, so "the first rep" was whatever the planner returned first.

    The e2e drove the dialog from the first checkbox and, in the same run, had created a rep minute
    earlier — the lead went to a seat the test never looked at. The list the dialog renders is ordered now.
    """
    async with login(client, MANAGER) as auth:
        rows = (await client.get("/api/v1/sales/team", headers=auth)).json()
    names = [row["name"] for row in rows]
    assert names == sorted(names), f"the rep list is not deterministic: {names}"
    assert {row["is_active"] for row in rows}  # suspended seats stay listed, marked (`is_active` False)
