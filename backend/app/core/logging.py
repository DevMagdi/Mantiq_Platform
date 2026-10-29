"""structlog JSON logging with correlation id."""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any, cast

import structlog

correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")


def setup_logging(level: str = "INFO", json_logs: bool = True) -> None:
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_correlation_id,
        RedactPhones(),
    ]
    structlog.configure(
        processors=[
            *shared,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), 20)),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=getattr(logging, level.upper(), 20), format="%(message)s", force=True)


def _add_correlation_id(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    event_dict.setdefault("cid", correlation_id.get())
    return event_dict


class RedactPhones:
    """Mask Egyptian mobile numbers in logs (data-protection requirement)."""

    _MARKERS = ("010", "011", "012", "015", "+201")

    def __call__(self, _logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        for key, value in list(event_dict.items()):
            if isinstance(value, str) and any(m in value for m in self._MARKERS):
                event_dict[key] = self.mask(value)
        return event_dict

    @staticmethod
    def mask(value: str) -> str:
        out: list[str] = []
        digits = [c for c in value if c.isdigit()]
        keep_head = 4 if value.startswith("+") else 3
        for i, ch in enumerate(value):
            out.append(ch if (ch.isdigit() is False or i < keep_head or i >= len(value) - 2) else "*")
        if len(digits) < 5:
            return value
        return "".join(out)


def get_logger(name: str = "app") -> structlog.stdlib.BoundLogger:
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(name))
