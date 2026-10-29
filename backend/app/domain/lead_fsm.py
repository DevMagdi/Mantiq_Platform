"""Lead status machine — one place that knows which transitions are legal."""

from __future__ import annotations

from app.domain.enums import LeadStatus

# Board columns (screen 3) in progression order; `lost`/`postponed` are filter-only.
PROGRESSION: tuple[LeadStatus, ...] = (
    LeadStatus.NEW,
    LeadStatus.CONTACTED,
    LeadStatus.INTERESTED,
    LeadStatus.MEETING_SCHEDULED,
    LeadStatus.MEETING_DONE,
    LeadStatus.QUOTE_SENT,
    LeadStatus.NEGOTIATION,
    LeadStatus.WON,
)

TERMINAL: frozenset[LeadStatus] = frozenset({LeadStatus.WON, LeadStatus.LOST})
# Anything a rep may set directly from a non-terminal state.
MANUAL_TARGETS: frozenset[LeadStatus] = frozenset(
    {
        LeadStatus.CONTACTED,
        LeadStatus.INTERESTED,
        LeadStatus.MEETING_SCHEDULED,
        LeadStatus.MEETING_DONE,
        LeadStatus.QUOTE_SENT,
        LeadStatus.NEGOTIATION,
        LeadStatus.POSTPONED,
        LeadStatus.NEW,
    }
)


def can_transition(current: LeadStatus, target: LeadStatus) -> bool:
    if current in TERMINAL:
        return False  # won => converted (irreversible), lost => needs reopen by manager (not in Phase 1)
    if target in {LeadStatus.WON, LeadStatus.LOST}:
        return False  # only /convert and /lose may reach these
    return target in MANUAL_TARGETS


def is_terminal(status: LeadStatus) -> bool:
    return status in TERMINAL


def rank(status: LeadStatus) -> int:
    return PROGRESSION.index(status) if status in PROGRESSION else -1
