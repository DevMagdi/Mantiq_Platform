"""Production boot guards — one test per setting that must stop the process.

These are the "would this survive a real deployment?" checks that an operator only discovers at 2 a.m.
otherwise. Each test starts from a *complete, valid* production configuration and breaks exactly one
value, so a failure names the rule and nothing else. `test_a_complete_production_config_boots` is the
control: if the baseline ever stops booting, the rest of this file proves nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from app.core.config import Settings

#: A production environment in which every guard is satisfied. Individual tests override one key.
VALID_PRODUCTION: dict[str, str] = {
    "ENV": "prod",
    "INTEGRATIONS_DRIVER": "live",
    "JWT_SECRET": "0123456789abcdef0123456789abcdef",  # 32 chars, not a published value
    "SECRETS_ENCRYPTION_KEY": "a-fernet-or-passphrase-of-32-chars",
    "DEBUG": "false",
    "COOKIE_SECURE": "true",
    "CORS_ORIGINS": "https://app.mantiq.example",
    "DATABASE_URL": "postgresql+asyncpg://mantiq:secret@postgres:5432/mantiq",
    "STORAGE_BACKEND": "s3",
    "S3_BUCKET": "mantiq-prod",
    "S3_ACCESS_KEY": "access",
    "S3_SECRET_KEY": "secret",
    "CELERY_TASK_ALWAYS_EAGER": "false",
}


@contextmanager
def production(**overrides: str) -> Iterator[Settings]:
    """Build `Settings` from `VALID_PRODUCTION` + overrides, with no `.env` file and no cache."""
    import os

    values = {**VALID_PRODUCTION, **overrides}
    keys = set(values) | set(VALID_PRODUCTION) | set(overrides)
    saved = {key: os.environ.get(key) for key in keys}
    for key in keys:
        os.environ.pop(key, None)
    os.environ.update(values)
    try:
        yield Settings(_env_file=None)  # type: ignore[call-arg]
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def refusal(**overrides: str) -> str:
    """The boot error text for a broken production configuration."""
    with pytest.raises(Exception) as raised, production(**overrides):
        pass
    return str(raised.value)


def test_a_complete_production_config_boots() -> None:
    with production() as settings:
        assert settings.is_production is True
        assert settings.ENV == "prod"
        assert settings.INTEGRATIONS_DRIVER == "live"


def test_the_production_alias_is_the_same_mode() -> None:
    with production(ENV="production") as settings:
        assert settings.ENV == "prod"
        assert settings.is_production is True


def test_debug_must_be_off() -> None:
    assert "DEBUG must be false" in refusal(DEBUG="true")


def test_the_refresh_cookie_must_be_secure() -> None:
    assert "COOKIE_SECURE must be true" in refusal(COOKIE_SECURE="false")


@pytest.mark.parametrize(
    "origins",
    ["*", "http://localhost:5173", "https://app.mantiq.example,http://127.0.0.1:5173", "http://app.mantiq.example"],
)
def test_cors_must_name_real_https_origins(origins: str) -> None:
    """A wildcard or a localhost origin in production is an any-origin API with credentials attached."""
    assert "CORS_ORIGINS must be the real https origin(s)" in refusal(CORS_ORIGINS=origins)


def test_cors_must_not_be_empty() -> None:
    assert "CORS_ORIGINS must list the SPA origin" in refusal(CORS_ORIGINS="")


def test_a_short_jwt_secret_is_refused() -> None:
    assert "JWT_SECRET must be a private value" in refusal(JWT_SECRET="too-short")


def test_the_published_dev_jwt_is_refused_even_though_it_is_34_chars() -> None:
    """A known secret is worse than a missing one — length alone would have let this through."""
    assert "JWT_SECRET must be a private value" in refusal(JWT_SECRET="dev-only-insecure-secret-change-me")


def test_secrets_encryption_key_is_required_at_boot() -> None:
    assert "SECRETS_ENCRYPTION_KEY is required" in refusal(SECRETS_ENCRYPTION_KEY="")


def test_sqlite_is_refused_as_the_production_database() -> None:
    assert "DATABASE_URL must point at PostgreSQL" in refusal(DATABASE_URL="sqlite+aiosqlite:///./prod.db")


def test_a_temporary_storage_directory_is_refused() -> None:
    text = refusal(STORAGE_BACKEND="local", STORAGE_DIR="/tmp/objects")
    assert "temporary directory" in text


def test_a_durable_local_storage_directory_is_accepted() -> None:
    with production(STORAGE_BACKEND="local", STORAGE_DIR="/srv/objects") as settings:
        assert settings.STORAGE_BACKEND == "local"


def test_s3_storage_needs_credentials() -> None:
    assert "STORAGE_BACKEND=s3 needs S3_ACCESS_KEY, S3_SECRET_KEY" in refusal(S3_ACCESS_KEY="", S3_SECRET_KEY="")


def test_eager_celery_is_refused() -> None:
    assert "CELERY_TASK_ALWAYS_EAGER must be false" in refusal(CELERY_TASK_ALWAYS_EAGER="true")


def test_an_explicit_simulation_driver_is_refused() -> None:
    assert "INTEGRATIONS_DRIVER=sim is not allowed" in refusal(INTEGRATIONS_DRIVER="sim")


def test_an_unset_driver_becomes_live_instead_of_refusing_to_boot() -> None:
    """The old rule compared against the `sim` *default*, so its own advice ("remove the variable") failed."""
    with production(INTEGRATIONS_DRIVER="") as settings:
        assert settings.INTEGRATIONS_DRIVER == "live"


def test_every_broken_setting_is_reported_in_one_boot_error() -> None:
    """One restart, one list. Operators should not discover the checklist one failure at a time."""
    text = refusal(DEBUG="true", COOKIE_SECURE="false", CELERY_TASK_ALWAYS_EAGER="true")
    assert text.count("  - ") >= 3
    for needle in ("DEBUG must be false", "COOKIE_SECURE must be true", "CELERY_TASK_ALWAYS_EAGER"):
        assert needle in text


def test_development_is_untouched_by_the_production_rules() -> None:
    """A laptop run must not need TLS, Postgres or S3 to start — otherwise nobody can develop."""
    import os

    saved = {key: os.environ.get(key) for key in ("ENV", "DEBUG", "COOKIE_SECURE", "DATABASE_URL")}
    os.environ.update({"ENV": "dev", "DEBUG": "true", "COOKIE_SECURE": "false", "DATABASE_URL": "sqlite+aiosqlite:///./dev.db"})
    try:
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    assert settings.is_production is False
    assert settings.DEBUG is True
    assert settings.COOKIE_SECURE is False


# --- the demo seeder ----------------------------------------------------------------


def test_the_seeder_refuses_to_run_in_production() -> None:
    """The seeded accounts share a published password; production seats are created on purpose."""
    from app.seed import ProductionSeedRefused, assert_seed_allowed

    for env in ("prod", "production", "PROD", " Production "):
        with pytest.raises(ProductionSeedRefused):
            assert_seed_allowed(env)


def test_the_seeder_runs_in_dev_and_test() -> None:
    from app.seed import assert_seed_allowed

    for env in ("dev", "test", "local", ""):
        assert_seed_allowed(env)  # returning at all is the assertion: it must not raise
