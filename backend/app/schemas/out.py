"""Response models for payloads the UI renders.

Why these exist: the frontend is only allowed to consume types generated from the OpenAPI file
(spec §3), so every response it reads must be described here. The models are deliberately
permissive (`extra="allow"`) — they *add* typing for the fields the UI depends on without
restricting what a serializer may return, which keeps them safe to introduce incrementally.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Open(BaseModel):
    """Permissive base: typed where the UI needs it, extra keys pass through."""

    model_config = ConfigDict(from_attributes=True, extra="allow")


class PageOut(Open):
    items: list[Any] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20
    pages: int | None = None


class LeadPageOut(PageOut):
    items: list[LeadOut] = Field(default_factory=list)


class RequestPageOut(PageOut):
    items: list[RequestOut] = Field(default_factory=list)


class ClientPageOut(PageOut):
    items: list[ClientOut] = Field(default_factory=list)


class InsuredPageOut(PageOut):
    items: list[InsuredOut] = Field(default_factory=list)


# ------------------------------------------------------------------ leads -------------------------
class LeadOut(Open):
    id: UUID
    name: str
    type: str
    status: str
    mobile: str | None = None
    contact_name: str | None = None
    contact_title: str | None = None
    headcount: int | None = None
    next_followup_at: datetime | None = None
    followup_overdue: bool = False
    insurance_type: str | None = None
    insurer: str | None = None
    assignee_name: str | None = None
    assignee_id: UUID | None = None
    last_activity_at: datetime | None = None
    converted_client_id: UUID | None = None


class LeadDetailOut(LeadOut):
    email: str | None = None
    source: str | None = None
    budget: float | str | None = None
    needs_text: str | None = None
    current_policy_expiry: date | None = None
    created_at: datetime | None = None
    converted_at: datetime | None = None
    lost_note: str | None = None
    lost_reason: str | None = None


class LeadBulkAssignOut(Open):
    assigned: int = 0
    per_rep: dict[str, int] = Field(default_factory=dict)
    skipped: list[dict[str, Any]] = Field(default_factory=list)


class LeadImportOut(Open):
    """Dry-run preview and final report of a lead import, in the same shape `ImportJobOut` uses."""

    id: UUID
    status: str
    filename: str | None = None
    total_rows: int = 0
    stats: dict[str, int] = Field(default_factory=dict)
    error_report: list[dict[str, Any]] = Field(default_factory=list)
    preview: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime | None = None
    committed_at: datetime | None = None
    #: the two keys the UI needs to drive itself, mirroring the insured-import contract
    confirm_required: bool = False
    commit_url: str | None = None


class TeamMemberOut(Open):
    """One seat of the sales office, as screen 6's team tab lists it."""

    id: UUID
    email: str
    full_name: str
    mobile: str | None = None
    role: str
    role_name: str | None = None
    is_active: bool = True
    open_leads: int = 0
    #: can this seat receive a lead? Served so the redistribute picker and the API agree on one rule
    #: (`leads.view_own`), instead of the UI offering every office seat and the API accepting it (R3).
    accepts_leads: bool = False
    last_login_at: str | None = None


class TeamAssignableRole(Open):
    code: str
    name: str


class TeamMemberCreatedOut(Open):
    id: UUID
    email: str
    role: str
    is_active: bool = True
    #: only present when the server generated it, and only ever in this one response
    temporary_password: str | None = None


class UserCreatedOut(Open):
    id: UUID
    email: str
    role: str
    #: only present when the server generated it, and only ever in this one response
    temporary_password: str | None = None


class LeadActivityOut(Open):
    id: UUID
    type: str
    body: str | None = None
    occurred_at: datetime
    actor_name: str | None = None


class LeadBoardColumnOut(Open):
    status: str
    count: int
    items: list[LeadOut] = Field(default_factory=list)


class LeadBoardOut(Open):
    columns: list[LeadBoardColumnOut] = Field(default_factory=list)


