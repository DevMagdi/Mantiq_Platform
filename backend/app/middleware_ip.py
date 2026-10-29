"""Request-scoped client IP for audit rows (contextvar set by the HTTP middleware)."""

from __future__ import annotations

from contextvars import ContextVar

_client_ip: ContextVar[str | None] = ContextVar("client_ip", default=None)


def set_client_ip(value: str | None) -> None:
    _client_ip.set(value)


def ip_of() -> str | None:
    return _client_ip.get()
