"""Egyptian mobile normalisation to E.164 (single source of truth for matching callers)."""

from __future__ import annotations

import re

_NON_DIGITS = re.compile(r"\D+")
_EG_MOBILE_E164 = re.compile(r"^201[0125]\d{8}$")  # without the leading '+'
_EG_NATIONAL = re.compile(r"^01[0125]\d{8}$")


def _digits(value: str | None) -> str:
    return _NON_DIGITS.sub("", value or "")


def normalize_mobile(raw: str | None) -> str | None:
    """Return E.164 (`+201001234567`) for Egyptian mobiles; keep other E.164 as-is.

    Accepts `01001234567`, `+201001234567`, `+20 100 1234 567`, `0020…`, `20100…` with
    spaces/dashes/parens anywhere. Returns None when unusable — the caller decides whether to
    reject (manual entry) or file as an unknown caller (webhook intake).
    """
    value = _digits(raw)
    if not value:
        return None
    if value.startswith("00"):
        value = value[2:]
    if _EG_NATIONAL.match(value):
        return "+20" + value[1:]
    if _EG_MOBILE_E164.match(value):
        return "+" + value
    if re.match(r"^1[0125]\d{8}$", value):  # bare subscriber number, no trunk code
        return "+20" + value
    if len(value) >= 8 and not value.startswith("0"):  # non-Egyptian, assume country-coded
        return "+" + value
    return None


def is_egyptian_mobile(normalized: str | None) -> bool:
    return bool(normalized and _EG_MOBILE_E164.match(normalized[1:]))


def display_mobile(normalized: str | None) -> str:
    """Grouped form for RTL tables: +20 100 1234 567."""
    value = _digits(normalized)
    if _EG_MOBILE_E164.match(value):
        return f"+20 {value[2:5]} {value[5:8]} {value[8:12]}"
    return normalized or ""


def mask_mobile(normalized: str | None) -> str:
    """Partially hidden form for logs and notifications (data-protection policy)."""
    value = _digits(normalized)
    if len(value) < 6:
        return "***"
    return f"+{value[:5]}{'*' * (len(value) - 7)}{value[-2:]}" if value else "***"


def phones_match(a: str | None, b: str | None) -> bool:
    na, nb = normalize_mobile(a), normalize_mobile(b)
    return bool(na and nb and na == nb)