class LeadStatsOut(Open):
    my_leads: int = 0
    new_week: int = 0
    meetings_week: int = 0
    meetings_today: int = 0
    followups_overdue: int = 0
    converted_month: int = 0
    conversion_rate_pct: float | None = None


class LeadConvertOut(Open):
    """Returned by `POST /leads/{id}/convert` — the client the lead became, plus idempotency info."""

    client_id: UUID
    already_converted: bool = False
    account_owner_id: UUID | None = None


class RepWorkloadOut(Open):
    rep_id: UUID
    name: str
    is_active: bool = True
    open_leads: int
    meetings: int
    won: int
    unassigned: int = 0
    conversion_rate: float | None = None
    load: str | None = None
    stages: dict[str, int] = Field(default_factory=dict)
    stages_pct: dict[str, float] = Field(default_factory=dict)


# ---------------------------------------------------------------- clients -------------------------
class ClientOut(Open):
    id: UUID
    name: str
    type: str
    status: str
    since_date: date | None = None
    converted_at: datetime | None = None
    is_converted_from_lead: bool = False
    source_lead_id: UUID | None = None
    account_owner_id: UUID | None = None
    account_owner_name: str | None = None
    insurance_type: str | None = None
    insurer: str | None = None
    insured_count: int = 0
    open_requests: int = 0
    end_date: date | None = None


class ContactOut(Open):
    id: UUID
    name: str
    title: str | None = None
    mobile: str | None = None
    email: str | None = None
    is_primary: bool = False


class InsuredOut(Open):
    id: UUID
    name: str
    mobile: str | None = None
    national_id: str | None = None
    department: str | None = None
    job_title: str | None = None
    coverage_status: str
    coverage_start: date | None = None
    coverage_end: date | None = None
    notes: str | None = None
    external_ref: str | None = None


class ClientKpisOut(Open):
    insured: int = 0
    open_requests: int = 0
    avg_response_minutes: float | None = None
    renewal_in_days: int | None = None
    calls: int = 0


class ClientOverviewOut(Open):
    client: ClientOut
    contacts: list[ContactOut] = Field(default_factory=list)
    kpis: ClientKpisOut
    policy: PolicyOut | None = None


class InsuredListOut(Open):
    page: InsuredPageOut
    kpis: dict[str, int] = Field(default_factory=dict)


class ImportJobOut(Open):
    id: UUID
    client_id: UUID | None = None
    type: str
    status: str
    filename: str | None = None
    total_rows: int = 0
    stats: dict[str, int] = Field(default_factory=dict)
    error_report: list[dict[str, Any]] = Field(default_factory=list)
    preview: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime | None = None


# -------------------------------------------------------------- requests ------------------------
class RequestOut(Open):
    id: UUID
    number: int
    subject: str
    status: str
    priority: str
    channel: str
    is_overdue: bool = False
    sla_due_at: datetime | None = None
    sla_minutes_remaining: int | None = None
    sla_hours_late: int | None = None
    employee_name: str | None = None
    client_id: UUID | None = None
    client_name: str | None = None
    insurance_type: str | None = None
    insurer: str | None = None
    assigned_to: str | None = None
    assignee_name: str | None = None
    mobile: str | None = None
    first_response_at: datetime | None = None
    created_at: datetime | None = None
    last_activity_at: datetime | None = None


class AttachmentOut(Open):
    id: UUID
    filename: str
    mime: str
    size: int = 0
    created_at: datetime | None = None
    url: str | None = None


class RequestMessageOut(Open):
    id: UUID
    direction: str
    channel: str
    body: str | None = None
    media_kind: str | None = None
    duration_s: int | None = None
    waveform: list[float] | None = None
    sender_ref: str | None = None
    external_id: str | None = None
    delivery_status: str
    created_at: datetime
    attachments: list[AttachmentOut] = Field(default_factory=list)


class RequestNoteOut(Open):
    id: UUID
    body: str
    author_name: str | None = None
    created_at: datetime


class CallOut(Open):
    id: UUID
    direction: str
    from_number: str | None = None
    to_number: str | None = None
    caller_name: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    duration_s: int | None = None
    has_recording: bool = False
    recording_consent: bool = False
    listen_allowed: bool = False


