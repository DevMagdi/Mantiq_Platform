"""String enums persisted as VARCHAR + CHECK (portable across SQLite/PostgreSQL)."""

from __future__ import annotations

from enum import StrEnum


def values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]


class LeadStatus(StrEnum):
    NEW = "new"
    CONTACTED = "contacted"
    INTERESTED = "interested"
    MEETING_SCHEDULED = "meeting_scheduled"
    MEETING_DONE = "meeting_done"
    QUOTE_SENT = "quote_sent"
    NEGOTIATION = "negotiation"
    WON = "won"
    LOST = "lost"
    POSTPONED = "postponed"

    @classmethod
    def board_columns(cls) -> list[LeadStatus]:
        """The 5 Kanban columns of screen 3 (lost/postponed are reachable only via filters)."""
        return [cls.NEW, cls.CONTACTED, cls.MEETING_SCHEDULED, cls.QUOTE_SENT, cls.WON]

    @classmethod
    def active(cls) -> list[LeadStatus]:
        return [s for s in cls if s not in {cls.WON, cls.LOST, cls.POSTPONED}]


class ClientType(StrEnum):
    COMPANY = "company"
    INDIVIDUAL = "individual"


class ClientStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    PROSPECT = "prospect"
    ARCHIVED = "archived"


class InsuranceTypeCode(StrEnum):
    MEDICAL = "medical"
    GENERAL = "general"
    MEDICAL_GENERAL = "both"
    LIFE = "life"
    MOTOR = "motor"
    TRAVEL = "travel"
    PROPERTY = "property"


class RequestStatus(StrEnum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    WAITING_INSURER = "waiting_insurer"
    REPLIED = "replied"
    RESOLVED = "resolved"
    CLOSED = "closed"

    @classmethod
    def open_statuses(cls) -> list[RequestStatus]:
        return [cls.NEW, cls.IN_PROGRESS, cls.WAITING_INSURER]


class RequestPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class Channel(StrEnum):
    WHATSAPP = "whatsapp"
    CALL = "call"
    EMAIL = "email"
    MANUAL = "manual"


class MessageDirection(StrEnum):
    IN = "in"
    OUT = "out"


class DeliveryStatus(StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"


class ActivityType(StrEnum):
    CALL = "call"
    WHATSAPP = "whatsapp"
    MEETING = "meeting"
    NOTE = "note"
    STATUS_CHANGE = "status_change"
    ASSIGNMENT = "assignment"


class ScopeType(StrEnum):
    CLIENT = "client"
    INSURER = "insurer"


class PortalRole(StrEnum):
    OWNER = "owner"
    CONTACT = "contact"


class CoverageStatus(StrEnum):
    ACTIVE = "active"
    PENDING = "pending"
    ENDED = "ended"


class CallDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class AttachmentOwner(StrEnum):
    REQUEST = "request"
    LEAD = "lead"
    CLIENT = "client"
    ISSUING_DOCUMENT = "issuing_document"


class ImportJobType(StrEnum):
    INSURED_PERSONS = "insured_persons"


class ImportJobStatus(StrEnum):
    DRY_RUN = "dry_run"
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class NotificationType(StrEnum):
    REQUEST_ASSIGNED = "request_assigned"
    REQUEST_OVERDUE = "request_overdue"
    REQUEST_DUE_SOON = "request_due_soon"
    NEW_REQUEST = "new_request"
    LEAD_ASSIGNED = "lead_assigned"
    LEAD_FOLLOWUP_DUE = "lead_followup_due"
    LEAD_CONVERTED = "lead_converted"
    IMPORT_FINISHED = "import_finished"


class PaymentPlan(StrEnum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMIANNUAL = "semiannual"
    CASH = "cash"


class PolicyStatus(StrEnum):
    DRAFT = "draft"
    PENDING_INSURER = "pending_insurer"
    ISSUED = "issued"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class InstallmentStatus(StrEnum):
    UPCOMING = "upcoming"
    PAID = "paid"
    LATE = "late"


class CommissionStatus(StrEnum):
    ACCRUED = "accrued"
    SETTLED = "settled"
    CLAWED_BACK = "clawed_back"


class IssuingStage(StrEnum):
    AGREEMENT = "agreement"
    DOCS_REVIEW = "docs_review"
    SENT_TO_INSURER = "sent_to_insurer"
    ISSUED = "issued"
    DELIVERED = "delivered"


class IssuingDocumentStatus(StrEnum):
    MISSING = "missing"
    UPLOADED = "uploaded"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"


class LedgerEntryType(StrEnum):
    """What a ledger row *is* (R10, ADR-030). The five types the four modules actually post."""

    PREMIUM_RECEIVABLE = "premium_receivable"
    COMMISSION_RECEIVABLE = "commission_receivable"
    COMMISSION_PAYABLE = "commission_payable"
    EXPENSE = "expense"
    ADJUSTMENT = "adjustment"

    @classmethod
    def money_in(cls) -> list[LedgerEntryType]:
        """Types that can only move money *into* the office (a receipt, never a payment)."""
        return [cls.PREMIUM_RECEIVABLE, cls.COMMISSION_RECEIVABLE]

    @classmethod
    def money_out(cls) -> list[LedgerEntryType]:
        return [cls.COMMISSION_PAYABLE, cls.EXPENSE]


class LedgerDirection(StrEnum):
    IN = "in"
    OUT = "out"


class LedgerStatus(StrEnum):
    POSTED = "posted"
    VOID = "void"


class PaymentMethod(StrEnum):
    CASH = "cash"
    BANK = "bank"
    WALLET = "wallet"
    OTHER = "other"


class SettlementStatus(StrEnum):
    """The lifecycle of one commission *payout* (distinct from `CommissionStatus` accrual)."""

    ACCRUED = "accrued"
    APPROVED = "approved"
    PAID = "paid"
    CANCELLED = "cancelled"
