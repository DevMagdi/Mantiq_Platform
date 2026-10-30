"""structlog JSON logging with correlation id."""

from __future__ import annotations

import logging
import re
import sys
from contextvars import ContextVar
from typing import Any, cast

import structlog

#: Loggers that print the **full request URL** at INFO. `httpx` logging a Graph call is how a user access
#: token and an app secret ended up in a console: `?input_token=…&access_token=<app_id>|<app_secret>`. The
#: redaction below would mask them anyway, but a URL we never print cannot leak — belt and braces, and the
#: reason this is a level change rather than only a formatter.
NOISY_HTTP_LOGGERS: tuple[str, ...] = ("httpx", "httpcore")

#: Query parameters whose value must never reach a log sink.
SECRET_QUERY_PARAMS: tuple[str, ...] = ("access_token", "input_token", "app_secret", "client_secret", "token")

#: `?access_token=abc` → `?access_token=***`; also matches `&`, and a JSON-ish `"access_token": "abc"`.
_SECRET_PARAM = re.compile(r"(?i)(\b(?:" + "|".join(SECRET_QUERY_PARAMS) + r")\b\s*[=:]\s*\"?)([^&\s\"',}]+)")
#: `Authorization: Bearer abc.def` / `bearer abc` → the credential is replaced, the scheme is kept.
_BEARER = re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/=\-]+)")
REDACTED = "***"

correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")


def setup_logging(level: str = "INFO", json_logs: bool = True) -> None:
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_correlation_id,
        RedactSecrets(),
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
    # INFO on these two prints every outbound URL, query string included. WARNING keeps the failures
    # (connection errors, bad responses) without the payload; redaction is the second line of defence.
    for name in NOISY_HTTP_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    # uvicorn's *access* log is the third sink, and the one a structlog processor cannot reach: it formats its
    # own line from `(client, method, full_path, version, status)` and writes it straight to stderr. A client
    # that sends `?access_token=…` — ours no longer does, but a vendor callback or a curl from a terminal
    # might — was printed verbatim. The filter masks the path argument; the formatting is untouched.
    access = logging.getLogger("uvicorn.access")
    access.addFilter(RedactAccessLog())


class RedactAccessLog(logging.Filter):
    """Mask secret query parameters in uvicorn's access line.

    uvicorn logs `'%s - "%s %s HTTP/%s" %d'` with the **full path** as the third argument, so the query string
    lands in the log exactly as received. Only that argument is rewritten — the client, method and status stay
    readable, which is what makes the line useful while debugging.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            patched = list(args)
            patched[2] = redact(args[2])
            record.args = tuple(patched)
        return True


class RedactSecrets:
    """Mask credentials before a line reaches any sink.

    A URL is the usual carrier: `…/debug_token?input_token=<token>&access_token=<app_id>|<app_secret>` puts
    two secrets in a log line, an error report and a screenshot. Both halves are masked here — the query
    parameters by name, and everything after `Bearer ` — because the alternative (remembering to sanitize
    each call site) is a rule that eventually gets missed.
    """

    def __call__(self, _logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        for key, value in list(event_dict.items()):
            if isinstance(value, str):
                event_dict[key] = redact(value)
            elif isinstance(value, (list, tuple)):
                event_dict[key] = type(value)(redact(item) if isinstance(item, str) else item for item in value)
        return event_dict


def redact(text: str) -> str:
    """`redact` is public so a test (and any future sink) can reuse the exact same rules."""
    masked = _SECRET_PARAM.sub(lambda match: f"{match.group(1)}{REDACTED}", text)
    return _BEARER.sub(lambda match: f"{match.group(1)}{REDACTED}", masked)


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