class RequestDetailOut(RequestOut):
    description: str | None = None
    messages: list[RequestMessageOut] = Field(default_factory=list)
    notes: list[RequestNoteOut] = Field(default_factory=list)
    calls: list[CallOut] = Field(default_factory=list)
    attachments: list[AttachmentOut] = Field(default_factory=list)


class RecordingUrlOut(Open):
    url: str
    expires_at: str
    duration_s: int | None = None
    recording_consent: bool = False


# --------------------------------------------------------------- dashboard ----------------------
class DashboardSummaryOut(Open):
    requests_today: int = 0
    requests_yesterday: int = 0
    open_requests: int = 0
    overdue: int = 0
    sla_compliance_pct: float | None = None
    avg_first_response_minutes: float | None = None
    avg_first_response_delta_minutes: float | None = None
    clients_total: int = 0


class DashboardChannelOut(Open):
    date: date
    whatsapp: int = 0
    call: int = 0
    email: int = 0
    manual: int = 0


class DashboardStatusOut(Open):
    key: str
    count: int
    pct: int


class ClientKpiRowOut(Open):
    total_clients: int = 0
    converted_from_leads: int = 0
    expiring_90d: int = 0


# -------------------------------------------------------------- users & masters -----------------
class UserOut(Open):
    id: UUID
    email: str
    full_name: str
    mobile: str | None = None
    role: str
    role_name: str | None = None
    is_active: bool = True
    mfa_enabled: bool = False
    last_login_at: datetime | None = None
    scopes: list[Any] = Field(default_factory=list)
    portal_client_id: UUID | None = None


class RoleOut(Open):
    id: UUID
    code: str
    name: str
    is_system: bool = True
    permissions: list[str] = Field(default_factory=list)


class PermissionOut(Open):
    code: str
    group: str
    description: str | None = None


class FeatureFlagOut(Open):
    key: str
    enabled: bool
    description: str | None = None


class InsurerOut(Open):
    id: UUID
    code: str
    name: str
    name_en: str | None = None
    is_active: bool = True


class InsuranceTypeOut(Open):
    id: UUID
    code: str
    name: str
    name_en: str | None = None


class LostReasonOut(Open):
    id: UUID
    name: str
    sort_order: int = 0


class SlaPolicyOut(Open):
    priority: str
    first_response_minutes: int
    resolution_minutes: int
    warn_before_minutes: int = 0


# ------------------------------------------------------------------ phase 2 ---------------------
class InsurerRefOut(Open):
    id: UUID
    name: str


class ClientRefOut(Open):
    """Enough to name a client on a policy row — no contacts, no notes, nothing to leak."""

    id: UUID
    name: str


class InsuranceTypeRefOut(Open):
    id: UUID
    code: str
    name: str


class PolicyDocumentRefOut(Open):
    """One issued file, as a list row needs it: what to call it and where the download lives."""

    filename: str
    size: int
    created_at: datetime
    download_url: str


class PolicyOut(Open):
    """
    A policy as the screens read it.

    `policy_no` is nullable here *and* in the model and the create body (`PolicyCreate.policy_no` defaults to
    `None`): a draft booked before the insurer answers carries no number yet. Declaring it `str` made FastAPI
    refuse to serialise the whole payload — the client file answered 500 for every client holding a draft
    policy, which is what took the `screens`, `screenshots` and therefore three e2e tests down (R3).
    """

    id: UUID
    client_id: UUID | None = None
    policy_no: str | None = None
    status: str
    start_date: date | None = None
    end_date: date | None = None
    premium_amount: float | str | None = None
    currency: str = "EGP"
    payment_plan: str | None = None
    commission_rate: float | str | None = None
    headcount: int | None = None
    client: ClientRefOut | None = None
    insurer: InsurerRefOut | None = None
    insurance_type: InsuranceTypeRefOut | None = None
    document: PolicyDocumentRefOut | None = None


