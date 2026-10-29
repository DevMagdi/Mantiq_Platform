"""Deterministic AI driver for local development, tests and the acceptance run (spec §8, M10).

Why a simulator instead of a vendor call in the test environment: the spec requires every external
provider to have both a real and a simulated driver behind one protocol, and an AI answer must never
depend on the network inside a test run.

What this driver does **not** do is the point:

* it never invents content that is not in the input — the summary is *extractive* (sentences copied
  from the thread), and `transcribe` only produces placeholder lines;
* it is deterministic, so a transcript for the same recording is byte-identical every time;
* it does not see data outside the text it is handed. That is what makes the flag and the scope check
  in `app/services/ai_svc.py` the security boundary, not provider-side policy.

A real driver (Azure Speech / Whisper / an LLM endpoint) replaces only this file: the protocol in
`app/integrations/base.py` is the contract.
"""

from __future__ import annotations

import re
import zlib
from typing import Any

_SENTENCE = re.compile(r"(?<=[.!?؟])\s+|\n+")
_TURNS = (
    "العميل يسأل عن حالة الطلب والمستندات الناقصة.",
    "الموظف يوضح أن المستند مطلوب من شركة التأمين وموعد الرد متفق عليه.",
    "العميل أكد إرسال المستند اليوم، والطرفان اتفقا على المتابعة هاتفياً.",
)

# intent detection is keyword based on purpose: a wrong guess costs a re-triage, a
# hallucinated one would silently change the SLA clock, and the UI shows it as a suggestion.
_INTENTS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("complaint", ("شكوى", "مستاء", "تصعيد", "مشكلة"), "sla"),
    ("document", ("مستند", "صورة", "رخصة", "بوليصة", "icar", "الملفات"), "sla"),
    ("renewal", ("تجديد", "مدة", "انتهت", "الحلول"), "sla"),
    ("data_update", ("تحديث", "تغيير البيانات", "العنوان", "الرقم", "اسم"), "sla"),
    ("refund", ("استرجاع", "استرداد", "فلوس", "المبلغ"), "sla"),
    ("claim", ("حادثة", "تعويض", "ضرر"), "sla"),
    ("payment", ("قسط", "دفعة", "الفاتورة", "السداد"), "billing"),
)


def _sentences(text: str) -> list[str]:
    return [chunk.strip() for chunk in _SENTENCE.split(text or "") if len(chunk.strip()) > 20]


class AISim:
    """`AIProvider` implementation used by `INTEGRATIONS_DRIVER=sim`."""

    name = "sim"
    enabled = True

    async def transcribe(self, object_key: str) -> str:
        """Placeholder transcript: stable per recording, three turns with fake offsets."""
        seed = zlib.crc32(object_key.encode("utf-8"))
        base = 4 + (seed % 9)
        lines: list[str] = []
        for index, turn in enumerate(_TURNS):
            seconds = base + index * (11 + (seed % 7))
            lines.append(f"{seconds // 60:02d}:{seconds % 60:02d} — {'العميل' if index % 2 == 0 else 'الموظف'}: {turn}")
        return "\n".join(lines)

    async def summarize(self, text: str) -> str:
        """Extractive only: the longest sentences, numbers first, never new wording."""
        candidates = _sentences(text)
        if not candidates:
            return ""
        ranked = sorted(candidates, key=lambda line: (1 if re.search(r"\d", line) else 0, len(line)), reverse=True)
        chosen = ranked[:3]
        # keep the thread order of the chosen sentences so the summary reads chronologically
        chosen.sort(key=candidates.index)
        return " ".join(chosen)

    async def classify(self, text: str) -> dict[str, Any]:
        lowered = (text or "").lower()
        best: tuple[str, int, str] | None = None
        for intent, needles, queue in _INTENTS:
            hits = sum(1 for needle in needles if needle in lowered)
            if hits and (best is None or hits > best[1]):
                best = (intent, hits, queue)
        if best is None:
            return {"intent": "inquiry", "confidence": 0.2, "queue": "sla", "urgency": "normal", "provider": self.name}
        intent, hits, queue = best
        return {
            "intent": intent,
            "confidence": round(min(0.4 + 0.2 * hits, 0.9), 2),
            "queue": queue,
            "urgency": "high" if intent in {"complaint", "claim"} else "normal",
            "provider": self.name,
        }
