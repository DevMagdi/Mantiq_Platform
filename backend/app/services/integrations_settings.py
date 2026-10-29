"""Reading, writing, testing and switching the integration providers (spec §8, ADR-016, R9).

R9 narrows what this screen is allowed to offer in production: the two channels are **live only** —
WhatsApp Business (Meta Cloud API) and **mobile phone calls** («مكالمات الموبايل») — and a provider's badge is
`Live` / `جاهز` / `غير مُعد` / `فشل آخر اختبار`. There is no «محاكاة» state to choose: simulation exists only
in a dev/test process (`core/config.py`), and `set_live` refuses to switch a provider *off* when `ENV=prod`.

The rules this module exists to enforce:

* **a secret never comes back out through a read.** `state()` returns the config values plus, for each
  secret field, only `{"set": bool, "display": "••••••••" | "not_set"}`. The single function that hands
  decrypted values onward is `resolved_for_registry()`, which feeds driver objects and is not reachable
  from an endpoint.
* **a blank means "keep", not "erase".** A form round-trips a config whose secrets it cannot see, so a
  half-filled save must never delete a production token. Removal is an explicit `null`.
* **live needs a green test and an explicit confirmation.** A wrong token is recoverable; a WhatsApp
  number that starts answering customers from a mis-typed credential is a support incident.
* **every write is audited** with masked before/after, and every test is recorded on the row so the screen
  can answer «آخر اختبار» without grepping a server.

Precedence: a stored row wins, otherwise the environment, otherwise the declared default — see
`effective_config`, which is what `integrations/registry.py` primes itself from.
"""

from __future__ import annotations

import functools
import hashlib
import hmac
import smtplib
import ssl
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import Conflict, ValidationFailed
from app.core.rate_limit import test_limiter
from app.core.secrets import decrypt_map, encrypt_map, mask_report
from app.models.auth import User
from app.models.integrations import PROVIDERS, IntegrationConfig, WebhookEvent
from app.schemas.common import EmailAddress
from app.services import audit

_EMAIL = TypeAdapter(EmailAddress)


class IncompleteConfig(Conflict):
    """The row is saved, but not enough of it exists to go live — `409 incomplete_config`."""

    code = "incomplete_config"


# ---------------------------------------------------------------------------------------------
# the field tables: one description of what each provider stores, validates and masks
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldSpec:
    key: str
    kind: str = "str"  # str | int | bool | email | choice
    default: Any = ""
    required_for_live: bool = False
    choices: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None


#: Only fields something in the code actually reads are offered. A setting nobody consumes is a lie with a
#: toggle on it: it survives a refactor by doing nothing, and the operator believes it did something. So
#: `receive_media` (webhook ingest), `recording_retention_days` (the beat task) and each driver's own
#: knobs are here, while `caller_id`, `log_lead_activity`, `auto_create_request`, `link_window_hours` and
#: the signed-URL TTL are deliberately **not** — see ADR-016 §8.
PROVIDER_FIELDS: dict[str, tuple[FieldSpec, ...]] = {
    "whatsapp": (
        FieldSpec("phone_number_id", required_for_live=True),
        FieldSpec("business_account_id"),
        FieldSpec("receive_media", "bool", True),
    ),
    #: Mobile phone calls (R9 §1.C). The key stays `telephony` — it is a stored contract and every existing
    #: row, webhook URL and test refers to it — but nothing user-facing calls it a «سنترال» any more.
    "telephony": (
        #: the provider dialect. `twilio` (Programmable Voice) is the documented live webhook shape;
        #: `generic` is the plain JSON contract. `sim` is gone: it was a driver choice in data, which is
        #: how a production row could say "simulate my customer calls" (R9 §4).
        FieldSpec("provider", "choice", "twilio", choices=("twilio", "generic")),
        #: the mobile-calls provider's base URL. Read twice: the connection test probes it, and
        #: `fetch_recording` refuses a recording URL on any other host (ADR-023 §4).
        #: required for live: without it the connection test has nothing to probe and `fetch_recording`
        #: refuses every URL, so "live" would mean "silently receiving nothing" (ADR-027)
        FieldSpec("provider_url", required_for_live=True),
        #: the mobile number the platform receives calls on; printed on the card so the operator can compare
        #: it with what the provider console says, and stored so a support call starts from the same value
        FieldSpec("number"),
        FieldSpec("trunk"),
        FieldSpec("recording_retention_days", "int", 180, minimum=1, maximum=3650),
    ),
    "email": (
        # 0 means "not set" — `effective_config` fills the pair from the environment's DSN, so the screen
        # cannot show a host different from the one the mailer would dial.
        FieldSpec("host", required_for_live=True),
        FieldSpec("port", "int", 0, minimum=0, maximum=65535),
        FieldSpec("username"),
        FieldSpec("encryption", "choice", "none", choices=("none", "starttls", "ssl")),
        FieldSpec("from_name", default="Broker Hub"),
        FieldSpec("from_email", "email", "no-reply@brokerhub.local", required_for_live=True),
        FieldSpec("reply_to", "email", ""),
    ),
}