class InstallmentOut(Open):
    id: UUID
    sequence_no: int | None = None
    due_date: date
    amount: float
    status: str
    paid_at: datetime | None = None


class PolicyKpisOut(Open):
    active: int = 0
    expiring_90d: int = 0
    installments_this_month: int = 0
    late_installments: int = 0
    expected_commission: float | None = None
    currency: str = "EGP"


class CommissionSummaryOut(Open):
    """`state` is the honest part: `not_computed` means no commission row exists (premium/rate zero or unset)."""

    state: str = "not_computed"
    amount: float | None = None
    rate: float | None = None
    status: str | None = None
    settled_at: str | None = None


class InstallmentTotalsOut(Open):
    count: int = 0
    paid_count: int = 0
    late_count: int = 0
    due_count: int = 0
    paid_amount: float = 0.0
    late_amount: float = 0.0
    due_amount: float = 0.0
    outstanding_amount: float = 0.0


class InstallmentPlanOut(Open):
    policy: PolicyOut
    items: list[InstallmentOut] = Field(default_factory=list)
    totals: InstallmentTotalsOut | None = None
    commission: CommissionSummaryOut | None = None


class IssuingDocumentOut(Open):
    name: str
    status: str


class IssuingCaseOut(Open):
    id: UUID
    stage: str
    client: str | None = None
    insurer: str | None = None
    premium_amount: float | None = None
    documents: list[IssuingDocumentOut] = Field(default_factory=list)


class IssuingDocumentDetailOut(Open):
    id: UUID
    name: str
    status: str
    requirement_id: UUID | None = None
    attachment_id: UUID | None = None


class IssuingCaseDetailOut(IssuingCaseOut):
    """Case header + the timestamps the pipeline screen needs (spec §9.13)."""

    headcount: int | None = None
    assigned_to: UUID | None = None
    started_at: str | None = None
    issued_at: str | None = None
    delivered_at: str | None = None


class InsurerPerformanceOut(Open):
    insurer: str
    requests: int
    avg_first_response_minutes: float | None = None
    sla_pct: float
    tone: str = "b"


class CommissionOut(Open):
    id: UUID
    policy_id: UUID | None = None
    amount: float
    status: str
    settled_at: datetime | None = None


# ------------------------------------------------------------------- portal ---------------------
class PortalClientOut(Open):
    id: UUID
    name: str
    type: str
    since_date: date | None = None


class PortalPolicySummaryOut(Open):
    policy_no: str | None = None
    status: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    payment_plan: str | None = None
    insurer: str | None = None
    insurance_type: str | None = None


class PortalNextDueOut(Open):
    due_date: date | None = None
    amount: float | None = None


class PortalPremiumsOut(Open):
    """Installment money the owner is allowed to see. Commissions never appear here (spec §6)."""

    total: float | None = None
    paid: float | None = None
    overdue: float | None = None
    next_due: PortalNextDueOut | None = None


class PortalKpisOut(Open):
    requests_this_month: int = 0
    avg_response_minutes: float | None = None
    premiums: PortalPremiumsOut | None = None


class PortalOverviewOut(Open):
    """`policy_summary` is null until the client has a policy — the portal renders placeholders."""

    client: PortalClientOut
    policy_summary: PortalPolicySummaryOut | None = None
    kpis: PortalKpisOut
    covered_count: int = 0
    renewal_in_days: int | None = None


class PortalMessageOut(Open):
    """What the owner may read of a thread: the message itself, nothing about staff routing."""

    id: UUID
    direction: str
    channel: str
    body: str | None = None
    created_at: datetime | None = None


class PortalRequestItemOut(Open):
    """Portal-safe projection.

    It deliberately does *not* inherit `RequestOut`: the isolation test walks the response keys and
    any staff-only field name (assignee, insurer, internal flags) must be absent, not merely null.
    """

    id: UUID
    number: int
    subject: str
    status: str
    channel: str
    created_at: datetime | None = None
    last_activity_at: datetime | None = None
    messages: list[PortalMessageOut] = Field(default_factory=list)


