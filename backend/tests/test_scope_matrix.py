"""The central isolation rule (spec §6) pinned at unit level, plus the RLS GUCs it feeds."""

from __future__ import annotations

from uuid import UUID, uuid4

from app.core.scope import Scope, build_scope
from app.domain import permissions as P
from app.models.auth import ClientPortalUser, Role, ScopeType, User, UserScope
from sqlalchemy import select
from sqlalchemy.orm import selectinload

# ---- pure Scope behaviour ---------------------------------------------------------------


def test_predicates_track_permissions_not_roles() -> None:
    scope = Scope(mode="owner", user_id=uuid4(), perms=frozenset({P.LEADS_VIEW_OWN}))
    assert scope.is_owner_only and not scope.is_all and not scope.is_portal
    assert scope.can(P.LEADS_VIEW_OWN) and not scope.can(P.LEADS_VIEW_ALL)
    assert scope.can_any(P.LEADS_VIEW_ALL, P.LEADS_VIEW_OWN) is True
    assert scope.can_any(P.LEADS_VIEW_ALL, P.CALLS_LISTEN) is False


def test_rls_gucs_bypass_only_for_unrestricted_scopes() -> None:
    user_id = uuid4()
    client_id = uuid4()
    assert Scope(mode="all", user_id=user_id).session_settings() == {"app.bypass_rls": "on", "app.scope_user_id": "null"}
    owner = Scope(mode="owner", user_id=user_id).session_settings()
    assert owner["app.bypass_rls"] == "off"
    assert owner["app.scope_user_id"] == str(user_id)
    portal = Scope(mode="portal_client", portal_client_id=client_id).session_settings()
    assert portal["app.bypass_rls"] == "off"
    assert portal["app.scope_client_id"] == str(client_id)
    assert "app.scope_user_id" in portal
    # an unauthenticated/permissionless caller must never look like a bypass
    assert Scope().session_settings()["app.bypass_rls"] == "off"


# ---- build_scope over the seeded graph ----------------------------------------------------


async def _user(session, email: str) -> User:
    stmt = (
        select(User)
        .where(User.email == email)
        .options(
            selectinload(User.role).selectinload(Role.permissions),
            selectinload(User.scopes),
            selectinload(User.portal_link),
        )
    )
    return (await session.execute(stmt)).scalar_one()


async def test_roles_map_to_the_expected_modes(session) -> None:
    rep = build_scope(await _user(session, "rep_a@brokerhub.test"))
    assert rep.mode == "owner" and rep.user_id is not None
    manager = build_scope(await _user(session, "manager@brokerhub.test"))
    assert manager.mode == "all" and manager.session_settings()["app.bypass_rls"] == "on"
    admin = build_scope(await _user(session, "admin@brokerhub.test"))
    assert admin.can(P.USERS_MANAGE) and admin.can(P.SETTINGS_MANAGE)
    cs = build_scope(await _user(session, "cs@brokerhub.test"))
    assert cs.can(P.REQUESTS_VIEW) and not cs.can(P.LEADS_VIEW_ALL)


async def test_portal_users_are_pinned_to_their_company(session, ids) -> None:
    owner = await _user(session, "owner@brokerhub.test")
    assert isinstance(owner.portal_link, ClientPortalUser)
    scope = build_scope(owner)
    assert scope.mode == "portal_client"
    assert scope.portal_client_id == UUID(ids["clients"]["ألف"])
    assert scope.session_settings()["app.scope_client_id"] == ids["clients"]["ألف"]


async def test_explicit_scopes_turn_a_staff_account_into_a_seeded_union(session, ids) -> None:
    """An account manager covering two books is neither owner-only nor all-office (spec §6 user_scopes)."""
    cs = await _user(session, "cs@brokerhub.test")
    session.add_all(
        [
            UserScope(user_id=cs.id, scope_type=ScopeType.CLIENT, scope_id=UUID(ids["clients"]["ألف"])),
            UserScope(user_id=cs.id, scope_type=ScopeType.CLIENT, scope_id=UUID(ids["clients"]["باء"])),
        ]
    )
    await session.commit()
    # the identity map still holds the pre-insert row, so drop it before re-reading the scope
    session.expire_all()
    scope = build_scope(await _user(session, "cs@brokerhub.test"))
    assert scope.mode == "seeded"
    assert {str(c) for c in scope.client_ids} == {ids["clients"]["ألف"], ids["clients"]["باء"]}
    assert scope.session_settings()["app.bypass_rls"] == "off"


async def test_a_role_without_permissions_sees_nothing(session) -> None:
    role = Role(code="observer", name="Observer", is_system=False)
    user = User(email="observer@brokerhub.test", password_hash="x", full_name="Observer", role=role)
    session.add_all([role, user])
    await session.commit()

    scope = build_scope(await _user(session, "observer@brokerhub.test"))
    assert scope.mode == "none"
    assert scope.perms == frozenset()
    assert scope.can_any(P.LEADS_VIEW_OWN, P.REQUESTS_VIEW) is False
