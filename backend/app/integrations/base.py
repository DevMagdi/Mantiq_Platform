"""Provider protocols (spec §8): one interface per external system.

R9: every protocol here is implemented by a **live** driver. The simulated implementations live in
`integrations/sims.py` and cannot be constructed outside dev/test, so "is this a real provider?" is
answered by the process mode, not by a subclass check.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class WhatsAppProvider(Protocol):
    name: str

    def verify_signature(self, payload: bytes, signature_header: str | None) -> bool: ...
    async def send_text(self, *, to: str, body: str) -> dict[str, Any]: ...
    async def send_template(self, *, to: str, template: str, variables: list[str]) -> dict[str, Any]: ...
    async def fetch_media(self, media_id: str) -> tuple[bytes, str]: ...


@runtime_checkable
class MobileCallsProviderProtocol(Protocol):
    """A provider of calls to the platform's mobile number (R9 §1.C)."""

    name: str

    def verify_signature(self, payload: bytes, signature_header: str | None) -> bool: ...
    async def fetch_recording(self, url: str) -> bytes: ...


@runtime_checkable
class StorageProvider(Protocol):
    def put_object(self, key: str, data: bytes, content_type: str) -> dict[str, Any]: ...
    def get_object(self, key: str) -> bytes: ...
    def delete_object(self, key: str) -> None: ...


@runtime_checkable
class NotifierProvider(Protocol):
    async def send_email(self, *, to: str, subject: str, body: str) -> bool: ...


@runtime_checkable
class AIProvider(Protocol):
    enabled: bool

    async def transcribe(self, object_key: str) -> str: ...
    async def summarize(self, text: str) -> str: ...
    async def classify(self, text: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ProviderResult:
    ok: bool
    external_id: str | None = None
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "message_id": self.external_id, "detail": self.detail}