class PortalRequestsOut(Open):
    items: list[PortalRequestItemOut] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20


class PortalDocumentOut(Open):
    id: UUID
    #: `policy` for an issued-policy file, `request_attachment` for a file on one of the owner's requests
    kind: str = "request_attachment"
    filename: str
    mime: str
    size: int = 0
    created_at: datetime | None = None
    request_number: int | None = None
    policy_no: str | None = None
    download_url: str | None = None


class PortalPolicyDocumentOut(Open):
    filename: str
    size: int = 0
    created_at: datetime | None = None
    download_url: str | None = None


class PortalPolicyOut(Open):
    """One contract as the owner may see it: no commission, no internal notes, no staff names."""

    id: UUID
    policy_no: str | None = None
    status: str
    insurance_type: str | None = None
    insurer: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    premium_amount: float | None = None
    currency: str = "EGP"
    headcount: int | None = None
    document: PortalPolicyDocumentOut | None = None


class PortalRequestOut(Open):
    request_id: UUID
    number: int


class SignedUrlOut(Open):
    url: str
    expires_at: str


# Some page models reference classes declared later in this module; rebuild them once all exist.
for _model in (LeadPageOut, RequestPageOut, ClientPageOut, InsuredPageOut, InsuredListOut, ClientOverviewOut):
    _model.model_rebuild()


class TeamRosterOut(Open):
    """`GET /sales-team/members`: the office seats, and the roles the tab is allowed to hand out.

    The allowlist is served rather than copied into the client — a frontend that repeats `OFFICE_ROLES`
    would drift the day the set changes, and the dialog would offer an option the API refuses.
    """

    members: list[TeamMemberOut] = Field(default_factory=list)
    assignable_roles: list[TeamAssignableRole] = Field(default_factory=list)


# ---------------------------------------------------------------- R10 money layer (ADR-030) ---------
class LedgerEntryOut(Open):
    id: UUID
    entry_no: str
    entry_type: str
    direction: str
    amount: float
    currency: str = "EGP"
    status: str
    client_id: UUID | None = None
    client_name: str | None = None
    policy_id: UUID | None = None
    policy_no: str | None = None
    installment_id: UUID | None = None
    installment_sequence: int | None = None
    user_id: UUID | None = None
    user_name: str | None = None
    method: str | None = None
    note: str | None = None
    posted_at: datetime
    posted_by_name: str | None = None
    voided_at: datetime | None = None
    void_reason: str | None = None


class LedgerPageOut(PageOut):
    items: list[LedgerEntryOut] = Field(default_factory=list)


class MoneyByMethodOut(Open):
    method: str
    in_total: float
    out_total: float
    balance: float


class SpendByTypeOut(Open):
    entry_type: str
    in_total: float
    out_total: float
    net: float


class SpendByPeriodOut(Open):
    currency: str = "EGP"
    period: str
    label: str
    start: date
    end: date
    total_in: float
    total_out: float
    net: float
    by_type: list[SpendByTypeOut] = Field(default_factory=list)


class CashboxOut(Open):
    currency: str = "EGP"
    as_of: datetime
    is_net_movements_only: bool = True
    balance_note: str = "net_movements_only"
    #: Score-8 (ADR-033): the counted opening the balance stands on, when one has been set
    opening_balance: float = 0.0
    opening_effective_at: datetime | None = None
    #: the earliest posted cash movement — the balance is the net movement from here to `as_of`
    since: datetime | None = None
    balance: float
    cash_in_total: float
    cash_out_total: float
    today_in: float
    today_out: float
    month_in: float
    month_out: float
    by_method: list[MoneyByMethodOut] = Field(default_factory=list)
    movements: list[LedgerEntryOut] = Field(default_factory=list)


class LedgerVoidOut(Open):
    entry: LedgerEntryOut
    cashbox_balance: float
    #: what the void put back: `{"kind": "installment"|"commission", "id": ..., "status": ...}`, or null
    reopened: dict[str, Any] | None = None


