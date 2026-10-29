"""WhatsApp **live** driver: Meta WhatsApp Business Cloud API.

This module speaks the provider dialect only; inbound handling is shared (`services/intake.py`). The
simulated driver lives in `integrations/sims.py` and cannot be constructed outside dev/test (R9 §1.A), so
there is no code path here that quietly records a message instead of sending it.

Going live needs all three of: `access_token`, `phone_number_id`, `app_secret`. The first two send, the
third verifies — and an empty `app_secret` refuses every webhook rather than signing with the empty key.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import verify_webhook_signature
from app.integrations.base import ProviderResult

log = get_logger("integrations.whatsapp")

#: `None` means the real network. A contract test sets a `httpx.MockTransport` so the *live* code path
#: (URL, bearer token, payload shape, error mapping) is what runs — swapping the driver for the simulator
#: would assert nothing about the code that ships.
_transport: httpx.AsyncBaseTransport | None = None


def use_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    global _transport
    _transport = transport


def _client(timeout: float = 15.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, transport=_transport)


class WhatsAppMeta:
    """Meta WhatsApp Business Cloud API (v21+).

    Credentials arrive from `integrations/registry.py`, which reads the saved settings row and falls back
    to the environment — so the repo and the container env stay secret-free either way. An empty
    `app_secret` means "no row said otherwise", and the environment value is used.
    """

    name = "meta"
    BASE = "https://graph.facebook.com/v21.0"

    def __init__(self, token: str, phone_number_id: str, app_secret: str = "") -> None:
        self.token = token
        self.phone_number_id = phone_number_id
        self.app_secret = app_secret or get_settings().WHATSAPP_APP_SECRET

    def verify_signature(self, payload: bytes, signature_header: str | None) -> bool:
        return verify_webhook_signature(payload, signature_header, self.app_secret)

    async def send_text(self, *, to: str, body: str) -> dict[str, Any]:
        async with _client() as client:
            response = await client.post(
                f"{self.BASE}/{self.phone_number_id}/messages",
                headers={"Authorization": f"Bearer {self.token}"},
                json={"messaging_product": "whatsapp", "to": to.lstrip("+"), "type": "text", "text": {"body": body}},
            )
        if response.status_code >= 400:
            return ProviderResult(ok=False, detail=response.text[:300]).as_dict()
        data = response.json()
        return ProviderResult(ok=True, external_id=data.get("messages", [{}])[0].get("id")).as_dict()

    async def send_template(self, *, to: str, template: str, variables: list[str]) -> dict[str, Any]:
        payload = {
            "messaging_product": "whatsapp",
            "to": to.lstrip("+"),
            "type": "template",
            "template": {
                "name": template,
                "language": {"code": "ar"},
                "components": [{"type": "body", "parameters": [{"type": "text", "text": v} for v in variables]}] if variables else [],
            },
        }
        async with _client() as client:
            response = await client.post(
                f"{self.BASE}/{self.phone_number_id}/messages",
                headers={"Authorization": f"Bearer {self.token}"},
                json=payload,
            )
        ok = response.status_code < 400
        external = response.json().get("messages", [{}])[0].get("id") if ok else None
        return ProviderResult(ok=bool(ok), external_id=external, detail=None if ok else response.text[:300]).as_dict()

    async def send_document(self, *, to: str, content: bytes, filename: str, mime: str, caption: str | None = None) -> dict[str, Any]:
        """Upload-then-send a document (Graph media upload → messages/document). Failure is returned, never raised silently."""
        async with _client(timeout=30) as client:
            upload = await client.post(
                f"{self.BASE}/{self.phone_number_id}/media",
                headers={"Authorization": f"Bearer {self.token}"},
                data={"messaging_product": "whatsapp", "type": mime},
                files={"file": (filename, content, mime)},
            )
            if upload.status_code >= 400:
                return ProviderResult(ok=False, detail=f"media upload: {upload.text[:200]}").as_dict()
            media_id = upload.json().get("id")
            response = await client.post(
                f"{self.BASE}/{self.phone_number_id}/messages",
                headers={"Authorization": f"Bearer {self.token}"},
                json={
                    "messaging_product": "whatsapp",
                    "to": to.lstrip("+"),
                    "type": "document",
                    "document": {"id": media_id, "filename": filename, **({"caption": caption} if caption else {})},
                },
            )
        if response.status_code >= 400:
            return ProviderResult(ok=False, detail=response.text[:300]).as_dict()
        data = response.json()
        return ProviderResult(ok=True, external_id=data.get("messages", [{}])[0].get("id")).as_dict()

    async def fetch_media(self, media_id: str) -> tuple[bytes, str]:
        async with _client(timeout=30) as client:
            meta = (await client.get(f"{self.BASE}/{media_id}", headers={"Authorization": f"Bearer {self.token}"})).json()
            url = meta.get("url", "")
            media = await client.get(url, headers={"Authorization": f"Bearer {self.token}"})
        return media.content, meta.get("mime_type", "application/octet-stream")


def _body_of(kind: str | None, payload: dict[str, Any]) -> str | None:
    """text → body; audio/image/document → caption; contacts → name."""
    if kind == "text":
        return payload.get("body")
    return payload.get("caption") or payload.get("filename") or payload.get("username")


def parse_inbound(raw: bytes) -> list[dict[str, Any]]:
    """Flatten the Cloud API webhook payload into normalized inbound messages."""
    try:
        body = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return []
    out: list[dict[str, Any]] = []
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            contacts = {c.get("wa_id"): c.get("profile", {}).get("name") for c in value.get("contacts", [])}
            for message in value.get("messages", []):
                kind = message.get("type", "text")
                payload = message.get(kind, {}) or {}
                out.append(
                    {
                        "external_id": message.get("id"),
                        "mobile": f"+{message.get('from')}" if message.get("from") else None,
                        "sender_name": contacts.get(message.get("from")),
                        "body": _body_of(message.get("type"), payload),
                        "media_kind": None if kind == "text" else kind,
                        "media_id": payload.get("id") if kind != "text" else None,
                        "filename": payload.get("filename"),
                        "mime_type": payload.get("mime_type"),
                        "duration_s": int(payload["duration"]) if str(payload.get("duration", "")).isdigit() else None,
                        "status": None,
                    }
                )
            for status in value.get("statuses", []):
                out.append(
                    {
                        "external_id": status.get("id"),
                        "mobile": f"+{status.get('recipient_id')}" if status.get("recipient_id") else None,
                        "status": status.get("status"),
                        "body": None,
                        "media_kind": None,
                        "media_id": None,
                        "duration_s": None,
                        "sender_name": None,
                    }
                )
    return [m for m in out if m.get("external_id")]
