"""Route-level aliases: every endpoint composes one of these, so an unscoped route can't be written by accident."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.deps import (
    CurrentUser,
    DbSession,
    PageQuery,
    ScopeDep,
    require_any_perm,
    require_perm,
)
from app.domain import permissions as P
from app.models.auth import User

# Permission aliases (403 for missing perms; Scope still decides 404 for foreign rows).
LeadsView = Annotated[User, Depends(require_any_perm(P.LEADS_VIEW_OWN, P.LEADS_VIEW_ALL))]
LeadsConvert = Annotated[User, Depends(require_perm(P.LEADS_CONVERT))]
LeadsAssign = Annotated[User, Depends(require_perm(P.LEADS_ASSIGN))]
#: Uploading a file into everyone's pipeline is a manager action. `leads.view_all` is exactly the
#: population allowed to see (so to speak, to own) the whole pool — a rep does not hold it and gets a 403.
#: There is deliberately no new permission slug for this: a permission nobody assigns is a permission
#: nobody can grant, and the seed owns `role_permissions`.
LeadsImport = Annotated[User, Depends(require_perm(P.LEADS_VIEW_ALL))]
ClientsView = Annotated[User, Depends(require_any_perm(P.CLIENTS_VIEW_OWN, P.CLIENTS_VIEW_ALL))]
ClientsManage = Annotated[User, Depends(require_perm(P.CLIENTS_MANAGE))]
InsuredManage = Annotated[User, Depends(require_perm(P.INSURED_MANAGE))]
RequestsView = Annotated[User, Depends(require_perm(P.REQUESTS_VIEW))]
RequestsManage = Annotated[User, Depends(require_perm(P.REQUESTS_MANAGE))]
CallsListen = Annotated[User, Depends(require_perm(P.CALLS_LISTEN))]
PoliciesView = Annotated[User, Depends(require_any_perm(P.POLICIES_VIEW, P.POLICIES_MANAGE))]
ReportsView = Annotated[User, Depends(require_perm(P.REPORTS_VIEW))]
ReportsExport = Annotated[User, Depends(require_perm(P.REPORTS_EXPORT))]
SettingsManage = Annotated[User, Depends(require_perm(P.SETTINGS_MANAGE))]
# R10 money layer (ADR-030): reading the ledger, writing to it, and taking a write back are three gates.
# A rep holds none of them, which is what keeps the office cashbox out of a rep's screen — the 403 is the
# isolation, not a hidden button.
FinanceView = Annotated[User, Depends(require_perm(P.FINANCE_VIEW))]
FinancePost = Annotated[User, Depends(require_perm(P.FINANCE_POST))]
FinanceVoid = Annotated[User, Depends(require_perm(P.FINANCE_VOID))]
FinancePeriodClose = Annotated[User, Depends(require_perm(P.FINANCE_PERIOD_CLOSE))]
CommissionsManage = Annotated[User, Depends(require_perm(P.COMMISSIONS_MANAGE))]
ReportsExecutive = Annotated[User, Depends(require_perm(P.REPORTS_EXECUTIVE))]

__all__ = [
    "CallsListen",
    "ClientsManage",
    "ClientsView",
    "CommissionsManage",
    "CurrentUser",
    "DbSession",
    "FinancePeriodClose",
    "FinancePost",
    "FinanceView",
    "FinanceVoid",
    "InsuredManage",
    "LeadsAssign",
    "LeadsConvert",
    "LeadsImport",
    "LeadsView",
    "PageQuery",
    "PoliciesView",
    "ReportsExecutive",
    "ReportsExport",
    "ReportsView",
    "RequestsManage",
    "RequestsView",
    "ScopeDep",
    "SettingsManage",
    "require_any_perm",
    "require_perm",
]
