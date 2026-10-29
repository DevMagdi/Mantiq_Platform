"""A small fixed-window limiter for endpoints that call a third party (ADR-016).

Why this exists rather than "just don't spam the button": `POST /settings/integrations/{provider}/test`
reaches Meta's `debug_token`, whose quota is per-app and shared with the production messaging the same
credential does. One curious operator clicking «اختبار الاتصال» twelve times can therefore spend the day's
budget for the whole workspace, and the failure it produces (HTTP 429 from Meta, on the *message* path)
looks like an outage.

Process-local on purpose, like `services/auth.py`'s lockout: with a single uvicorn worker per container
this is exact, and with more than one the limit is "N per worker per minute", which is still the right
shape of protection. Moving it to Redis is a deployment change, not a correctness one — and pretending
otherwise would make the setting screen depend on a service the tests do not have.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from app.core.errors import TooManyRequests


@dataclass(frozen=True)
class Budget:
    allowed: int
    window_seconds: float


class SlidingWindow:
    def __init__(self, budget: Budget) -> None:
        self.budget = budget
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str) -> int:
        """Record a call and return what is left in the window. Raises 429 when the window is full."""
        now = time.monotonic()
        window = self._hits.setdefault(key, deque())
        cutoff = now - self.budget.window_seconds
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= self.budget.allowed:
            retry_after = int(window[0] + self.budget.window_seconds - now) + 1
            raise TooManyRequests(
                "Too many connection tests for this provider — wait a minute and try again",
                details={"retry_after_seconds": max(retry_after, 1), "limit": self.budget.allowed},
            )
        window.append(now)
        return max(self.budget.allowed - len(window), 0)

    def remaining(self, key: str) -> int:
        window = self._hits.get(key)
        if not window:
            return self.budget.allowed
        now = time.monotonic()
        cutoff = now - self.budget.window_seconds
        return max(self.budget.allowed - sum(1 for hit in window if hit >= cutoff), 0)

    def reset(self) -> None:
        """Test/admin hook, mirroring `auth.reset_rate_limits`."""
        self._hits.clear()


#: six tests per minute per user per provider — enough to debug a typo, not enough to exhaust a quota
TEST_BUDGET = Budget(allowed=6, window_seconds=60.0)
test_limiter = SlidingWindow(TEST_BUDGET)
