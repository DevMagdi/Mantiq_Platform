"""Arabic RTL rendering for the documents the product hands to a customer (spec §2, ADR-021).

An issued policy is a legal artefact, so "download the record" is not enough: what the client keeps is a
file. This module produces that file — a one-page A4 PDF, right-to-left, with the font embedded — from a
plain `PolicyDocumentData`, so the same function serves the staff screen and the portal.

Two mechanics are worth knowing before changing anything here:

* Arabic needs **shaping** (`arabic_reshaper` maps a word to its contextual presentation forms) and
  **reordering** (`python-bidi` applies the Unicode bidi algorithm). Skipping them renders the letters
  disconnected and in reverse; both are applied in `_rtl`.
* The font is **bundled** (`app/assets/fonts/DejaVuSans.ttf`) rather than taken from the system, because
  the runtime image, the CI runner and a laptop have different font sets, and a document that raises
  `TTFError` on one of them is not a document.

No provider is touched here: this is deterministic bytes in, deterministic bytes out, which is what makes
it testable and what makes the checksum stored next to the file meaningful.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import arabic_reshaper
from bidi.algorithm import get_display
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_REGULAR = "DejaVuSans"
_BOLD = "DejaVuSans-Bold"

#: ink colours — the same values as `frontend/src/styles/tokens.css` (ADR-034), so a printed page and the
#: screen it came from are the same document. `_ACCENT` is a fill only (the letterhead rule), never body text.
_INK = HexColor("#16273B")  # Mantiq Navy
_INK_STRONG = HexColor("#0E1728")  # Deep Navy — the product wordmark
_MUTED = HexColor("#687380")  # navy at 65% on white — matches --color-muted
_LINE = HexColor("#E4E7EA")  # rgba(22,39,59,.10) flattened on white
_ACCENT = HexColor("#BDA77E")  # Mantiq Gold
_ROW_TINT = HexColor("#FAFBFC")  # --color-bg — the zebra stripe
_LEGAL_NAME = "Mantiq Technologies"
#: the mark, rasterised by `frontend/scripts/generate-brand-icons.py` (reportlab cannot read SVG)
_MARK_PNG = _FONT_DIR.parent / "brand" / "mantiq-mark.png"


@lru_cache(maxsize=1)
def _fonts() -> None:
    """Register the bundled faces once per process (reportlab keeps a global registry)."""
    pdfmetrics.registerFont(TTFont(_REGULAR, str(_FONT_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont(_BOLD, str(_FONT_DIR / "DejaVuSans-Bold.ttf")))


def _rtl(text: str | None) -> str:
    """Shape + reorder one right-to-left line. Empty stays empty rather than becoming a stray dash."""
    if not text:
        return "—"
    shaped: str = get_display(arabic_reshaper.reshape(str(text)))
    return shaped


_ARABIC = range(0x0600, 0x0700)


def _value(text: str) -> str:
    """A table *value*: shaped only when it actually carries Arabic.

    The label column is Arabic by construction, but a value may be `BH-2026-0007`, `275,000.00 EGP` or a
    company name. Running an ASCII code through the shaper is harmless, but running Arabic through
    `drawString` untouched prints the letters reversed and disconnected — which is exactly how the first
    render of this document looked.
    """
    return _rtl(text) if any(ord(char) in _ARABIC for char in text) else text


def _plan(value: str | None) -> str:
    """The payment plan in Arabic on an Arabic document; an unknown value is printed as it arrived."""
    if not value:
        return "—"
    return {
        "monthly": "شهري",
        "quarterly": "ربع سنوي",
        "semiannual": "نصف سنوي",
        "cash": "دفعة واحدة",
    }.get(str(value).lower(), str(value))


def _fmt_date(value: date | None) -> str:
    # ISO dates are unambiguous in both directions and need no locale data on the server
    return value.isoformat() if value else "—"


def _money(amount: Any, currency: str) -> str:
    if amount in (None, ""):
        return "—"
    try:
        return f"{float(amount):,.2f} {currency}"
    except (TypeError, ValueError):
        return f"{amount} {currency}"


@dataclass(frozen=True)
class PolicyDocumentData:
    """Everything that appears on the page. Missing optional fields render as `—`, never as a blank gap."""

    policy_no: str
    client_name: str
    insurance_type: str | None = None
    insurer: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    issue_date: date | None = None
    premium: Any = None
    currency: str = "EGP"
    headcount: int | None = None
    payment_plan: str | None = None
    broker_name: str = "Mantiq"
    broker_contact: str | None = None
    reference: str | None = None


def render_policy_pdf(data: PolicyDocumentData) -> bytes:
    """A4, one page, right-to-left. Deterministic for the same input (no timestamps injected here)."""
    _fonts()
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    margin = 46
    right = width - margin
    left = margin
    cursor = height - margin

    # --- letterhead -------------------------------------------------------------------------
    page.setFillColor(_ACCENT)
    page.rect(0, height - 14, width, 14, stroke=0, fill=1)
    # mark on the inline-start edge (left for this RTL page's letterhead block), wordmark to its right
    mark_size = 27
    if _MARK_PNG.exists():
        # sits level with the wordmark's cap height, never on the subtitle line below it
        page.drawImage(str(_MARK_PNG), left, cursor + 1, mark_size, mark_size, mask="auto")
    page.setFillColor(_INK_STRONG)
    page.setFont(_BOLD, 21)
    page.drawRightString(right, cursor, f"{data.broker_name}")
    page.setFont(_REGULAR, 9)
    page.setFillColor(_MUTED)
    page.drawRightString(right, cursor - 13, _LEGAL_NAME)
    cursor -= 22
    page.setFont(_REGULAR, 11.5)
    page.setFillColor(_MUTED)
    page.drawRightString(right, cursor, _rtl("شهادة تأمين — وثيقة صادرة"))
    page.setFont(_REGULAR, 9)
    page.drawString(left, cursor, f"Policy certificate · {data.policy_no}")
    cursor -= 16
    page.setStrokeColor(_LINE)
    page.setLineWidth(1)
    page.line(left, cursor, right, cursor)
    cursor -= 30

    page.setFillColor(_INK)
    page.setFont(_BOLD, 15)
    page.drawRightString(right, cursor, _rtl("بيانات الوثيقة"))
    cursor -= 24

    rows: list[tuple[str, str]] = [
        ("اسم المؤمن له", data.client_name),
        ("رقم الوثيقة", data.policy_no),
        ("نوع التأمين", data.insurance_type or "—"),
        ("شركة التأمين", data.insurer or "—"),
        ("بداية التغطية", _fmt_date(data.start_date)),
        ("نهاية التغطية", _fmt_date(data.end_date)),
        ("تاريخ الإصدار", _fmt_date(data.issue_date)),
        ("القسط السنوي", _money(data.premium, data.currency)),
        ("طريقة السداد", _plan(data.payment_plan)),
        ("عدد المؤمَّن عليهم", str(data.headcount) if data.headcount else "—"),
    ]
    if data.reference:
        rows.append(("الرقم المرجعي", data.reference))

    row_height = 30
    for index, (label, value) in enumerate(rows):
        if index % 2 == 0:
            page.setFillColor(_ROW_TINT)
            page.rect(left, cursor - 21, right - left, row_height - 6, stroke=0, fill=1)
        page.setFillColor(_MUTED)
        page.setFont(_REGULAR, 10.5)
        page.drawRightString(right - 10, cursor - 12, _rtl(label))
        page.setFillColor(_INK)
        page.setFont(_BOLD, 11)
        # values are drawn left-aligned: numbers, dates and reference codes are Latin/LTR, and an
        # RTL-aligned Latin string in an Arabic row reads from the wrong end
        page.drawString(left + 12, cursor - 12, _value(str(value)))
        cursor -= row_height

    # --- contact block ----------------------------------------------------------------------
    cursor -= 16
    page.setStrokeColor(_LINE)
    page.line(left, cursor, right, cursor)
    cursor -= 22
    page.setFillColor(_INK)
    page.setFont(_BOLD, 11)
    page.drawRightString(right, cursor, _rtl("للتواصل بخصوص هذه الوثيقة"))
    cursor -= 17
    page.setFont(_REGULAR, 10.5)
    page.setFillColor(_MUTED)
    if data.broker_contact:
        page.drawRightString(right, cursor, _rtl(data.broker_contact))
        cursor -= 15
    page.drawRightString(right, cursor, _rtl(data.broker_name))

    # --- footer ------------------------------------------------------------------------------
    page.setFont(_REGULAR, 8.5)
    page.setFillColor(_MUTED)
    page.drawString(left, margin - 8, f"Reference: {data.policy_no}")
    page.drawRightString(right, margin - 8, _rtl(f"هذه الوثيقة صادرة إلكترونيًا — {_LEGAL_NAME}"))

    page.showPage()
    page.save()
    return buffer.getvalue()
