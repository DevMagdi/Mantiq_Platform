"""Application settings. All values come from env; nothing secret is committed."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

#: `ENV` values that mean "this process serves real customers". `production` and `prod` are the same mode;
#: `prod` is the canonical spelling (it is what `.env.example`, `infra/` and `core/secrets.py` already say).
PRODUCTION_ENVS: frozenset[str] = frozenset({"prod", "production"})
#: `ENV` values that may construct a simulated driver (R9: simulation never exists in production).
SIMULATION_ENVS: frozenset[str] = frozenset({"dev", "test"})

#: The JWT value this repository publishes. It is 34 chars long, so a length check alone would let it
#: through — production compares against it by name.
DEV_JWT_SECRET: str = "dev-only-insecure-secret-change-me"

#: Development placeholders that must never sign or verify anything in production. A known secret is worse
#: than a missing one: `hmac.new(b"dev-app-secret", …)` is computable by anybody who has read this repo.
DEV_PLACEHOLDER_SECRETS: dict[str, str] = {
    "WHATSAPP_APP_SECRET": "dev-app-secret",
    "WHATSAPP_VERIFY_TOKEN": "dev-verify-token",
    "TELEPHONY_WEBHOOK_SECRET": "dev-telephony-secret",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "Mantiq"  # R13 brand (ADR-034) — user-visible product name
    ENV: str = "dev"
    DEBUG: bool = True
    API_V1_PREFIX: str = "/api/v1"

    # --- database ---
    DATABASE_URL: str = "postgresql+asyncpg://broker:broker@localhost:5432/broker_hub"
    DATABASE_URL_TEST: str = "sqlite+aiosqlite:///./dev_test.db"
    DB_ECHO: bool = False

    # --- auth ---
    JWT_SECRET: str = DEV_JWT_SECRET
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_TTL_MIN: int = 15
    REFRESH_TOKEN_TTL_DAYS: int = 14
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "lax"
    LOGIN_MAX_ATTEMPTS: int = 5
    LOGIN_LOCKOUT_SECONDS: int = 300

    # --- cors / security headers ---
    # `NoDecode`: without it pydantic-settings runs `json.loads` on the raw env value *before* any validator,
    # so the comma-separated form this repo documents (`infra/.env.example`, compose) crashes the import.
    CORS_ORIGINS: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:5173"])
    TRUST_HOPS: int = 1

    # --- storage (recordings & attachments) ---
    STORAGE_BACKEND: str = "local"  # local | s3
    STORAGE_DIR: Path = Path("/tmp/broker-hub-objects")
    S3_ENDPOINT_URL: str = "http://localhost:9000"
    S3_BUCKET: str = "broker-hub"
    S3_ACCESS_KEY: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    RECORDING_SIGNED_URL_TTL_SECONDS: int = 120
    RECORDING_RETENTION_DAYS: int = 180

    # --- secrets at rest (ADR-016) ---
    # A Fernet key (`python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"`)
    # or any passphrase >=16 chars. Unset in development ⇒ derived from JWT_SECRET; required in prod.
    SECRETS_ENCRYPTION_KEY: str = ""
    # Set only while rotating the key above: blobs written under the retired key stay readable, every new
    # write uses the current one. Clear it once the values have been re-saved — the window is not permanent.
    SECRETS_ENCRYPTION_KEY_OLD: str = ""

    # --- integrations ---
    # `INTEGRATIONS_DRIVER` is the *fallback*: a saved row in `integration_configs` wins over it (ADR-016),
    # so the settings screen — not a file on the server — is the normal way to go live.
    # R9: `ENV=prod` forces `live`; an explicit `sim` in production is refused at boot (see `_production_rules`).
    INTEGRATIONS_DRIVER: str = "sim"  # sim | live (dev/test only for sim)
    # WhatsApp Business Cloud API
    WHATSAPP_ACCESS_TOKEN: str = ""
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_APP_SECRET: str = "dev-app-secret"
    WHATSAPP_VERIFY_TOKEN: str = "dev-verify-token"
    WHATSAPP_BUSINESS_ACCOUNT_ID: str = ""
    #: The Meta *App ID* (App dashboard → Settings → Basic). It is not a secret: it is the public half of the
    #: `app_id|app_secret` pair the `debug_token` endpoint demands. Without it the token introspection is
    #: refused with "(#100) You must provide an app access token…" even when the token being tested is valid.
    WHATSAPP_APP_ID: str = ""
    #: One Graph version for the whole backend. Meta retires a version after ~two years (v21.0 on 2027-01-21),
    #: and a version pinned in three files is a version that gets bumped in two of them.
    WHATSAPP_GRAPH_VERSION: str = "v25.0"
    # Mobile phone calls provider (a VoIP/mobile-call gateway that signs its webhook with HMAC).
    # `twilio` is the documented live dialect (Programmable Voice); `generic` is the JSON contract.
    TELEPHONY_PROVIDER: str = "twilio"  # twilio | generic
    TELEPHONY_WEBHOOK_SECRET: str = "dev-telephony-secret"
    TELEPHONY_API_KEY: str = ""
    TELEPHONY_WEBHOOK_URL: str = ""
    AI_ENABLED: bool = False

    # --- redis / celery ---
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_TASK_ALWAYS_EAGER: bool = True

    # --- messaging ---
    # Prometheus scrape guard: /metrics is open in development, otherwise it needs this bearer token.
    METRICS_TOKEN: str = ""
    # `SMTP_URL` is what the mailer connects to. `SMTP_HOST`/`SMTP_PORT` are the override the settings
    # screen edits (and, when set, they win over the URL) — empty means "whatever the URL says", so the
    # screen never shows a host that is not in use.
    SMTP_URL: str = "smtp://localhost:1025"
    SMTP_HOST: str = ""
    SMTP_PORT: int = 0
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_ENCRYPTION: str = "none"  # none | starttls | ssl
    SMTP_FROM_NAME: str = "Mantiq"  # R13 brand: the e-mail from-name users see
    EMAIL_FROM: str = "no-reply@mantiq.local"
    SMTP_REPLY_TO: str = ""

    # --- SLA defaults (mirrored in DB table sla_policies; DB wins) ---
    WORKDAY_START: str = "09:00"
    WORKDAY_END: str = "18:00"
    # same reason as CORS_ORIGINS: `WEEKEND_DAYS=4,5` is what a human writes in `.env`
    WEEKEND_DAYS: Annotated[list[int], NoDecode] = Field(default_factory=lambda: [4, 5])  # Fri, Sat (Egypt)
    TIMEZONE: str = "Africa/Cairo"
    REPLY_THREAD_WINDOW_MINUTES: int = 240

    @field_validator("CORS_ORIGINS", "WEEKEND_DAYS", mode="before")
    @classmethod
    def _list_from_env(cls, v: object) -> object:
        """Accept the form humans actually write in `.env` / compose: `a,b` as well as `["a","b"]`.

        pydantic-settings decodes any *complex* field (a `list[...]`) with `json.loads`, and a plain
        comma-separated value raises `SettingsError: error parsing value for field "CORS_ORIGINS"` **while
        importing the app** — the container exits before it can answer /healthz, and the stack's boot test
        fails with a traceback that never mentions CORS. `infra/docker-compose.yml` has shipped exactly that
        form since the stack was written, so no `docker compose up` could ever have started the API (R3: the
        `docker` job reached this point once the image pull was fixed).
        """
        if isinstance(v, str):
            text = v.strip()
            if not text:
                return []
            if text.startswith("["):
                # `NoDecode` also switches off the JSON path, so the documented JSON form is parsed here
                try:
                    return json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"expected a comma-separated list or a JSON array, got {text!r}") from exc
            return [piece.strip() for piece in text.split(",") if piece.strip()]
        return v

    @field_validator("SMTP_ENCRYPTION")
    @classmethod
    def _smtp_encryption(cls, v: str) -> str:
        if v not in {"none", "starttls", "ssl"}:
            raise ValueError("SMTP_ENCRYPTION must be none | starttls | ssl")
        return v

    @model_validator(mode="after")
    def _production_rules(self) -> Settings:
        """What "ENV=prod" means, enforced at settings load.

        Every rule here is a boot error, not a log line: a process that is about to serve real
        customers must not start half-configured. The whole set is checked at once and reported as one
        list, so an operator fixes one file rather than discovering problems one restart at a time.

        1. `ENV=production` is the same mode as `prod` (rewritten to the canonical `prod`).
        2. `INTEGRATIONS_DRIVER`: an *explicitly* configured `sim` is refused (that is operator intent we
           must not quietly override); absent or `live` is forced to `live`. The old check compared
           against the `sim` *default*, so the message's own advice — "remove the variable" — still
           failed to boot.
        3. `JWT_SECRET` is long, and is not the value published in this repository.
        4. `DEBUG` is off, `COOKIE_SECURE` is on, and `CORS_ORIGINS` names real origins (no wildcard, no
           localhost) — a leaked refresh cookie or an any-origin API is a production incident.
        5. `SECRETS_ENCRYPTION_KEY` is present at boot, not at first use of the settings screen.
        6. `DATABASE_URL` is PostgreSQL (SQLite has no row-level security and one writer).
        7. Object storage is durable: `s3`, or a `local` directory that is not a temp dir.
        8. Celery is not `always_eager`: running tasks inline blocks the request that triggered them.
        9. Development placeholder webhook secrets (`dev-app-secret`, …) are blanked, so production can
           never verify a webhook with a key that is published in this repository.
        """
        if self.ENV.strip().lower() == "production":
            self.ENV = "prod"
        if not self.is_production:
            return self

        problems: list[str] = []

        driver = self.INTEGRATIONS_DRIVER.strip().lower()
        if driver == "sim" and "INTEGRATIONS_DRIVER" in self.model_fields_set:
            problems.append(
                "INTEGRATIONS_DRIVER=sim is not allowed with ENV=prod (production serves the live "
                "WhatsApp Cloud API and the live mobile-calls webhook). Remove it or set it to `live`."
            )
        elif driver not in {"sim", "live", ""}:
            problems.append(f"INTEGRATIONS_DRIVER must be `live` or unset with ENV=prod, got {driver!r}.")
        self.INTEGRATIONS_DRIVER = "live"

        if len(self.JWT_SECRET) < 32 or self.JWT_SECRET == DEV_JWT_SECRET:
            problems.append(
                "JWT_SECRET must be a private value of >= 32 chars (openssl rand -hex 32); the default "
                "and the values published in this repository are refused."
            )

        if self.DEBUG:
            problems.append("DEBUG must be false with ENV=prod (it leaks tracebacks and SQL to clients).")

        if not self.COOKIE_SECURE:
            problems.append("COOKIE_SECURE must be true with ENV=prod (the refresh cookie must be HTTPS-only).")

        origins = [origin.strip() for origin in self.CORS_ORIGINS if origin.strip()]
        if not origins:
            problems.append("CORS_ORIGINS must list the SPA origin(s) with ENV=prod; an empty list is a guess.")
        else:
            unsafe = [
                origin
                for origin in origins
                if origin == "*" or "localhost" in origin or "127.0.0.1" in origin or "//[::1]" in origin or origin.startswith("http://")
            ]
            if unsafe:
                problems.append("CORS_ORIGINS must be the real https origin(s) with ENV=prod; refused: " + ", ".join(unsafe))

        if len(self.SECRETS_ENCRYPTION_KEY.strip()) < 16:
            problems.append(
                "SECRETS_ENCRYPTION_KEY is required with ENV=prod (>= 16 chars, or a Fernet key): stored "
                "integration credentials must not be encrypted with a value derived from JWT_SECRET."
            )

        if not self.is_postgres:
            problems.append(
                "DATABASE_URL must point at PostgreSQL (postgresql+asyncpg://) with ENV=prod: SQLite has no "
                "row-level security and takes one writer at a time."
            )

        storage = self.STORAGE_BACKEND.strip().lower()
        if storage == "s3":
            missing = [
                name
                for name, value in (
                    ("S3_BUCKET", self.S3_BUCKET),
                    ("S3_ACCESS_KEY", self.S3_ACCESS_KEY),
                    ("S3_SECRET_KEY", self.S3_SECRET_KEY),
                )
                if not value.strip()
            ]
            if missing:
                problems.append("STORAGE_BACKEND=s3 needs " + ", ".join(missing) + " with ENV=prod.")
        elif storage == "local":
            directory = str(self.STORAGE_DIR)
            if not directory.startswith("/"):
                problems.append("STORAGE_DIR must be an absolute path when STORAGE_BACKEND=local.")
            elif directory.startswith(("/tmp", "/var/tmp", "/dev/shm")):
                problems.append(
                    f"STORAGE_DIR={directory} is a temporary directory: attachments and recordings would "
                    "disappear on restart. Point it at a mounted volume or use STORAGE_BACKEND=s3."
                )
        else:
            problems.append(f"STORAGE_BACKEND must be `s3` or `local` with ENV=prod, got {storage!r}.")

        if self.CELERY_TASK_ALWAYS_EAGER:
            problems.append(
                "CELERY_TASK_ALWAYS_EAGER must be false with ENV=prod: it runs every task inline in the "
                "request that queued it (PDF rendering, exports, SLA sweeps) and no worker is used."
            )

        if problems:
            raise ValueError(
                "Refusing to start with ENV=prod — fix these settings first:\n" + "\n".join(f"  - {problem}" for problem in problems)
            )

        for field_name, placeholder in DEV_PLACEHOLDER_SECRETS.items():
            if getattr(self, field_name) == placeholder:
                setattr(self, field_name, "")
        return self

    @property
    def is_production(self) -> bool:
        """The single place the rest of the app asks `is this serving real customers?`."""
        return self.ENV.strip().lower() in PRODUCTION_ENVS

    @property
    def simulation_allowed(self) -> bool:
        """Simulated drivers exist only outside production (R9 §1.A)."""
        return not self.is_production and self.ENV.strip().lower() in SIMULATION_ENVS

    # --- deployment ---
    # Absolute origin used to render webhook URLs for copy-paste (Meta asks for a public callback URL).
    PUBLIC_BASE_URL: str = "http://localhost:5173"

    @property
    def is_postgres(self) -> bool:
        return self.DATABASE_URL.startswith("postgres")

    @property
    def smtp_endpoint(self) -> tuple[str, int]:
        """The host/port actually used to send: the discrete override if set, else the parsed URL."""
        if self.SMTP_HOST.strip():
            return self.SMTP_HOST.strip(), self.SMTP_PORT or 1025
        rest = self.SMTP_URL.strip().replace("smtp://", "").replace("smtp+tls://", "").split("@")[-1]
        host, _, port = rest.partition(":")
        return host or "localhost", int(port or 1025)

    @property
    def smtp_url(self) -> str:
        """One place assembles the DSN, so no driver parses two shapes."""
        host, port = self.smtp_endpoint
        auth = f"{self.SMTP_USERNAME}@" if self.SMTP_USERNAME else ""
        return f"smtp://{auth}{host}:{port}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