class AgingBucketOut(Open):
    bucket: str
    amount: float
    count: int


class ArSummaryOut(Open):
    currency: str = "EGP"
    as_of: date
    outstanding_total: float
    overdue_total: float
    current_total: float
    clients_count: int
    overdue_clients_count: int
    buckets: list[AgingBucketOut] = Field(default_factory=list)


class ArClientRowOut(Open):
    client_id: UUID
    client_name: str
    owner_name: str | None = None
    outstanding_total: float
    overdue_total: float
    current_total: float
    oldest_due_date: date | None = None
    installments_count: int = 0
    last_payment_at: datetime | None = None
    buckets: list[AgingBucketOut] = Field(default_factory=list)


class ArClientPageOut(PageOut):
    items: list[ArClientRowOut] = Field(default_factory=list)


class ArInstallmentOut(Open):
    installment_id: UUID
    policy_id: UUID
    policy_no: str | None = None
    sequence_no: int
    due_date: date
    amount: float
    days_overdue: int
    bucket: str
    status: str


class ArClientDetailOut(Open):
    client_id: UUID
    client_name: str
    owner_name: str | None = None
    outstanding_total: float
    overdue_total: float
    current_total: float
    oldest_due_date: date | None = None
    installments_count: int = 0
    last_payment_at: datetime | None = None
    buckets: list[AgingBucketOut] = Field(default_factory=list)
    installments: list[ArInstallmentOut] = Field(default_factory=list)
    payments: list[LedgerEntryOut] = Field(default_factory=list)


class SettlementOut(Open):
    """One commission and its payout state. `id` is the *commission* id the pay route takes."""

    id: UUID
    commission_id: UUID
    settlement_id: UUID | None = None
    status: str
    amount: float
    currency: str = "EGP"
    payee_user_id: UUID | None = None
    payee_name: str | None = None
    policy_id: UUID
    policy_no: str | None = None
    client_id: UUID | None = None
    client_name: str | None = None
    installment_id: UUID | None = None
    accrued_at: datetime | None = None
    approved_at: datetime | None = None
    paid_at: datetime | None = None
    ledger_entry_id: UUID | None = None
    ledger_entry_no: str | None = None
    method: str | None = None
    note: str | None = None


class SettlementPageOut(PageOut):
    items: list[SettlementOut] = Field(default_factory=list)


class ExecutivePeriodOut(Open):
    from_date: date
    to_date: date
    label: str


class ExecutiveKpisOut(Open):
    collections_mtd: float
    #: `null` on purpose: the office has no collection target model yet, and a made-up denominator would
    #: turn a real numerator into a fake percentage (R10 out of scope, ADR-030 §8).
    collections_target: float | None = None
    outstanding_ar: float
    overdue_ar: float
    commissions_accrued: float
    commissions_paid_mtd: float
    open_requests: int
    overdue_requests: int
    new_leads_mtd: int
    converted_leads_mtd: int
    policies_issued_mtd: int
    cashbox_balance: float


class SalesPerformanceOut(Open):
    user_id: UUID | None = None
    name: str
    policies_issued_mtd: int
    premium_mtd: float
    collected_mtd: float


class ChannelMixOut(Open):
    channel: str
    count: int


class RenewalOut(Open):
    policy_id: UUID
    policy_no: str | None = None
    client_name: str | None = None
    insurer_name: str | None = None
    end_date: date
    days_left: int
    premium_amount: float


class ExecutiveAlertOut(Open):
    code: str
    severity: str
    message: str
    count: int


class ExecutiveDashboardOut(Open):
    period: ExecutivePeriodOut
    kpis: ExecutiveKpisOut
    sales_performance: list[SalesPerformanceOut] = Field(default_factory=list)
    ar_aging: list[AgingBucketOut] = Field(default_factory=list)
    channel_mix: list[ChannelMixOut] = Field(default_factory=list)
    renewals_next_30d: list[RenewalOut] = Field(default_factory=list)
    alerts: list[ExecutiveAlertOut] = Field(default_factory=list)
