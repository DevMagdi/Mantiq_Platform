"""SLA arithmetic: business-hours aware deadlines + elapsed-time metrics.

Rules (spec §8): deadlines are counted in working minutes only; Friday/Saturday are the default
weekend; a request that is replied to stops the clock for `resolved` purposes but keeps its
`first_response_at` stamp forever.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, timezone

CAIRO = timezone(timedelta(hours=2))  # Egypt is UTC+2 year-round since 2023


@dataclass(frozen=True)
class BusinessHours:
    open_at: time = time(9, 0)
    close_at: time = time(18, 0)
    weekend_days: frozenset[int] = frozenset({4, 5})  # Python weekday(): Mon=0 … Fri=4, Sat=5

    @classmethod
    def from_rows(cls, rows: list[tuple[int, time, time, bool]]) -> BusinessHours:
        """rows: (weekday, open, close, is_open). Empty input → defaults."""
        if not rows:
            return cls()
        weekend = {w for (w, _o, _c, is_open) in rows if not is_open}
        first = next(r for r in rows if r[3])
        return cls(open_at=first[1], close_at=first[2], weekend_days=frozenset(weekend))

    def is_working_day(self, moment: datetime) -> bool:
        return moment.astimezone(CAIRO).weekday() not in self.weekend_days

    def day_window(self, day: datetime) -> tuple[datetime, datetime] | None:
        local = day.astimezone(CAIRO)
        if local.weekday() in self.weekend_days:
            return None
        base = local.replace(hour=0, minute=0, second=0, microsecond=0)
        start = base.replace(hour=self.open_at.hour, minute=self.open_at.minute)
        end = base.replace(hour=self.close_at.hour, minute=self.close_at.minute)
        return (
            start.astimezone(UTC).replace(tzinfo=UTC),
            end.astimezone(UTC).replace(tzinfo=UTC),
        )

    def day_capacity_minutes(self) -> int:
        return self.close_at.hour * 60 + self.close_at.minute - (self.open_at.hour * 60 + self.open_at.minute)


def add_business_minutes(start: datetime, minutes: int, hours: BusinessHours) -> datetime:
    """Push `minutes` of working time forward from `start`, skipping weekends/off-hours."""
    if minutes <= 0:
        return start
    remaining = timedelta(minutes=minutes)
    cursor = start
    guard = 0
    while remaining > timedelta(0):
        guard += 1
        if guard > 400:  # 400 days of look-ahead is a bug, not a deadline
            raise ValueError("SLA window exceeds look-ahead guard")
        window = hours.day_window(cursor)
        if window is None:
            cursor = _next_midnight(cursor)
            continue
        win_start, win_end = window
        if cursor < win_start:
            cursor = win_start
        if cursor >= win_end:
            cursor = _next_midnight(cursor)
            continue
        available = win_end - cursor
        if available >= remaining:
            return cursor + remaining
        remaining -= available
        cursor = _next_midnight(cursor)
    return cursor


def business_minutes_between(a: datetime, b: datetime, hours: BusinessHours) -> float:
    if b <= a:
        return 0.0
    total = 0.0
    cursor = a
    while cursor < b:
        window = hours.day_window(cursor)
        if window is None:
            cursor = _next_midnight(cursor)
            continue
        win_start, win_end = window
        lo = max(cursor, win_start, a)
        hi = min(b, win_end)
        if hi > lo:
            total += (hi - lo).total_seconds() / 60
        cursor = _next_midnight(cursor)
    return round(total, 2)


def sla_state(now: datetime, sla_due_at: datetime | None, status_value: str) -> tuple[bool, int | None]:
    """(is_overdue, hours_late) — `resolved`/`closed` are never overdue."""
    if sla_due_at is None or status_value in {"resolved", "closed"}:
        return False, None
    if now <= sla_due_at:
        return False, None
    return True, int((now - sla_due_at).total_seconds() // 3600)


def _next_midnight(moment: datetime) -> datetime:
    local = moment.astimezone(CAIRO)
    return (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
