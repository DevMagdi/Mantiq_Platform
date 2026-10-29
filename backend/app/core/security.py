"""Passwords, JWTs, webhook signatures, signed media URLs, TOTP."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)

TokenType = Literal["access", "refresh", "password_reset", "mfa"]


def hash_password(password: str) -> str:
    return ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return ph.verify(hashed, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


#: Argon2 is slow on purpose (~110 ms and 64 MiB here) — that is what makes a stolen hash expensive. The
#: cost is the point, so it must not be paid *on the event loop*: one burst of sign-ins then stalls every
#: other request in the process. Measured in R6 before this wrapper: 16 concurrent wrong passwords held
#: `/healthz` for **1.24 s** and the burst took 2.27 s; a CI e2e run lost 21 mobile tests to 20 s timeouts
#: in exactly that window. Four slots bound the worst case at ~256 MiB of Argon2 memory.
_HASH_SLOTS = asyncio.Semaphore(4)


async def hash_password_async(password: str) -> str:
    """`hash_password`, off the event loop (routes call this one)."""
    async with _HASH_SLOTS:
        return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password: str, hashed: str) -> bool:
    """`verify_password`, off the event loop (routes call this one)."""
    async with _HASH_SLOTS:
        return await asyncio.to_thread(verify_password, password, hashed)


def needs_rehash(hashed: str) -> bool:
    try:
        return ph.check_needs_rehash(hashed)
    except InvalidHashError:
        return True


def _make_token(token_type: TokenType, user_id: UUID, extra: dict[str, Any] | None) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    ttl = timedelta(minutes=settings.ACCESS_TOKEN_TTL_MIN) if token_type == "access" else timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "typ": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": secrets.token_urlsafe(12),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def make_access_token(user_id: UUID, perms: tuple[str, ...] = ()) -> str:
    return _make_token("access", user_id, {"perms": sorted(perms)})


def make_refresh_token(user_id: UUID, version: int) -> str:
    return _make_token("refresh", user_id, {"ver": version})


def decode_token(token: str, expected_type: TokenType | None = None) -> dict[str, Any]:
    settings = get_settings()
    payload: dict[str, Any] = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    if expected_type and payload.get("typ") != expected_type:
        raise jwt.InvalidTokenError(f"wrong token type: {payload.get('typ')}")
    return payload


def sign_path(path: str, expires_at: int, secret: str | None = None) -> str:
    key = (secret or get_settings().JWT_SECRET).encode()
    return hmac.new(key, f"{path}:{expires_at}".encode(), hashlib.sha256).hexdigest()


def make_signed_media_url(object_key: str, ttl_seconds: int | None = None) -> tuple[str, int]:
    """Short-lived signed URL for recordings/attachments (spec §6)."""
    settings = get_settings()
    ttl = ttl_seconds or settings.RECORDING_SIGNED_URL_TTL_SECONDS
    expires = int(time.time()) + ttl
    sig = sign_path(object_key, expires, settings.JWT_SECRET)
    return f"/api/v1/media/{object_key}?expires={expires}&sig={sig}", expires


def verify_signed_media_url(object_key: str, expires: int, signature: str) -> bool:
    settings = get_settings()
    if int(time.time()) > int(expires):
        return False
    expected = sign_path(object_key, int(expires), settings.JWT_SECRET)
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(payload: bytes, signature_header: str | None, secret: str) -> bool:
    """`sha256=<hex>` style signature over the raw body (WhatsApp Cloud API compatible).

    An unset secret **refuses**, it does not sign with the empty key: `hmac.new(b"", ...)` is perfectly
    computable, so a live deployment that lost its App Secret would have accepted any body whose
    `X-Hub-Signature-256` was the HMAC of the empty string — a forgeable webhook, not a rejected one (R7).
    """
    if not secret or not signature_header:
        return False
    provided = signature_header.split("=", 1)[-1].strip()
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided.lower())


def totp_now(secret_b32: str, at: float | None = None, step: int = 30) -> str:
    counter = int((at if at is not None else time.time()) // step)
    return _totp_for_counter(secret_b32, counter)


def totp_matches(secret_b32: str, code: str, window: int = 1) -> bool:
    try:  # constant-time-ish: compare all allowed counters
        counter = int(time.time() // 30)
    except Exception:  # pragma: no cover
        return False
    clean = "".join(ch for ch in code if ch.isdigit())
    if len(clean) != 6:
        return False
    return any(hmac.compare_digest(_totp_for_counter(secret_b32, counter + delta), clean) for delta in range(-window, window + 1))


def _totp_for_counter(secret_b32: str, counter: int) -> str:
    try:
        key = base64.b32decode(secret_b32.upper(), casefold=True)
    except Exception:
        key = secret_b32.encode()
    digest = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return f"{binary % 1_000_000:06d}"


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


#: no look-alikes (l/1/I, O/0): a temporary password is read aloud or pasted once, and the account cannot
#: be recovered without it
_TEMP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


def generate_temp_password(length: int = 14) -> str:
    """A throwaway password for an account the owner is expected to change on their first sign-in.

    It clears the 10-character policy with room to spare and is guaranteed to carry an upper, a lower and a
    digit, so no client has to be taught that the policy is already satisfied.
    """
    if length < 10:
        raise ValueError("a generated password must clear the 10-character policy")
    while True:
        value = "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(length))
        if any(c.isupper() for c in value) and any(c.islower() for c in value) and any(c.isdigit() for c in value):
            return value
