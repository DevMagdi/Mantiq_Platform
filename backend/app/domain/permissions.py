"""Fine-grained permissions and the default role → permission matrix (spec §6)."""

from __future__ import annotations

from typing import Final

# Permission slugs (must match `permissions.code` in the DB and the frontend route guards).
LEADS_VIEW_OWN: Final = "leads.view_own"
LEADS_VIEW_ALL: Final = "leads.view_all"
LEADS_ASSIGN: Final = "leads.assign"
LEADS_CONVERT: Final = "leads.convert"
REQUESTS_VIEW: Final = "requests.view"
REQUESTS_MANAGE: Final = "requests.manage"
CALLS_LISTEN: Final = "calls.listen"
CLIENTS_VIEW_OWN: Final = "clients.view_own"
CLIENTS_VIEW_ALL: Final = "clients.view_all"
CLIENTS_MANAGE: Final = "clients.manage"
INSURED_MANAGE: Final = "insured.manage"
POLICIES_VIEW: Final = "policies.view"
POLICIES_MANAGE: Final = "policies.manage"
COMMISSIONS_VIEW: Final = "commissions.view"
REPORTS_VIEW: Final = "reports.view"
REPORTS_EXPORT: Final = "reports.export"
# R10 (ADR-030): the money layer has its own gate. There is deliberately no `finance.manage`: posting and
# voiding are *different* powers (one writes history, the other takes it back), and the office wanted the
# second one to be the rarer one.
FINANCE_VIEW: Final = "finance.view"
FINANCE_POST: Final = "finance.post"
FINANCE_VOID: Final = "finance.void"
#: Score-8 (ADR-033): closing/reopening a finance month, and posting into one once closed
FINANCE_PERIOD_CLOSE: Final = "finance.period_close"
COMMISSIONS_MANAGE: Final = "commissions.manage"
REPORTS_EXECUTIVE: Final = "reports.executive"
USERS_MANAGE: Final = "users.manage"
SETTINGS_MANAGE: Final = "settings.manage"
# Derived, never stored in `role_permissions`: granted implicitly to portal accounts
# (their rows are pinned to one client by Scope) so the SPA can gate its portal routes.
PORTAL_ACCESS: Final = "portal.access"

ALL_PERMISSIONS: Final[tuple[str, ...]] = (
    LEADS_VIEW_OWN,
    LEADS_VIEW_ALL,
    LEADS_ASSIGN,
    LEADS_CONVERT,
    REQUESTS_VIEW,
    REQUESTS_MANAGE,
    CALLS_LISTEN,
    CLIENTS_VIEW_OWN,
    CLIENTS_VIEW_ALL,
    CLIENTS_MANAGE,
    INSURED_MANAGE,
    POLICIES_VIEW,
    POLICIES_MANAGE,
    COMMISSIONS_VIEW,
    REPORTS_VIEW,
    REPORTS_EXPORT,
    FINANCE_VIEW,
    FINANCE_POST,
    FINANCE_VOID,
    FINANCE_PERIOD_CLOSE,
    COMMISSIONS_MANAGE,
    REPORTS_EXECUTIVE,
    USERS_MANAGE,
    SETTINGS_MANAGE,
)  # NOTE: PORTAL_ACCESS is derived (see services/auth.issue_tokens)

ROLE_PERMISSIONS: Final[dict[str, tuple[str, ...]]] = {
    "system_admin": ALL_PERMISSIONS,
    "sales_manager": (
        LEADS_VIEW_ALL,
        LEADS_ASSIGN,
        LEADS_CONVERT,
        REQUESTS_VIEW,
        REQUESTS_MANAGE,
        CALLS_LISTEN,
        CLIENTS_VIEW_ALL,
        CLIENTS_MANAGE,
        INSURED_MANAGE,
        POLICIES_VIEW,
        POLICIES_MANAGE,
        COMMISSIONS_VIEW,
        REPORTS_VIEW,
        REPORTS_EXPORT,
        FINANCE_VIEW,
        FINANCE_POST,
        FINANCE_VOID,
        FINANCE_PERIOD_CLOSE,
        COMMISSIONS_MANAGE,
        REPORTS_EXECUTIVE,
        SETTINGS_MANAGE,
    ),
    "sales_rep": (LEADS_VIEW_OWN, LEADS_CONVERT, CLIENTS_VIEW_OWN, CLIENTS_MANAGE, INSURED_MANAGE),
    "customer_service": (REQUESTS_VIEW, REQUESTS_MANAGE, CALLS_LISTEN),
    "account_manager": (
        REQUESTS_VIEW,
        REQUESTS_MANAGE,
        CALLS_LISTEN,
        CLIENTS_VIEW_ALL,
        INSURED_MANAGE,
        POLICIES_VIEW,
        COMMISSIONS_VIEW,
        REPORTS_VIEW,
        FINANCE_VIEW,
    ),
    # Portal roles carry no staff permissions; their access is scoped by client id (see Scope).
    "client_owner": (),
    "client_contact": (),
}

DEFAULT_ROLES: Final[dict[str, str]] = {
    "system_admin": "مدير النظام",
    "sales_manager": "مدير مبيعات",
    "sales_rep": "بياع",
    "customer_service": "خدمة عملاء",
    "account_manager": "مدير حساب",
    "client_owner": "صاحب شركة العميل (بوابة)",
    "client_contact": "جهة اتصال العميل (بوابة)",
}
