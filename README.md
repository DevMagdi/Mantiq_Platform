# Mantiq · منطق

**منصة إدارة الوساطة التأمينية** — an insurance-brokerage operations platform: leads and a sales
kanban, a unified WhatsApp/phone inbox, client files, policies and installments, policy issuance with
generated PDFs, an accounting ERP hub, reports, a client portal, and a settings hub for the live
channel providers.

Arabic (RTL) is the primary language; English mirrors the interface. The product name is **Mantiq ·
منطق**, operated by **Mantiq Technologies** — brand rules live in
[ADR-034](docs/decisions/034-mantiq-brand.md).

> Status: this repository is being prepared for a **live production hand-over**. Read
> [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md) first — it lists every production gate,
> what is verified, and the two items that still need a decision from the owner.

---

## What is in here

| Path | What it is |
| --- | --- |
| `backend/` | FastAPI + SQLAlchemy (async) + Alembic + Celery. Python **3.12**. |
| `frontend/` | React + TypeScript + Vite + Tailwind SPA. Node **≥ 20** (CI and images use 22). |
| `infra/` | Dockerfiles, the laptop compose stack, and `docker-compose.prod.yml` for production. |
| `docs/` | Status, runbook, pilot pack, ADRs, API spec, load-test evidence, UI-parity screenshots. |
| `docs/api/openapi.json` | Generated from the app; `frontend/src/lib/api/schema.d.ts` is generated from it. |

---

## Run it locally (no Docker)

```bash
# backend — PostgreSQL is the target, but SQLite works for a laptop run
cd backend
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env                       # optional; every key has a dev default
alembic upgrade head
python -m app.seed                         # demo data — refuses to run when ENV=prod
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

```bash
# frontend — in a second terminal
cd frontend
npm ci
VITE_API_ORIGIN=http://127.0.0.1:8000 npm run dev     # http://localhost:5173
```

Sign in with a seeded account (`manager@mantiq.test` … `admin@mantiq.test`); the demo password is
printed on the sign-in screen and can be overridden with `SEED_PASSWORD` before seeding.

**SQLite caveat:** row-level security, the migration chain and the concurrency tests only run for real
against PostgreSQL. Use the compose stack below if you are touching permissions, RLS or isolation.

**Two workers, like production (R15):** the deployed API runs `--workers 2`, and one behaviour only exists
when it does — the change feed crosses processes over PostgreSQL `LISTEN/NOTIFY`, so a tab attached to worker A
still hears a change handled by worker B. Reproduce that locally:

```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
python scripts/load/r15_fanout_probe.py --base http://127.0.0.1:8000 --attempts 12   # expects 12/12
```

On PostgreSQL the feed logs `realtime.fanout_ready mode=shared` per worker. If your database cannot hold a
`LISTEN` session open (PgBouncer in *transaction* pooling mode), set `REALTIME_FANOUT=off`: the feed becomes
process-local, and every tab repairs itself through the revision probe (`?since=`) instead.

## Run it locally (Docker)

```bash
cp infra/.env.example infra/.env            # set JWT_SECRET for anything shared
docker compose -f infra/docker-compose.yml up -d --build
docker compose -f infra/docker-compose.yml exec backend python -m app.seed
curl -fsS http://localhost:8000/readyz     # {"status":"ready","database":"ok"}
```

Postgres, Redis, the API, a Celery worker + beat, and the built SPA behind nginx. Mail is captured by
mailpit at <http://localhost:8025> — nothing leaves the machine.

## Run it in production

```bash
cp infra/.env.prod.example infra/.env.prod
chmod 600 infra/.env.prod
# fill in every CHANGE-ME: JWT_SECRET, SECRETS_ENCRYPTION_KEY, DATABASE_URL, CORS_ORIGINS,
# PUBLIC_BASE_URL, EMAIL_FROM, and either S3_* or a durable STORAGE_DIR.
docker compose -f infra/docker-compose.prod.yml --env-file infra/.env.prod up -d
```

`ENV=prod` makes the API **validate the whole configuration at boot** and refuse to start on: a weak or
published `JWT_SECRET`, an empty `SECRETS_ENCRYPTION_KEY`, `DEBUG=true`, `COOKIE_SECURE=false`, a CORS
origin that is `*`/`localhost`/plain `http`, SQLite, a temp storage directory, or eager Celery. Every
problem is reported in one error, so a misconfigured deploy fails before it takes traffic.

The production stack also differs from the laptop one by: no mailpit, no published Postgres/Redis
ports, TLS in nginx with HSTS, restart policies, healthchecks (readiness for the API, SSE-safe proxying
for the change feed), resource limits and log rotation.

> **Before the first real deployment**, follow the checklist in
> [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md): rotate the secrets, purge `backend/.env`
> from the git history, enable and test RLS against PostgreSQL, and point the provider credentials.

## Verify a change

```bash
cd backend  && pytest -q && ruff check . && mypy app
cd frontend && npm run codegen:check && npm run lint && npm run typecheck && npm test && npm run build
```

End-to-end (needs a running stack; `E2E_USE_PREVIEW=1` runs against the built bundle, which is what CI does):

```bash
cd frontend && E2E_USE_PREVIEW=1 npx playwright test
```

## Documentation

| Document | Read it when |
| --- | --- |
| [docs/STATUS.md](docs/STATUS.md) | You want the round-by-round record and the literal verification output. |
| [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md) | You are deploying, or auditing what is production-safe. |
| [docs/PILOT_PACK.md](docs/PILOT_PACK.md) | You are running a pilot or a demo for a customer. |
| [docs/runbook.md](docs/runbook.md) | Something is on fire: operations, monitoring, incidents, permissions. |
| [docs/decisions/](docs/decisions/) | You need *why* a choice was made (architecture, brand, security, integrations). |
| [docs/qa/](docs/qa/) · [docs/perf/](docs/perf/) | You need load-test and QA evidence. |

## License and data

No real personal data is in this repository. The seed creates fictitious people, Egyptian insurer
names from a public register, and numbers that are not issued. Demo credentials are development-only
and `python -m app.seed` refuses to run under `ENV=prod`.
