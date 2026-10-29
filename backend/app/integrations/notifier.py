"""Email via SMTP/Mailpit + in-app notifications (spec §8).

The credentials come from the settings row when one is saved and live, otherwise from the environment — so
`notifier` has no idea which, and neither do its callers. `SmtpNotifier` takes explicit values for exactly
that reason: the registry builds it, this module only knows how to talk SMTP.
"""

from __future__ import annotations

import functools
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("notifier")


class SmtpNotifier:
    """One provider call per email, no retries here: the celery task owns retry policy (spec §8)."""

    def __init__(
        self,
        *,
        host: str = "",
        port: int = 0,
        username: str = "",
        password: str = "",
        encryption: str = "",
        from_addr: str = "",
        from_name: str = "",
        reply_to: str = "",
    ) -> None:
        settings = get_settings()
        env_host, env_port = settings.smtp_endpoint
        self.host = host or env_host
        self.port = int(port or env_port)
        self.username = username or settings.SMTP_USERNAME
        self.password = password or settings.SMTP_PASSWORD
        self.encryption = (encryption or settings.SMTP_ENCRYPTION).lower()
        self.from_addr = from_addr or settings.EMAIL_FROM
        self.from_name = from_name or settings.SMTP_FROM_NAME
        self.reply_to = reply_to or settings.SMTP_REPLY_TO

    def _send_sync(self, message: EmailMessage) -> None:
        context = ssl.create_default_context()
        client: Any = (
            smtplib.SMTP_SSL(self.host, self.port, timeout=10, context=context)
            if self.encryption == "ssl"
            else smtplib.SMTP(self.host, self.port, timeout=10)
        )
        with client as smtp:
            smtp.ehlo()
            if self.encryption == "starttls":
                smtp.starttls(context=context)
                smtp.ehlo()
            if self.username:
                smtp.login(self.username, self.password)
            smtp.send_message(message)

    async def send_email(self, *, to: str, subject: str, body: str) -> bool:
        message = EmailMessage()
        message["From"] = f"{self.from_name} <{self.from_addr}>" if self.from_name else self.from_addr
        message["To"] = to
        message["Subject"] = subject
        if self.reply_to:
            message["Reply-To"] = self.reply_to
        message.set_content(body)
        try:
            import anyio

            await anyio.to_thread.run_sync(functools.partial(self._send_sync, message))
            return True
        except Exception as exc:  # pragma: no cover - no SMTP server in unit tests
            log.warning("email_failed", to=to, subject=subject, error=str(exc)[:200])
            return False


class ConsoleNotifier:
    """Records instead of sending — the sim driver of the email channel (spec §8)."""

    sent: list[dict[str, Any]] = []

    async def send_email(self, *, to: str, subject: str, body: str) -> bool:
        log.info("email_console", to=to, subject=subject, chars=len(body))
        ConsoleNotifier.sent.append({"to": to, "subject": subject, "chars": len(body)})
        return True

    @classmethod
    def reset(cls) -> None:
        cls.sent = []


def email_notifier() -> ConsoleNotifier | SmtpNotifier:
    """Delegates to the registry so the saved row decides (ADR-016 §1)."""
    from app.integrations.registry import email_notifier as _from_registry

    return _from_registry()  # type: ignore[no-any-return]