#: masked on every read; `null` on a write deletes, a blank leaves untouched
PROVIDER_SECRETS: dict[str, tuple[str, ...]] = {
    "whatsapp": ("access_token", "app_secret", "verify_token"),
    "telephony": ("webhook_secret", "api_key"),
    "email": ("password", "inbound_secret"),
}

#: which environment variable each field falls back to (ADR-016 §4)
ENV_FALLBACK: dict[str, dict[str, str]] = {
    "whatsapp": {
        "access_token": "WHATSAPP_ACCESS_TOKEN",
        "app_secret": "WHATSAPP_APP_SECRET",
        "verify_token": "WHATSAPP_VERIFY_TOKEN",
        "phone_number_id": "WHATSAPP_PHONE_NUMBER_ID",
        "business_account_id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
    },
    "telephony": {
        "provider_url": "TELEPHONY_WEBHOOK_URL",
        "webhook_secret": "TELEPHONY_WEBHOOK_SECRET",
        "api_key": "TELEPHONY_API_KEY",
        "provider": "TELEPHONY_PROVIDER",
    },
    "email": {
        "host": "SMTP_HOST",
        "port": "SMTP_PORT",
        "username": "SMTP_USERNAME",
        "password": "SMTP_PASSWORD",
        "encryption": "SMTP_ENCRYPTION",
        "from_name": "SMTP_FROM_NAME",
        "from_email": "EMAIL_FROM",
        "reply_to": "SMTP_REPLY_TO",
        "inbound_secret": "EMAIL_INBOUND_SECRET",
    },
}

#: the credential whose absence makes a provider unusable — checked when switching to live
PRIMARY_SECRET = {"whatsapp": "access_token", "telephony": "webhook_secret", "email": "password"}
#: R9 §1.B: WhatsApp live needs *both* halves — the token that sends and the App Secret that verifies. A
#: live row with no App Secret answers 403 to every inbound message, which is a deaf channel, not a live one.
LIVE_REQUIRED_SECRETS: dict[str, tuple[str, ...]] = {
    "whatsapp": ("access_token", "app_secret"),
    "telephony": ("webhook_secret",),
    # inbound_secret is deliberately not required here: an outbound-only SMTP setup is connected
    # for replies; the inbound webhook refuses unsigned deliveries until its own secret is set (R12 §D)
    "email": ("password",),
}

SIM_TEMPLATES = (
    {"name": "policy_reminder_ar", "language": "ar", "status": "APPROVED"},
    {"name": "renewal_notice_ar", "language": "ar", "status": "APPROVED"},
    {"name": "docs_needed_ar", "language": "ar", "status": "PENDING"},
)


def defaults(provider: str) -> dict[str, Any]:
    return {spec.key: spec.default for spec in PROVIDER_FIELDS[provider]}


def _field_error(field_name: str, message: str, kind: str = "invalid") -> ValidationFailed:
    return ValidationFailed(
        "Some fields need attention",
        details=[{"field": field_name, "message": message, "type": kind}],
    )


def _coerce(spec: FieldSpec, value: Any) -> Any:
    if value is None:
        return spec.default
    if spec.kind == "int":
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            raise _field_error(spec.key, "رقم صحيح مطلوب") from None
        if spec.minimum is not None and number < spec.minimum:
            raise _field_error(spec.key, f"الأقل {spec.minimum}")
        if spec.maximum is not None and number > spec.maximum:
            raise _field_error(spec.key, f"الأكثر {spec.maximum}")
        return number
    if spec.kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str) and value.strip().lower() in {"true", "false", "1", "0", "on", "off", "yes", "no"}:
            return value.strip().lower() in {"true", "1", "on", "yes"}
        raise _field_error(spec.key, "قيمة صح/خطأ مطلوبة")
    if spec.kind == "choice":
        text = str(value).strip()
        if text not in spec.choices:
            raise _field_error(spec.key, " / ".join(spec.choices))
        return text
    if spec.kind == "email":
        text = str(value).strip()
        if not text:
            return ""
        try:
            return _EMAIL.validate_python(text)
        except ValidationError:
            raise _field_error(spec.key, "بريد إلكتروني صحيح مطلوب") from None
    return str(value).strip()[:512]


def validate_config(provider: str, candidate: dict[str, Any] | None) -> dict[str, Any]:
    """Coerce + range-check every declared field. Unknown keys are rejected by `update`, not silently kept."""
    values = dict(candidate or {})
    return {spec.key: _coerce(spec, values.get(spec.key, spec.default)) for spec in PROVIDER_FIELDS[provider]}


# ---------------------------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------------------------


