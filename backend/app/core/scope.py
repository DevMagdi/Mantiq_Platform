"""The single central place where data isolation is decided (spec §6 "طبقة واحدة مركزية").

Every repository query receives a Scope and cannot opt out of it. Out-of-scope ids are *not*
forbidden — they are absent (404), so the API cannot be used to enumerate other reps' data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from app.domain import permissions as P

if TYPE_CHECKING:
    from app.models.auth import User

Mode = Literal["all", "owner", "seeded", "portal_client", "none"]


@dataclass(frozen=True)
class Scope:
    mode: Mode = "none"
    user_id: UUID | None = None
    client_ids: frozenset[UUID] = field(default_factory=frozenset)
    insurer_ids: frozenset[UUID] = field(default_factory=frozenset)
    portal_client_id: UUID | None = None
    perms: frozenset[str] = field(default_factory=frozenset)
    role_code: str = ""

    # ---- convenience predicates -------------------------------------------------
    @property
    def is_all(self) -> bool:
        return self.mode == "all"

    @property
    def is_portal(self) -> bool:
        return self.mode == "portal_client"

    @property
    def is_owner_only(self) -> bool:
        return self.mode == "owner"

    def can(self, perm: str) -> bool:
        return perm in self.perms

    def can_any(self, *perms: str) -> bool:
        return any(p in self.perms for p in perms)

    # ---- RLS second net ---------------------------------------------------------
    def session_settings(self) -> dict[str, str]:
        """GUCs consumed by the PostgreSQL RLS policies (migration 0002, ADR-003).

        They are published on every request by `core/rls.py` (an `after_begin` hook on the route's
        session), so a query that forgot its repository filter is still confined to the same rows.
        A `Scope` that never reaches the database — a worker, a health check — simply leaves the
        settings unset, which the policies read as "system access". See ADR-003 and ADR-014.

        `app.bypass_rls=on` is set only for scopes that are intentionally unrestricted (managers,
        admins, system jobs). Owner/seeded/portal scopes keep it off, so RLS re-checks the row.
        """
        if self.mode == "all":
            return {"app.bypass_rls": "on", "app.scope_user_id": "null"}
        if self.mode == "owner" and self.user_id is not None:
            return {"app.bypass_rls": "off", "app.scope_user_id": str(self.user_id)}
        if self.mode == "portal_client" and self.portal_client_id is not None:
            return {
                "app.bypass_rls": "off",
                "app.scope_user_id": "null",
                "app.scope_client_id": str(self.portal_client_id),
            }
        return {"app.bypass_rls": "off", "app.scope_user_id": str(self.user_id or "")}


def build_scope(user: User) -> Scope:
    perms = frozenset(user.permission_codes)
    role_code = user.role.code if user.role else ""

    # Portal users are pinned to exactly one client row — before any staff logic.
    link = user.portal_link
    if link is not None and (user.role and user.role.code.startswith("client_")):
        return Scope(mode="portal_client", user_id=user.id, portal_client_id=link.client_id, perms=perms, role_code=role_code)
    if link is not None and not (perms & {P.LEADS_VIEW_OWN, P.LEADS_VIEW_ALL, P.REQUESTS_VIEW, P.CLIENTS_VIEW_OWN, P.CLIENTS_VIEW_ALL}):
        return Scope(mode="portal_client", user_id=user.id, portal_client_id=link.client_id, perms=perms, role_code=role_code)

    client_ids = frozenset(s.scope_id for s in user.scopes if s.scope_type.value == "client")
    insurer_ids = frozenset(s.scope_id for s in user.scopes if s.scope_type.value == "insurer")

    view_all = {P.LEADS_VIEW_ALL, P.CLIENTS_VIEW_ALL} & perms
    view_own = {P.LEADS_VIEW_OWN, P.CLIENTS_VIEW_OWN} & perms

    if client_ids or insurer_ids:
        # Staff with an explicit scope list (CS / account manager): union of both nets.
        return Scope(
            mode="seeded",
            user_id=user.id,
            client_ids=client_ids,
            insurer_ids=insurer_ids,
            perms=perms,
            role_code=role_code,
        )
    if not view_all and view_own:
        return Scope(mode="owner", user_id=user.id, perms=perms, role_code=role_code)
    if view_all or perms:
        return Scope(mode="all", user_id=user.id, perms=perms, role_code=role_code)
    return Scope(mode="none", user_id=user.id, perms=perms, role_code=role_code)
