"""Pure domain unit tests — no DB, no FastAPI."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.domain.enums import LeadStatus
from app.domain.lead_fsm import can_transition, is_terminal
from app.domain.phone import display_mobile, mask_mobile, normalize_mobile
from app.domain.sla import BusinessHours, add_business_minutes, business_minutes_between, sla_state

HOURS = BusinessHours()  # 09:00–18:00, Fri/Sat weekend (Egypt)


class TestPhone:
    @pytest.mark.parametrize(
        "raw",
        ["01001234567", "+201001234567", "+20 100 1234 567", "0020 100 1234 567", "201001234567", "(0100) 123-4567"],
    )
    def test_all_formattings_converge(self, raw: str) -> None:
        assert normalize_mobile(raw) == "+201001234567"

    def test_landline_and_garbage_are_rejected(self) -> None:
        assert normalize_mobile("12345") is None
        assert normalize_mobile("") is None
        assert normalize_mobile(None) is None
        assert normalize_mobile("013") is None

    def test_display_and_mask(self) -> None:
        assert display_mobile("+201001234567") == "+20 100 123 4567"
        masked = mask_mobile("+201001234567")
        assert "*" in masked and "1234567" not in masked

    def test_non_egyptian_kept(self) -> None:
        assert normalize_mobile("+966512345678") == "+966512345678"


class TestSla:
    def test_deadline_skips_weekend(self) -> None:
        # Fri 2026-10-23 12:00 UTC (14:00 Cairo) is a weekend: the 6h clock only starts at
        # Sun 09:00 Cairo = 07:00 UTC, so the deadline lands at 13:00 UTC Sunday.
        start = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
        assert add_business_minutes(start, 6 * 60, HOURS) == datetime(2026, 10, 25, 13, 0, tzinfo=UTC)

    def test_out_of_hours_rolls_to_next_open(self) -> None:
        after_close = datetime(2026, 10, 25, 19, 0, tzinfo=UTC)  # 21:00 Cairo, Sunday
        assert add_business_minutes(after_close, 30, HOURS) == datetime(2026, 10, 26, 7, 30, tzinfo=UTC)

    def test_zero_minutes_is_noop(self) -> None:
        moment = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
        assert add_business_minutes(moment, 0, HOURS) == moment

    def test_business_minutes_between_excludes_weekend(self) -> None:
        # Sun 09:00–18:00 + Mon 09:00–10:00 Cairo = 540 + 60
        a = datetime(2026, 10, 25, 7, 0, tzinfo=UTC)
        b = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)
        assert business_minutes_between(a, b, HOURS) == 600.0

    def test_guard_against_runaway_window(self) -> None:
        with pytest.raises(ValueError):
            add_business_minutes(datetime(2026, 10, 25, 7, 0, tzinfo=UTC), 401 * 600, HOURS)

    def test_overdue_state(self) -> None:
        due = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
        assert sla_state(due - timedelta(minutes=5), due, "new") == (False, None)
        assert sla_state(due + timedelta(hours=3, minutes=59), due, "in_progress") == (True, 3)
        assert sla_state(due + timedelta(hours=9), due, "resolved") == (False, None)


class TestLeadFsm:
    def test_forward_and_sideways_are_allowed(self) -> None:
        assert can_transition(LeadStatus.NEW, LeadStatus.MEETING_SCHEDULED)
        assert can_transition(LeadStatus.NEGOTIATION, LeadStatus.NEW)

    def test_won_and_lost_need_their_own_endpoints(self) -> None:
        assert not can_transition(LeadStatus.NEGOTIATION, LeadStatus.WON)
        assert not can_transition(LeadStatus.NEGOTIATION, LeadStatus.LOST)

    def test_terminal_states_are_frozen(self) -> None:
        assert not can_transition(LeadStatus.WON, LeadStatus.CONTACTED)
        assert is_terminal(LeadStatus.LOST) and is_terminal(LeadStatus.WON)
