"""Settings as they arrive from the environment — the form a human writes, not the form a model wants.

pydantic-settings decodes every `list[...]` field with `json.loads` before any validator runs, so
`CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173` — the exact line `infra/.env.example` has always
documented, and the exact value `infra/docker-compose.yml` passes — raised
`SettingsError: error parsing value for field "CORS_ORIGINS"` at *import* time. The API container exited
before it could answer `/healthz`, so `docker compose up` could never have worked (R3: the docker job only
reached this code path once the deleted MinIO image stopped masking it).
"""

from __future__ import annotations

import pytest
from app.core.config import Settings


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings()


def test_cors_origins_accepts_the_comma_separated_form_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, CORS_ORIGINS="http://localhost:5173,http://127.0.0.1:5173")
    assert settings.CORS_ORIGINS == ["http://localhost:5173", "http://127.0.0.1:5173"]


def test_cors_origins_still_accepts_a_json_array(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, CORS_ORIGINS='["https://app.brokerhub.test"]')
    assert settings.CORS_ORIGINS == ["https://app.brokerhub.test"]


def test_weekend_days_accepts_both_forms(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _settings(monkeypatch, WEEKEND_DAYS="4,5").WEEKEND_DAYS == [4, 5]
    assert _settings(monkeypatch, WEEKEND_DAYS="[5]").WEEKEND_DAYS == [5]


def test_the_default_still_holds_when_the_variable_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert Settings().CORS_ORIGINS == ["http://localhost:5173"]


def test_a_malformed_json_array_is_a_readable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(Exception) as raised:
        _settings(monkeypatch, CORS_ORIGINS='["http://a.test"')
    assert "comma-separated list or a JSON array" in str(raised.value)
