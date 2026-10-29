"""Secrets at rest (spec §8, ADR-016).

Why this module exists: integration credentials used to live only in `.env`, so "connecting WhatsApp"
meant editing a file on a server and restarting the process. Moving them into `integration_configs`
makes them manageable — and turns the database into a copy of every token, which is not acceptable
without encryption at rest. Fernet (AES-128-CBC + HMAC-SHA256, authenticate-then-decrypt) is used rather
than a hand-rolled mode: the standard library ships no cipher, and a wrong chain here is a silent
integrity failure, not a crash.

Three rules the rest of the app must not be able to break:

* the only way a secret leaves this module is *to a provider driver*, through
  `decrypt_map()`; no response model contains the ciphertext or the plaintext;
* a value is represented in the API by `MASK` plus `"set": true` — the UI shows dots, never a partial
  prefix, because a 4-character prefix of an app secret is enough to correlate two accounts;
* `decrypt_map` never raises on garbage: a rotated/foreign key must show "not set" (and let the operator
  re-enter the value) rather than 500 the settings screen, which is the one screen needed to fix it;
* rotation is a window, not an event: `SECRETS_ENCRYPTION_KEY_OLD` keeps the retired key readable while new
  writes use the current key, and clearing it is what closes the window (runbook §5).
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import get_settings

MASK = "••••••••"
_NOT_SET = "not_set"
_KDF_INFO = b"broker-hub/secrets/v1"


class SecretConfigError(RuntimeError):
    """The encryption key is unusable — a configuration error, surfaced with the fix in its message."""


def _derive_key(material: bytes) -> bytes:
    digest = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_KDF_INFO).derive(material)
    return base64.urlsafe_b64encode(digest)


def _resolve(raw: str, *, label: str) -> Fernet | None:
    """One configured key entry. A Fernet key is used as-is; a passphrase is stretched with the same HKDF
    rather than handed to AES directly, and is refused below 16 characters so the shortcut cannot be silent."""
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return Fernet(value.encode())
    except (ValueError, binascii.Error):
        if len(value) < 16:
            raise SecretConfigError(f"{label} must be a Fernet key or a passphrase of >=16 chars") from None
        return Fernet(_derive_key(value.encode()))


def _fernet() -> Fernet | MultiFernet:
    settings = get_settings()
    current = _resolve(settings.SECRETS_ENCRYPTION_KEY, label="SECRETS_ENCRYPTION_KEY")
    if current is None:
        if settings.ENV == "prod":
            raise SecretConfigError(
                "SECRETS_ENCRYPTION_KEY is required when ENV=prod: stored integration secrets must not be "
                "protected by the same secret that signs sessions (rotating one would destroy the other)"
            )
        # development and CI: derive from JWT_SECRET so a fresh clone works with zero setup
        current = Fernet(_derive_key(settings.JWT_SECRET.encode()))
    previous = _resolve(settings.SECRETS_ENCRYPTION_KEY_OLD, label="SECRETS_ENCRYPTION_KEY_OLD")
    if previous is None:
        return current
    # rotation window: a blob written under the retired key still decrypts, and every write goes out under
    # the current one (`MultiFernet` semantics). Without this, rotating the key would mean physically
    # re-collecting each vendor token — the step operators skip, which is how keys stay stale for years.
    return MultiFernet([current, previous])


def encrypt_map(values: dict[str, Any]) -> str | None:
    """Serialize + encrypt. `None` for an empty map, so "no secrets" is one representation, not two."""
    clean = {key: str(value) for key, value in values.items() if value not in (None, "")}
    if not clean:
        return None
    return _fernet().encrypt(json.dumps(clean, sort_keys=True).encode()).decode()


def decrypt_map(blob: str | None) -> dict[str, str]:
    if not blob:
        return {}
    try:
        plain = _fernet().decrypt(blob.encode())
        loaded = json.loads(plain.decode())
    except (InvalidToken, ValueError, binascii.Error, UnicodeDecodeError):
        return {}
    return {str(k): str(v) for k, v in loaded.items()} if isinstance(loaded, dict) else {}


def mask_report(secret_names: tuple[str, ...], values: dict[str, str]) -> list[dict[str, Any]]:
    """What a GET is allowed to say about each secret field: its name, whether it is set, and dots."""
    return [{"key": name, "set": bool(values.get(name)), "display": (MASK if values.get(name) else _NOT_SET)} for name in secret_names]