@dataclass
class State:
    """Everything the settings screen is allowed to know about one provider."""

    provider: str
    config: dict[str, Any] = field(default_factory=dict)
    secrets: list[dict[str, Any]] = field(default_factory=list)
    is_live: bool = False
    status: str = "unset"  # live | ready | unset | error — the badge in the card header (R9 §1.D)
    last_tested_at: str | None = None
    last_test_status: str | None = None
    last_test_detail: str | None = None
    updated_at: str | None = None
    # Connection semantics (R12 §A): the only states an operator acts on. Saving valid credentials
    # connects; missing/invalid credentials are named, never silently simulated.
    connection_status: str = "not_connected"  # connected | not_connected | error
    missing_fields: list[str] = field(default_factory=list)
    last_webhook_at: str | None = None
    last_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "config": self.config,
            "secrets": self.secrets,
            "is_live": self.is_live,
            "status": self.status,
            "connection_status": self.connection_status,
            "missing_fields": self.missing_fields,
            "last_webhook_at": self.last_webhook_at,
            "last_error": self.last_error,
            "last_tested_at": self.last_tested_at,
            "last_test_status": self.last_test_status,
            "last_test_detail": self.last_test_detail,
            "updated_at": self.updated_at,
        }


async def _row(session: AsyncSession, provider: str) -> IntegrationConfig | None:
    return (await session.execute(select(IntegrationConfig).where(IntegrationConfig.provider == provider))).scalar_one_or_none()


def _env_value(provider: str, key: str) -> Any:
    env_name = ENV_FALLBACK.get(provider, {}).get(key)
    if not env_name:
        return None
    return getattr(get_settings(), env_name, None)


def _stored_config(provider: str, row: IntegrationConfig | None) -> dict[str, Any]:
    if row is None or not row.config:
        return {}
    return {key: value for key, value in dict(row.config).items() if key in defaults(provider)}


def effective_config(provider: str, row: IntegrationConfig | None) -> dict[str, Any]:
    """defaults → environment → stored row. The env layer is what keeps a deployment that only edited
    `.env` working, and it is also why a blank form field does not silently disable a credential the
    server already has."""
    merged = defaults(provider)
    for key in merged:
        env_value = _env_value(provider, key)
        if env_value not in (None, ""):
            merged[key] = env_value
    merged.update(_stored_config(provider, row))
    if provider == "telephony" and str(merged.get("provider") or "").strip().lower() == "sim":
        # A row saved before R9 says `provider: sim`; `sim` is no longer a choice, and re-validating a legacy
        # row must not break the boot. The value is normalized on read, and the next save writes `twilio`.
        merged["provider"] = "twilio"
    if provider == "email":
        # The mailer dials `smtp_endpoint`; showing anything else here would be a second truth about the
        # same server, so an unset host/port is filled from it rather than left blank.
        host, port = get_settings().smtp_endpoint
        if not merged.get("host"):
            merged["host"] = host
        if not merged.get("port"):
            merged["port"] = port
    for key, value in merged.items():
        spec = next(spec for spec in PROVIDER_FIELDS[provider] if spec.key == key)
        merged[key] = _coerce(spec, value)
    return merged


def secrets_for(provider: str, row: IntegrationConfig | None) -> dict[str, str]:
    """Stored secrets, topped up from the environment for anything not stored."""
    out = {key: value for key, value in decrypt_map(row.secrets_encrypted if row else None).items() if value}
    for key in PROVIDER_SECRETS[provider]:
        if key not in out:
            env_value = _env_value(provider, key)
            if env_value:
                out[key] = str(env_value)
    return out


