"""Driver selection: **live in production, no silent fallback** (R9 §1.A, §2.1).

R8 and earlier treated the simulated driver as "the default, replaced by live when configured", and the
registry fell back to it whenever a live provider was incomplete. That is how a production process could
serve a green settings screen while every WhatsApp reply and every call went into a list in memory. R9
inverts the rule:

* `ENV=prod` ⇒ only live drivers. The settings row cannot turn a provider "off", `INTEGRATIONS_DRIVER` is
  forced to `live` (`core/config.py`), and `assert_production_ready()` refuses to boot if a live provider is
  missing the secrets it needs — an unusable production process is a better outcome than a lying one.
* A live provider with missing credentials raises `IntegrationNotConfigured` (`503`) from the factory
  instead of returning a simulator. The Arabic message names what is missing.
* Simulated drivers are constructible only when `ENV=dev|test`; `integrations/sims.py` enforces that in the
  constructor, so even a mis-wired call site cannot build one in production.

`prime(session)` loads `integration_configs` into process memory at startup and after every settings write.
It is a cache and not a second source of truth: the row is the source, and the two ways it can disagree with
a driver — a second API worker that never saw the write, a worker that started before the row existed — are
both fixed by the same line in `docs/runbook.md` (re-save, or restart the worker).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.core.config import get_settings
from app.core.errors import IntegrationNotConfigured
from app.core.logging import get_logger
from app.integrations.ai_sim import AISim
from app.integrations.mobile_calls import MobileCallsProvider
from app.integrations.notifier import ConsoleNotifier, SmtpNotifier
from app.integrations.sims import MobileCallsSim, WhatsAppSim
from app.integrations.whatsapp import WhatsAppMeta
from app.models.integrations import PROVIDERS
from app.services import integrations_settings as cfg

log = get_logger("integrations.registry")

_whatsapp: Any | None = None
_mobile_calls: Any | None = None
_ai: Any | None = None
_email: Any | None = None
_primed: dict[str, cfg.ResolvedIntegration] | None = None
_env_fallback: dict[str, cfg.ResolvedIntegration] | None = None


async def prime(session: Any) -> None:
    """Load the saved rows into memory and rebuild the drivers. Never raises: an unavailable settings
    table (first boot, before `alembic upgrade`) must not take the API down with it — readiness is asserted
    separately by `assert_production_ready()`, which *does* fail the boot."""
    global _primed
    try:
        _primed = await cfg.resolved_for_registry(session)
    except Exception as exc:  # pragma: no cover - depends on schema state, not on our logic
        log.warning("integrations_prime_skipped", error=f"{type(exc).__name__}: {str(exc)[:180]}")
        _primed = None
    reset_providers()


def is_primed() -> bool:
    return _primed is not None


def _snapshot() -> dict[str, cfg.ResolvedIntegration]:
    global _env_fallback
    if _primed:
        return _primed
    if _env_fallback is None:
        _env_fallback = {provider: _from_env(provider) for provider in PROVIDERS}
    return _env_fallback


def _from_env(provider: str) -> cfg.ResolvedIntegration:
    settings = get_settings()
    # `ENV=prod` forces the driver to `live` in settings, so this expression cannot be false in production.
    is_live = settings.INTEGRATIONS_DRIVER == "live" or settings.is_production
    return cfg.ResolvedIntegration(
        provider=provider,
        is_live=is_live,
        config=cfg.effective_config(provider, None),
        secrets=cfg.secrets_for(provider, None),
    )


def resolved(provider: str) -> cfg.ResolvedIntegration:
    """Per-provider fallback: a snapshot that predates a new provider (or an empty primed cache because
    the settings table did not exist yet) must never raise inside a webhook handler.

    R9: in production the answer is *always* live. A row saved as "not live" (or a snapshot primed before
    the row existed) must not be able to select a simulated driver, so the mode of the process wins over the
    mode of the row — the one place that decision is made.
    """
    state = _snapshot().get(provider) or _from_env(provider)
    if get_settings().is_production and not state.is_live:
        return replace(state, is_live=True)
    return state


def config_value(provider: str, key: str, fallback: Any = None) -> Any:
    """A non-secret setting, for the request paths that honor one (media staging, retention)."""
    value = resolved(provider).config.get(key)
    return fallback if value in (None, "") else value


def live_mode(provider: str) -> bool:
    return resolved(provider).is_live


def _missing(*pairs: tuple[str, str]) -> list[str]:
    return [label for label, value in pairs if not str(value or "").strip()]


def whatsapp_provider() -> Any:
    """Meta Cloud API when live; a simulator only in dev/test; otherwise a hard, named failure."""
    global _whatsapp
    if _whatsapp is None:
        state = resolved("whatsapp")
        token = state.secret("access_token")
        phone_number_id = state.value("phone_number_id")
        if not state.is_live:
            _whatsapp = WhatsAppSim()  # raises SimulationDisabled outside dev/test
            return _whatsapp
        missing = _missing(
            ("WHATSAPP_ACCESS_TOKEN", token),
            ("WHATSAPP_PHONE_NUMBER_ID", phone_number_id),
            # without the App Secret every delivery is refused, so "live" would mean "silently deaf" (R9 §1.B)
            ("WHATSAPP_APP_SECRET", state.secret("app_secret")),
        )
        if missing:
            # No fallback: a live row that cannot send must say so, not record into a list (R9 §1.A).
            raise IntegrationNotConfigured(
                "واتساب غير مكتمل الإعداد: " + "، ".join(missing) + ". أضِف القيمة من /settings أو من متغيّرات البيئة.",
                details={"provider": "whatsapp", "missing": missing},
            )
        _whatsapp = WhatsAppMeta(
            token=token,
            phone_number_id=phone_number_id,
            app_secret=state.secret("app_secret"),
        )
    return _whatsapp


def mobile_calls_provider() -> Any:
    """The mobile-calls driver (R9 §1.C): live webhook verification + recording fetch.

    `webhook_secret` is the credential that makes an inbound call trustworthy — without it every delivery is
    a rejected 403, so a live row without one is refused here by name. `provider_url` is only needed to lock
    the recording fetch to the provider's host; a missing one disables recording rather than guessing.
    """
    global _mobile_calls
    if _mobile_calls is None:
        state = resolved("telephony")
        if not state.is_live:
            _mobile_calls = MobileCallsSim()  # raises SimulationDisabled outside dev/test
            return _mobile_calls
        secret = state.secret("webhook_secret")
        if not secret:
            raise IntegrationNotConfigured(
                "مكالمات الموبايل غير مكتملة الإعداد: TELEPHONY_WEBHOOK_SECRET غير مضبوط، فكل مكالمة واردة ستُرفض بالتوقيع.",
                details={"provider": "telephony", "missing": ["webhook_secret"]},
            )
        if not state.value("provider_url"):
            log.warning("mobile_calls_no_provider_url", note="recording downloads are refused until provider_url is set")
        _mobile_calls = MobileCallsProvider(
            webhook_secret=secret,
            provider_url=str(state.value("provider_url") or ""),
            provider_name=str(state.value("provider") or "twilio"),
        )
    return _mobile_calls


def email_notifier() -> Any:
    """`SmtpNotifier` built from the saved row when live, the console when not.

    Email is out of R9's channel scope (WhatsApp + mobile calls), and the console notifier is *not* a
    simulator of a customer-facing channel: it is an empty sink in a developer's machine.
    """
    global _email
    if _email is None:
        state = resolved("email")
        if not state.is_live or not state.value("host"):
            _email = ConsoleNotifier()
        else:
            _email = SmtpNotifier(
                host=state.value("host"),
                port=state.number("port", 1025),
                username=state.value("username"),
                password=state.secret("password"),
                encryption=str(state.config.get("encryption") or "none"),
                from_addr=state.value("from_email") or get_settings().EMAIL_FROM,
                from_name=state.value("from_name"),
                reply_to=state.value("reply_to"),
            )
    return _email


def ai_provider() -> Any:
    """Simulated by default (spec §8: every provider has a simulated driver).

    There is deliberately no `live` AI driver yet: no vendor is approved for the data this touches
    (recordings and client threads), so choosing a provider is a decision, not a ticket. AI is outside the
    R9 channel scope; `docs/STATUS.md` says so.
    """

    global _ai
    if _ai is None:
        _ai = AISim()
    return _ai


def readiness() -> dict[str, Any]:
    """What production still needs, as machine keys + Arabic sentences. Also the preflight of the whole app."""
    state: dict[str, Any] = {"production": get_settings().is_production, "ready": True, "blockers": []}
    for provider, factory in (("whatsapp", whatsapp_provider), ("mobile_calls", mobile_calls_provider)):
        try:
            driver = factory()
        except IntegrationNotConfigured as exc:
            state["blockers"].append({"provider": provider, "message": exc.message, "missing": exc.details or {}})
            continue
        except Exception as exc:  # pragma: no cover - defensive: a driver constructor is not a validator
            state["blockers"].append({"provider": provider, "message": f"{type(exc).__name__}: {str(exc)[:160]}", "missing": {}})
            continue
        # a dev/test process is allowed to be simulated; a production one never is
        if get_settings().is_production and getattr(driver, "name", "") == "sim":
            state["blockers"].append({"provider": provider, "message": "محاكاة في عملية إنتاج — مرفوض", "missing": {}})
    state["ready"] = not state["blockers"]
    return state


def assert_production_ready() -> None:
    """Fail the boot, loudly and in Arabic, when production is missing a live credential (R9 §2.1).

    Called from the app lifespan. In dev/test it is a no-op: the simulator is the honest default there.
    """
    settings = get_settings()
    if not settings.is_production:
        return
    report = readiness()
    if report["blockers"]:
        lines = [f"- {blocker['provider']}: {blocker['message']}" for blocker in report["blockers"]]
        raise RuntimeError(
            "ENV=prod requires live integration credentials; the process refuses to start instead of "
            "running on simulation:\n" + "\n".join(lines)
        )


def reset_providers() -> None:
    """Drop the built drivers *and* the env fallback, so the next call re-reads the snapshot."""
    global _whatsapp, _mobile_calls, _email, _env_fallback
    _whatsapp = _mobile_calls = _email = None
    _env_fallback = None
