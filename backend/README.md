# Backend — FastAPI + SQLAlchemy 2 (async)

Python 3.11+, strict typing (`mypy --strict`), Arabic-first responses, one central isolation layer.

## Layout

```
app/core/          settings, errors (uniform {code,message,details}), security/JWT, scope (RLS), deps
app/domain/        enums, permissions matrix, phone (E.164), sla (business-hours clock), lead FSM
app/models/        SQLAlchemy 2.0 typed models (33 tables) — no business logic here
app/repositories/  query building + `with_scope` (every list/detail query is scoped)
app/services/      the rules: leads/convert, requests/SLA/replies, clients/import, portal, reports/exports, auth, audit
app/integrations/  provider protocols + real & simulator drivers (WhatsApp, telephony, storage, notify, AI)
app/api/           serializers (the only place wire shapes are decided) + v1/endpoints/*
app/workers/       celery_app (beat schedule) + tasks: sla sweep, notifications, imports, media retention
app/seed.py        deterministic demo data (fictitious people, +201… numbers, @brokerhub.test)
migrations/        alembic — 0001 baseline, 0002 PostgreSQL RLS + partial indexes
scripts/           export_openapi.py, check_openapi_fresh.py, check_core_coverage.py, smoke_api.py
tests/             124 tests: auth, isolation matrix, intake/webhooks, leads flow, clients, import, portal,
                   exports (XLSX scoping), /metrics, /ai (flag, consent, human review) and issuing scoping
```

## Commands

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env                       # optional: everything has a dev default

alembic upgrade head                       # PostgreSQL, or DATABASE_URL=sqlite+aiosqlite:///./dev.db
python -m app.seed                         # demo data (idempotent)
uvicorn app.main:app --reload --port 8000  # /docs, /openapi.json

celery -A app.workers.celery_app:celery_app worker --loglevel INFO
celery -A app.workers.celery_app:celery_app beat --loglevel INFO

ruff check . && ruff format --check . && mypy app
pytest -q                                  # SQLite by default — no services needed
coverage run -m pytest -q && coverage report
```

## Testing rules (spec §10)

- `pytest` runs the whole suite on SQLite in-process (`httpx.ASGITransport`), so CI is fast and local.
- Real-Postgres behaviour (RLS policies, partial unique indexes) lives behind `@pytest.mark.postgres_only`
  and runs in CI with `DATABASE_URL_TEST` pointed at a PostgreSQL 16 service.
- Coverage is gated per core module (`python scripts/check_core_coverage.py`) rather than on a global
  average, because the spec names SLA, scope, lead conversion, the importer and the portal.
- `concurrency = ["thread", "greenlet"]` in `pyproject.toml` is not decoration: without it SQLAlchemy's
  async greenlets make the report understate the truth by 3–8× (measured on `app/services/leads.py`).

## Conventions that are not negotiable

1. **No hand-written DTOs on the frontend.** The OpenAPI file is exported (`scripts/export_openapi.py`) to
   `docs/api/openapi.json`, and the SPA generates its types. If a response shape is wrong, fix the
   Pydantic model, not the TypeScript.
2. **Out-of-scope is 404, never 403** — enforced in the repository layer through `Scope`, tested in
   `tests/test_isolation_matrix.py`.
3. **Phone numbers enter through `domain.phone.normalize_mobile` and leave through `mask_mobile`.**
4. **Every mutation writes an audit row** inside the same transaction (`services/audit.py`), and audit rows
   are never updated or deleted.
5. Providers are protocols with a `sim` and a `live` driver; the simulator endpoints are the only dev
   surface and are refused unless `INTEGRATIONS_DRIVER=sim` or `DEBUG=true`.
