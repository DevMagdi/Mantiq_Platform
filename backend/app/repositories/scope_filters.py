"""Scope → SQL predicates. Imported by every repository; the isolation guarantee lives here.

Rules (spec §6):
  owner         → leads.assigned_to = me · clients.account_owner_id = me
  seeded        → union of `user_scopes` (client ids) and/or insurer ids
  portal_client → the single linked client (internal fields stripped at the schema layer)
  all           → no extra predicate (admin / sales manager)
  none          → always-empty predicate

`with_scope()` is used for list *and* by-id lookups, which is what makes "404 for foreign rows"
hold for search, counters, Kanban boards and exports without writing the rule per endpoint.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy import Select, select

from app.core.scope import Scope
from app.models.clients import Client
from app.models.leads import Lead
from app.models.requests import ServiceRequest

_NONE_MATCH = sa.false()


def _client_id_in(ids: frozenset[Any]) -> Any:
    if not ids:
        return _NONE_MATCH
    return Client.id.in_(ids)


def lead_predicate(scope: Scope) -> Any:
    if scope.mode == "all":
        return sa.true()
    if scope.mode == "owner":
        return Lead.assigned_to == scope.user_id
    if scope.mode == "seeded":
        # Staff with an explicit scope list are not lead owners unless they also own the pipeline.
        return (
            sa.true()
            if scope.can_any(
                "leads.view_all",
            )
            else _NONE_MATCH
        )
    return _NONE_MATCH  # portal / none


def client_predicate(scope: Scope) -> Any:
    if scope.mode == "all":
        return sa.true()
    if scope.mode == "owner":
        return Client.account_owner_id == scope.user_id
    if scope.mode == "seeded":
        return _client_id_in(scope.client_ids) if scope.client_ids else sa.true()
    if scope.mode == "portal_client":
        return Client.id == scope.portal_client_id
    return _NONE_MATCH


def _visible_client_ids(scope: Scope) -> Any:
    """Subquery shared by requests, insured persons, calls and exports."""
    if scope.mode == "owner":
        return select(Client.id).where(Client.account_owner_id == scope.user_id)
    if scope.mode == "seeded":
        return select(Client.id).where(Client.id.in_(scope.client_ids)) if scope.client_ids else select(Client.id)
    return select(Client.id).where(_NONE_MATCH)


def request_predicate(scope: Scope) -> Any:
    """Requests inherit their client's visibility; unknown-caller rows are staff-wide only."""
    if scope.mode == "all":
        return sa.true()
    if scope.mode in {"owner", "seeded"}:
        return sa.and_(
            ServiceRequest.client_id.is_not(None),
            ServiceRequest.client_id.in_(_visible_client_ids(scope)),
        )
    if scope.mode == "portal_client":
        return ServiceRequest.client_id == scope.portal_client_id
    return _NONE_MATCH


def insured_predicate(scope: Scope) -> Any:
    from app.models.clients import InsuredPerson

    if scope.mode == "all":
        return sa.true()
    if scope.mode in {"owner", "seeded"}:
        return InsuredPerson.client_id.in_(_visible_client_ids(scope))
    if scope.mode == "portal_client":
        return InsuredPerson.client_id == scope.portal_client_id
    return _NONE_MATCH


def policy_predicate(scope: Scope) -> Any:
    from app.models.phase2 import Policy

    if scope.mode == "all":
        return sa.true()
    if scope.mode in {"owner", "seeded"}:
        return Policy.client_id.in_(_visible_client_ids(scope))
    if scope.mode == "portal_client":
        return Policy.client_id == scope.portal_client_id
    return _NONE_MATCH


def issuing_case_predicate(scope: Scope) -> Any:
    """Issuing cases follow their client, exactly like policies (spec §6: one central rule)."""
    from app.models.phase2 import IssuingCase

    if scope.mode == "all":
        return sa.true()
    if scope.mode in {"owner", "seeded"}:
        return IssuingCase.client_id.in_(_visible_client_ids(scope))
    if scope.mode == "portal_client":
        return IssuingCase.client_id == scope.portal_client_id
    return _NONE_MATCH


def _visible_policy_ids(scope: Scope) -> Any:
    """The policies a scope may see — commissions and settlements hang off them, not off a client column."""
    from app.models.phase2 import Policy

    if scope.mode == "owner":
        return select(Policy.id).where(Policy.client_id.in_(_visible_client_ids(scope)))
    if scope.mode == "seeded":
        return select(Policy.id).where(Policy.client_id.in_(scope.client_ids)) if scope.client_ids else select(Policy.id)
    return select(Policy.id).where(_NONE_MATCH)


def ledger_predicate(scope: Scope) -> Any:
    """A ledger row is visible when the *client* it belongs to is (ADR-030 §6).

    Office-level rows (an expense with no client) are therefore manager-only: a rep's scope has no client
    to match them on, and "half the office's expenses" is not a thing a rep should ever see.
    """
    from app.models.finance import LedgerEntry

    if scope.mode == "all":
        return sa.true()
    if scope.mode in {"owner", "seeded"}:
        return LedgerEntry.client_id.in_(_visible_client_ids(scope))
    if scope.mode == "portal_client":
        return LedgerEntry.client_id == scope.portal_client_id
    return _NONE_MATCH


def commission_predicate(scope: Scope) -> Any:
    from app.models.phase2 import Commission

    if scope.mode == "all":
        return sa.true()
    if scope.mode in {"owner", "seeded"}:
        return Commission.policy_id.in_(_visible_policy_ids(scope))
    return _NONE_MATCH


PREDICATES: dict[str, Any] = {
    "lead": lead_predicate,
    "issuing_case": issuing_case_predicate,
    "client": client_predicate,
    "request": request_predicate,
    "insured": insured_predicate,
    "policy": policy_predicate,
    "ledger": ledger_predicate,
    "commission": commission_predicate,
}

_SOFT_DELETE_TABLES: dict[str, Any] = {"lead": Lead, "client": Client}


def with_scope(stmt: Select[Any], scope: Scope, kind: str) -> Select[Any]:
    """The single entry point: every list and every by-id lookup passes through here."""
    table = _SOFT_DELETE_TABLES.get(kind)
    if table is not None:
        stmt = stmt.where(table.deleted_at.is_(None))
    if scope.mode != "all":
        stmt = stmt.where(PREDICATES[kind](scope))
    return stmt


async def row_visible(session: Any, stmt: Select[Any], scope: Scope, kind: str) -> Any:
    """By-id lookup helper: returns the row or None (callers raise NotFound → 404)."""
    return (await session.execute(with_scope(stmt, scope, kind).limit(1))).scalar_one_or_none()