async def state(session: AsyncSession, provider: str) -> State:
    _assert_provider(provider)
    row = await _row(session, provider)
    out = State(
        provider=provider,
        config=effective_config(provider, row),
        secrets=mask_report(PROVIDER_SECRETS[provider], secrets_for(provider, row)),
        is_live=bool(row.is_live) if row else False,
        last_tested_at=row.last_tested_at.astimezone(UTC).isoformat() if row and row.last_tested_at else None,
        last_test_status=row.last_test_status if row else None,
        last_test_detail=row.last_test_detail if row else None,
        updated_at=row.updated_at.astimezone(UTC).isoformat() if row and row.updated_at else None,
    )
    out.status = _status_of(out)
    # connection semantics (R12 §A): missing credentials are named, a failed test is an error,
    # and a fully credentialed provider is connected — there is no operator "go live" step any more.
    missing: list[str] = []
    for spec in PROVIDER_FIELDS[provider]:
        if spec.required_for_live and str(out.config.get(spec.key) or "").strip() in ("", "0"):
            missing.append(spec.key)
    set_secrets = {item["key"] for item in out.secrets if item["set"]}
    missing.extend(key for key in LIVE_REQUIRED_SECRETS.get(provider, ()) if key not in set_secrets)
    out.missing_fields = missing
    from app.models.integrations import WebhookEvent

    latest_ok = (
        (
            await session.execute(
                select(WebhookEvent)
                .where(WebhookEvent.provider == provider, WebhookEvent.status == "ok")
                .order_by(WebhookEvent.occurred_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    latest_err = (
        (
            await session.execute(
                select(WebhookEvent)
                .where(WebhookEvent.provider == provider, WebhookEvent.status.in_(["error", "rejected"]))
                .order_by(WebhookEvent.occurred_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    out.last_webhook_at = latest_ok.occurred_at.astimezone(UTC).isoformat() if latest_ok else None
    out.last_error = (latest_err.detail or latest_err.status) if latest_err else None
    out.connection_status = "error" if out.last_test_status == "failed" else ("connected" if not missing else "not_connected")
    return out


def _status_of(state: State) -> str:
    """`live | ready | unset | error` — the three words the production screen shows (R9 §1.D).

    R9 renamed `sim` to `ready`: a configured provider that is not live is «جاهز» (tested, credentials
    saved, one switch away), not "simulated". Simulation is a process mode (dev/test), never a provider
    state an operator can see in production.
    """
    if state.last_test_status == "failed":
        return "error"
    if state.is_live:
        return "live"
    if any(item["set"] for item in state.secrets):
        return "ready"
    return "unset"


async def all_states(session: AsyncSession) -> list[dict[str, Any]]:
    return [(await state(session, provider)).as_dict() for provider in PROVIDERS]


def webhook_urls() -> dict[str, Any]:
    settings = get_settings()
    base = settings.PUBLIC_BASE_URL.rstrip("/") or "http://localhost:8000"
    prefix = settings.API_V1_PREFIX
    return {
        "base_url": base,
        "items": [
            {
                "provider": "whatsapp",
                "label": "WhatsApp Webhooks",
                "url": f"{base}{prefix}/webhooks/whatsapp",
                "note": "Meta → App dashboard → WhatsApp → Configuration: callback URL, ثم verify token نفسه هنا.",
            },
            {
                "provider": "telephony",
                "label": "مكالمات الموبايل (Mobile calls)",
                "url": f"{base}{prefix}/webhooks/mobile-calls",
                #: the pre-R9 path still answers (see `endpoints/webhooks.py`), so a console that was already
                #: configured keeps working during the rename; the new URL is the one to paste from now on
                "note": (
                    "اضبط مزود مكالمات الموبايل أن يرسل started / ended / recording_ready مع ترويسة "
                    "X-Call-Signature. المسار القديم /webhooks/telephony ما زال يعمل كمرادف."
                ),
            },
            {
                "provider": "email",
                "label": "البريد الوارد (Email inbound)",
                "url": f"{base}{prefix}/webhooks/email",
                "note": (
                    "مزوّد بريد وارد (webhook) يرسل JSON موقّعًا بـ X-Email-Signature: "
                    "sha256=HMAC(inbound_secret, body) — العقد في docs/runbook.md."
                ),
            },
        ],
    }


# ---------------------------------------------------------------------------------------------
# writes
# ---------------------------------------------------------------------------------------------


async def update(
    session: AsyncSession,
    actor: User,
    provider: str,
    *,
    config: dict[str, Any] | None = None,
    secrets: dict[str, Any] | None = None,
    is_live: bool | None = None,
    confirm_live: bool = False,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Merge a partial form into the row, switch live only on a green test, audit the masked diff."""
    _assert_provider(provider)
    row = await _row(session, provider)
    created = row is None
    if row is None:
        row = IntegrationConfig(provider=provider, config=defaults(provider), is_live=False)
        session.add(row)
        await session.flush()

    before_config = effective_config(provider, row)
    before_secrets = {key: bool(value) for key, value in secrets_for(provider, row).items()}
    before_is_live = bool(row.is_live)

    if config is not None:
        unknown = sorted(set(config) - set(defaults(provider)))
        if unknown:
            raise ValidationFailed(
                "Some fields need attention",
                details=[{"field": name, "message": "حقل غير معروف لهذا المزوّد", "type": "extra"} for name in unknown],
            )
        row.config = validate_config(provider, {**before_config, **config})

    after_secret_names = set()
    if secrets is not None:
        unknown_secret = sorted(set(secrets) - set(PROVIDER_SECRETS[provider]))
        if unknown_secret:
            raise ValidationFailed(
                "Some fields need attention",
                details=[{"field": name, "message": "حقل سري غير معروف لهذا المزوّد", "type": "extra"} for name in unknown_secret],
            )
        current = decrypt_map(row.secrets_encrypted)
        for key, value in secrets.items():
            if value is None:
                current.pop(key, None)
            else:
                text = str(value).strip()
                if text and text != "••••••••":
                    current[key] = text
            after_secret_names.add(key)
        row.secrets_encrypted = encrypt_map(current)

    if is_live is not None and is_live != row.is_live:
        _assert_switch(row, provider, to_live=is_live, confirm=confirm_live)
        row.is_live = is_live

    row.updated_by = actor.id
    await session.flush()
    after_config = effective_config(provider, row)
    after_secrets = {key: bool(value) for key, value in secrets_for(provider, row).items()}
    await audit.write(
        session,
        actor=actor,
        action="integration.update",
        entity_type="integration_config",
        entity_id=row.id,
        before={
            "config": _mask_dict(provider, before_config),
            "secrets": before_secrets,
            "is_live": before_is_live,
            "created": created,
        },
        after={
            "config": _mask_dict(provider, after_config),
            "secrets": after_secrets,
            "is_live": row.is_live,
            "changed": {
                "config": sorted(k for k in after_config if before_config.get(k) != after_config[k]),
                "secrets": sorted(after_secret_names),
                "is_live": before_is_live != row.is_live,
            },
        },
        ip=ip,
        user_agent=user_agent,
    )
    await session.commit()
    await _reprime(session)
    return (await state(session, provider)).as_dict()


def _mask_dict(provider: str, values: dict[str, Any]) -> dict[str, Any]:
    """Audit payloads must not carry secrets either — a config field could hold a token pasted by mistake."""
    out = dict(values)
    for key in PROVIDER_SECRETS[provider]:
        if key in out:
            out[key] = "••••••••" if out[key] else ""
    return out


#: Arabic labels for the fields that can block a switch. The screen has its own translations, but the API
#: message is read by people too (a curl, a log, a support ticket), and "missing: phone_number_id" helps
#: nobody at 2 a.m.
FIELD_LABELS: dict[str, str] = {
    "phone_number_id": "معرّف رقم واتساب (Phone Number ID)",
    "business_account_id": "معرّف حساب واتساب للأعمال (WABA ID)",
    "access_token": "توكين الوصول (Access Token)",
    "app_secret": "التوقيع السري لتطبيق ميتا (App Secret)",
    "verify_token": "توكين التحقق من الـwebhook (Verify Token)",
    "provider_url": "رابط مزود مكالمات الموبايل",
    "number": "رقم الموبايل",
    "trunk": "خط مكالمات الموبايل",
    "webhook_secret": "سر توقيع أحداث مكالمات الموبايل (Webhook Secret)",
    "api_key": "مفتاح API لمزود مكالمات الموبايل",
    "host": "مضيف SMTP",
    "port": "منفذ SMTP",
    "from_email": "بريد المرسل",
    "password": "كلمة مرور SMTP",
}


def label(key: str) -> str:
    return FIELD_LABELS.get(key, key)


def switch_blockers(row: IntegrationConfig | None, provider: str, *, confirm: bool = True) -> list[dict[str, Any]]:
    """Everything that stands between this provider and live mode, in the order `_assert_switch` checks it.

    One function, three consumers: the 409 (`_assert_switch`), the preflight endpoint and — through the
    endpoint — the screen that disables its own button. The screen used to guess (it knew only two of the
    three providers' required fields), so it offered a button the server was always going to refuse.
    """
    blockers: list[dict[str, Any]] = []
    if not confirm:
        blockers.append(
            {
                "code": "confirm",
                "message": "التبديل إلى الوضع الحيّ يحتاج تأكيدًا صريحًا (confirm=true)",
                "fields": [],
            }
        )
    if row is None or row.last_test_status != "ok":
        blockers.append(
            {
                "code": "test",
                "message": "لم ينجح أي اختبار اتصال لهذا المزوّد بعد — شغّل «اختبار الاتصال» أولًا",
                "fields": [],
            }
        )
    config = effective_config(provider, row)
    secrets = secrets_for(provider, row)
    missing = [spec.key for spec in PROVIDER_FIELDS[provider] if spec.required_for_live and not str(config.get(spec.key, "")).strip()]
    for name in LIVE_REQUIRED_SECRETS[provider]:
        if not secrets.get(name):
            missing.append(name)
    if missing:
        blockers.append(
            {
                "code": "fields",
                "message": "إعدادات ناقصة للوضع الحيّ: " + "، ".join(label(key) for key in missing),
                "fields": missing,
            }
        )
    return blockers


def _assert_switch(row: IntegrationConfig, provider: str, *, to_live: bool, confirm: bool) -> None:
    if not to_live:
        # In dev/test, back to simulation is safe and always allowed. In production there is no simulated
        # driver to return to (R9 §1.A), so "turn the channel off" is refused rather than pretended.
        if get_settings().is_production:
            raise Conflict(
                "ENV=prod لا يوجد فيه وضع محاكاة: القنوات تعمل حيًّا فقط. "
                "أوقف الاعتماديات من إعدادات المزوّد بدلًا من تحويل القناة إلى المحاكاة.",
                details={"provider": provider, "blockers": [], "missing": [], "labels": []},
            )
        return
    blockers = switch_blockers(row, provider, confirm=confirm)
    if not blockers:
        return
    first = blockers[0]
    # `required` is the pre-R6 contract (`["confirm_live"]` / `["test"]`) and is kept as the short answer;
    # `blockers`/`missing`/`labels` are the precise one the screen renders.
    required = ["confirm_live"] if first["code"] == "confirm" else ["test"] if first["code"] == "test" else []
    details: dict[str, Any] = {
        "provider": provider,
        "required": required,
        "blockers": blockers,
        "missing": [key for blocker in blockers if blocker["code"] == "fields" for key in blocker["fields"]],
        "labels": [label(key) for blocker in blockers if blocker["code"] == "fields" for key in blocker["fields"]],
        "hint": "شغّل اختبار الاتصال، واملأ الحقول الناقصة، ثم أكّد التبديل — لا شيء من هذا يحدث تلقائيًا",
    }
    if first["code"] == "fields":
        raise IncompleteConfig(first["message"], details=details)
    raise Conflict(first["message"], details=details)


async def preflight(session: AsyncSession, provider: str) -> dict[str, Any]:
    """What the screen needs to decide whether to offer «التحويل إلى الوضع الحيّ» at all."""
    _assert_provider(provider)
    row = await _row(session, provider)
    blockers = switch_blockers(row, provider, confirm=True)  # the confirmation comes from the dialog
    current = await state(session, provider)
    return {
        "provider": provider,
        "ready": not blockers,
        "blockers": blockers,
        "missing": [key for blocker in blockers if blocker["code"] == "fields" for key in blocker["fields"]],
        "labels": [label(key) for blocker in blockers if blocker["code"] == "fields" for key in blocker["fields"]],
        "is_live": current.is_live,
        "mode": "live" if current.is_live else "ready",
        #: false in a production process: the screen hides «الرجوع إلى المحاكاة», because there is nothing
        #: to go back to (R9 §1.D — no simulation affordance in the production UI).
        "sim_allowed": get_settings().simulation_allowed,
        "status": current.status,
        "test_status": row.last_test_status if row else None,
        "tested_at": current.last_tested_at,
        "next": "جاهز للتحويل" if not blockers else blockers[0]["message"],
    }


async def set_live(
    session: AsyncSession,
    actor: User,
    provider: str,
    *,
    is_live: bool,
    confirm: bool = False,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    _assert_provider(provider)
    row = await _row(session, provider)
    if row is None:
        row = IntegrationConfig(provider=provider, config=defaults(provider), is_live=False)
        session.add(row)
        await session.flush()
    before = bool(row.is_live)
    _assert_switch(row, provider, to_live=is_live, confirm=confirm)
    row.is_live = is_live
    row.updated_by = actor.id
    await audit.write(
        session,
        actor=actor,
        action="integration.live",
        entity_type="integration_config",
        entity_id=row.id,
        before={"is_live": before},
        after={"is_live": is_live, "confirm": confirm},
        ip=ip,
        user_agent=user_agent,
    )
    await session.commit()
    await _reprime(session)
    return (await state(session, provider)).as_dict()


# ---------------------------------------------------------------------------------------------
# resolution for drivers, connection tests, template sync, event log
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedIntegration:
    """Decrypted, merged provider config — the only type that carries a plaintext secret onward."""

    provider: str
    is_live: bool
    config: dict[str, Any]
    secrets: dict[str, str]

    def secret(self, name: str) -> str:
        return self.secrets.get(name) or str(_env_value(self.provider, name) or "")

    def value(self, name: str, fallback: str = "") -> str:
        raw = self.config.get(name)
        if raw in (None, ""):
            raw = _env_value(self.provider, name)
        return str(raw) if raw not in (None, "") else fallback

    def number(self, name: str, fallback: int) -> int:
        try:
            return int(self.config.get(name) or fallback)
        except (TypeError, ValueError):
            return fallback


async def resolved_for_registry(session: AsyncSession) -> dict[str, ResolvedIntegration]:
    """Called by `integrations/registry.py` at startup and after every settings write. Not a route."""
    settings = get_settings()
    out: dict[str, ResolvedIntegration] = {}
    for provider in PROVIDERS:
        row = await _row(session, provider)
        # R9 §1.A: `ENV=prod` has one mode. The row cannot switch a channel off, because "off" would have to
        # mean a simulator that does not exist in production.
        is_live = True if settings.is_production else (bool(row.is_live) if row is not None else settings.INTEGRATIONS_DRIVER == "live")
        out[provider] = ResolvedIntegration(
            provider=provider,
            is_live=is_live,
            config=effective_config(provider, row),
            secrets=secrets_for(provider, row),
        )
    return out


@dataclass(frozen=True)
class TestOutcome:
    ok: bool
    detail: str


# `run_connection_test`, not `test_connection`: a module-level name starting with `test_` is read as a pytest
# case by the linters (PT028 fired on the first draft), and a function that takes a request's body is not one.
async def run_connection_test(
    session: AsyncSession,
    actor: User,
    provider: str,
    *,
    to: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """A real check when live, an explicitly-labelled simulation when not — and always recorded."""
    _assert_provider(provider)
    test_limiter.check(f"integration-test|{actor.id}|{provider}")
    resolved = (await resolved_for_registry(session))[provider]
    started = time.monotonic()
    if not resolved.is_live:
        outcome = TestOutcome(True, "وضع المحاكاة: لم يخرج أي نداء إلى الشبكة. الإعدادات مقبولة والصيغة صحيحة.")
    elif provider == "whatsapp":
        outcome = await _test_whatsapp(resolved)
    elif provider == "telephony":
        outcome = await _test_mobile_calls(resolved)
    else:
        outcome = await _test_email(resolved, to=to or actor.email, probe_only=to is None)
    took_ms = int((time.monotonic() - started) * 1000)

    row = await _row(session, provider)
    if row is None:
        row = IntegrationConfig(provider=provider, config=defaults(provider), is_live=False)
        session.add(row)
        await session.flush()
    row.last_tested_at = datetime.now(UTC)
    row.last_test_status = "ok" if outcome.ok else "failed"
    row.last_test_detail = outcome.detail[:900]
    row.updated_by = actor.id
    await audit.write(
        session,
        actor=actor,
        action="integration.test",
        entity_type="integration_config",
        entity_id=row.id,
        after={
            "provider": provider,
            "ok": outcome.ok,
            "mode": "live" if resolved.is_live else "simulated",
            "ms": took_ms,
            "detail": outcome.detail[:300],
        },
        ip=ip,
        user_agent=user_agent,
    )
    await session.commit()
    await _reprime(session)
    return {
        "provider": provider,
        "ok": outcome.ok,
        "detail": outcome.detail,
        "took_ms": took_ms,
        #: a test in a dev/test process never reaches the network — the word says so instead of "sim",
        #: which R9 removed from the provider state vocabulary
        "mode": "live" if resolved.is_live else "simulated",
        "tested_at": row.last_tested_at.isoformat(),
    }


async def sync_templates(session: AsyncSession, actor: User, *, ip: str | None = None, user_agent: str | None = None) -> dict[str, Any]:
    _assert_provider("whatsapp")
    resolved = (await resolved_for_registry(session))["whatsapp"]
    if not resolved.is_live:
        templates = [dict(item) for item in SIM_TEMPLATES]
        detail = "وضع المحاكاة: قوالب محاكاة محلية؛ المزامنة الحقيقية تقرأ من نشاط Business Account في الوضع الحيّ."
    else:
        account = resolved.value("business_account_id")
        token = resolved.secret("access_token")
        if not account or not token:
            raise IncompleteConfig(
                "تحتاج المزامنة Business Account ID وAccess Token في الوضع الحيّ",
                details={"missing": [key for key, value in {"business_account_id": account, "access_token": token}.items() if not value]},
            )
        templates, detail = await _fetch_meta_templates(account, token)

    row = await _row(session, "whatsapp")
    await audit.write(
        session,
        actor=actor,
        action="integration.templates_sync",
        entity_type="integration_config",
        entity_id=row.id if row else None,
        after={"count": len(templates), "mode": "live" if resolved.is_live else "sim", "detail": detail[:300]},
        ip=ip,
        user_agent=user_agent,
    )
    await session.commit()
    return {"provider": "whatsapp", "templates": templates, "detail": detail, "synced_at": datetime.now(UTC).isoformat()}


async def recent_events(session: AsyncSession, provider: str | None = None, *, limit: int = 20) -> list[dict[str, Any]]:
    """The last few inbound events, newest first, within 24 hours — the screen answers "did it arrive?"."""
    since = datetime.now(UTC) - timedelta(hours=24)
    stmt = (
        select(WebhookEvent)
        .where(WebhookEvent.occurred_at >= since)
        .order_by(WebhookEvent.occurred_at.desc())
        .limit(min(max(limit, 1), 50))
    )
    if provider:
        stmt = stmt.where(WebhookEvent.provider == provider)
    return [
        {
            "id": str(row.id),
            "provider": row.provider,
            "event_type": row.event_type,
            "external_id": row.external_id,
            "status": row.status,
            "detail": (row.detail or "")[:300] or None,
            "occurred_at": row.occurred_at.astimezone(UTC).isoformat() if row.occurred_at else None,
        }
        for row in list((await session.execute(stmt)).scalars().all())
    ]


async def record_event(
    session: AsyncSession,
    *,
    provider: str,
    event_type: str,
    external_id: str | None = None,
    status: str = "ok",
    detail: str | None = None,
) -> None:
    """Called from the webhook handlers. The caller commits: an event log must never decide a request's fate.

    `status` is clamped to the column's width on purpose. The value is not always a literal of ours — the
    telephony handler passes the intake's own result — and an over-long string used to abort the insert on
    PostgreSQL, turning a *logged* webhook into a 500 after the work had already been committed (ADR-024).
    """
    session.add(
        WebhookEvent(
            provider=provider,
            event_type=event_type,
            external_id=external_id,
            status=status[:32],
            detail=(detail or "")[:900] or None,
        )
    )


async def _reprime(session: AsyncSession) -> None:
    from app.integrations import registry

    await registry.prime(session)


def _assert_provider(provider: str) -> None:
    if provider not in PROVIDERS:
        raise ValidationFailed(
            "مزوّد غير معروف",
            details=[{"field": "provider", "message": " / ".join(PROVIDERS), "type": "invalid"}],
        )


# ---------------------------------------------------------------------------------------------
# the three probes
# ---------------------------------------------------------------------------------------------


async def _test_whatsapp(resolved: ResolvedIntegration) -> TestOutcome:
    """Meta's own token introspection: proves the credential is alive without messaging a customer."""
    import httpx

    token = resolved.secret("access_token")
    phone_id = resolved.value("phone_number_id")
    if not token or not phone_id:
        return TestOutcome(False, "ناقص: access_token وphone_number_id مطلوبان في الوضع الحيّ")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get("https://graph.facebook.com/v21.0/debug_token", params={"input_token": token})
    except httpx.HTTPError as exc:
        return TestOutcome(False, f"تعذّر الوصول إلى graph.facebook.com: {type(exc).__name__}")
    if response.status_code >= 400:
        return TestOutcome(False, f"Meta رفض الفحص ({response.status_code}): {response.text[:220]}")
    data = (response.json() or {}).get("data") or {}
    if not data.get("is_valid"):
        return TestOutcome(False, "الـToken غير صالح — منتهي أو ملغى من App dashboard")
    if data.get("phone_number_id") and str(data["phone_number_id"]) != str(phone_id):
        return TestOutcome(False, f"الـToken يخصّ رقمًا آخر: {data['phone_number_id']} ≠ {phone_id}")
    app_name = data.get("application") or {}
    label = app_name.get("name") if isinstance(app_name, dict) else str(app_name)
    return TestOutcome(True, f"الاتصال سليم — Number ID {phone_id}" + (f"، التطبيق {label}" if label else ""))


async def _test_mobile_calls(resolved: ResolvedIntegration) -> TestOutcome:
    """Two halves, both real: the HMAC contract we verify with, and a reachability probe of the provider.

    The signature self-test alone answered "green" for a URL that nobody could reach — which is the failure
    an operator most needs to see before going live (ADR-023 §3). The copies never say «سنترال»: the channel
    is a mobile number served by a mobile-calls provider (R9 §1.C).
    """
    from app.core.security import verify_webhook_signature
    from app.integrations.mobile_calls import MobileCallsProvider

    secret = resolved.secret("webhook_secret")
    if not secret:
        return TestOutcome(False, "ناقص: webhook_secret غير مضبوط، فكل مكالمة موبايل واردة ستُرفض بالتوقيع")
    sample = b'{"event":"call.started","from":"+201000000000"}'
    signature = "sha256=" + hmac.new(secret.encode(), sample, hashlib.sha256).hexdigest()
    if not verify_webhook_signature(sample, signature, secret):
        return TestOutcome(False, "تحقّق التوقيع فشل محليًا — تهيئة الخادم غير متسقة")
    driver = MobileCallsProvider(
        webhook_secret=secret,
        provider_url=str(resolved.value("provider_url") or ""),
        provider_name=str(resolved.config.get("provider") or "twilio"),
    )
    ok, detail = await driver.probe()
    if not ok:
        return TestOutcome(False, f"التوقيع سليم، لكن مزود مكالمات الموبايل غير قابل للوصول: {detail}")
    return TestOutcome(True, f"التوقيع يعمل (sha256) و{detail}. ضع عنوان الاستدعاء أسفل البطاقة في لوحة المزود.")


async def _test_email(resolved: ResolvedIntegration, *, to: str, probe_only: bool) -> TestOutcome:
    import anyio

    host = resolved.value("host")
    if not host:
        return TestOutcome(False, "ناقص: SMTP host غير مضبوط (لا في الإعدادات ولا في البيئة)")
    port = resolved.number("port", 1025)
    encryption = str(resolved.config.get("encryption") or "none")
    username = resolved.value("username")
    password = resolved.secret("password")
    from_email = resolved.value("from_email")
    args = (host, port, encryption, username, password, from_email, resolved.value("from_name"), resolved.value("reply_to"), to, probe_only)
    return await anyio.to_thread.run_sync(functools.partial(_smtp_probe, *args))


def _smtp_probe(
    host: str,
    port: int,
    encryption: str,
    username: str,
    password: str,
    from_email: str,
    from_name: str,
    reply_to: str,
    to: str,
    probe_only: bool,
) -> TestOutcome:
    from email.message import EmailMessage

    context = ssl.create_default_context()
    try:
        if encryption == "ssl":
            client: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=12, context=context)
        else:
            client = smtplib.SMTP(host, port, timeout=12)
        with client:
            client.ehlo()
            if encryption == "starttls":
                client.starttls(context=context)
                client.ehlo()
            if username:
                client.login(username, password)
            if probe_only:
                return TestOutcome(True, f"المصافحة نجحت مع {host}:{port} ({encryption}) ولم تُرسل رسالة.")
            message = EmailMessage()
            message["From"] = f"{from_name} <{from_email}>" if from_name else from_email
            message["To"] = to
            message["Subject"] = "Broker Hub — اختبار إعدادات البريد"
            if reply_to:
                message["Reply-To"] = reply_to
            message.set_content("رسالة اختبار من شاشة الإعدادات.\n")
            client.send_message(message)
            return TestOutcome(True, f"أُرسلت رسالة اختبار إلى {to} عبر {host}:{port}.")
    except (smtplib.SMTPException, OSError) as exc:
        return TestOutcome(False, f"{type(exc).__name__}: {str(exc)[:220]}")


async def _fetch_meta_templates(account: str, token: str) -> tuple[list[dict[str, Any]], str]:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"https://graph.facebook.com/v21.0/{account}/message_templates",
                params={"fields": "name,language,status,category", "limit": 100},
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        return [], f"تعذّر الوصول إلى Meta: {type(exc).__name__}"
    if response.status_code >= 400:
        return [], f"Meta رفض الطلب ({response.status_code}): {response.text[:220]}"
    payload = response.json() or {}
    templates = [
        {
            "name": row.get("name") or "",
            "language": row.get("language") or "",
            "status": str(row.get("status") or "UNKNOWN").upper(),
            "category": row.get("category") or "",
        }
        for row in payload.get("data") or []
    ]
    return templates, f"جُمِّعت {len(templates)} قالبًا من Meta."
