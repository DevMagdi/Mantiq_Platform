"""The simulated drivers — **dev/test only** (R9 §1.A).

Before R9 the simulator was the default driver and the registry fell back to it whenever a live provider
was incomplete. That made "production is live" a claim nobody could verify: a missing token produced a
green screen and messages that went nowhere. The two classes below are therefore quarantined:

* they live in this module and nowhere else, so `grep -r "Sim(" app/` answers "where can a simulator be
  built?" in one line;
* their constructors call `require_simulation_allowed()`, which raises `SimulationDisabled` as soon as
  `ENV=prod` (or any `ENV` other than dev/test). A production process therefore cannot instantiate one
  even by mistake — `tests/test_production_live_only.py` asserts exactly that.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from app.core.config import get_settings
from app.integrations.base import ProviderResult


class SimulationDisabled(RuntimeError):
    """A simulated driver was asked for in a process that must only use live providers."""


def simulation_allowed() -> bool:
    return get_settings().simulation_allowed


def require_simulation_allowed(kind: str) -> None:
    settings = get_settings()
    if not settings.simulation_allowed:
        raise SimulationDisabled(
            f"{kind} simulator is not available when ENV={settings.ENV!r}: production serves live providers only "
            "(WhatsApp Cloud API + the mobile-calls webhook). Set the provider's credentials instead."
        )


class WhatsAppSim:
    """Records outbound sends in memory so dev/test E2E flows can assert on them."""

    name = "sim"
    sent: list[dict[str, Any]] = []

    def __init__(self) -> None:
        require_simulation_allowed("WhatsApp")

    def verify_signature(self, payload: bytes, signature_header: str | None) -> bool:
        return True  # the simulator signs nothing; the dev endpoints are dev-only

    async def send_text(self, *, to: str, body: str) -> dict[str, Any]:
        result = ProviderResult(ok=True, external_id=f"wamid.sim_{uuid.uuid4().hex[:16]}").as_dict()
        WhatsAppSim.sent.append({"to": to, "body": body, "at": time.time(), **result})
        return result

    async def send_template(self, *, to: str, template: str, variables: list[str]) -> dict[str, Any]:
        result = ProviderResult(ok=True, external_id=f"wamid.sim_{uuid.uuid4().hex[:16]}").as_dict()
        WhatsAppSim.sent.append({"to": to, "template": template, "variables": variables, **result})
        return result

    async def send_document(self, *, to: str, content: bytes, filename: str, mime: str, caption: str | None = None) -> dict[str, Any]:
        result = ProviderResult(ok=True, external_id=f"wamid.sim_{uuid.uuid4().hex[:16]}").as_dict()
        WhatsAppSim.sent.append({"to": to, "document": filename, "bytes": len(content), **result})
        return result

    async def fetch_media(self, media_id: str) -> tuple[bytes, str]:
        return b"SIM-AUDIO", "audio/ogg"

    @classmethod
    def reset(cls) -> None:
        cls.sent = []


class MobileCallsSim:
    """The mobile-calls simulator: a playable silence file, no network at all."""

    name = "sim"

    def __init__(self) -> None:
        require_simulation_allowed("Mobile calls")

    def verify_signature(self, payload: bytes, signature_header: str | None) -> bool:
        return True

    async def fetch_recording(self, url: str) -> bytes:
        # 44-byte WAV header + silence: a real, playable file the <audio> element accepts.
        from app.core.storage_bytes import silence_wav

        _ = url
        return silence_wav(seconds=2)

    async def probe(self, url: str | None) -> tuple[bool, str]:
        return True, "وضع المحاكاة: لم يخرج أي نداء إلى الشبكة."
