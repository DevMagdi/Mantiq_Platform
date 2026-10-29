# infra/ — local stack and containers

One `docker compose` file brings up the whole platform on a laptop: PostgreSQL 16, Redis 7, the API,
a Celery worker, Celery beat, and the built SPA behind nginx. Attachments live on the `objectsdata`
volume (local storage driver); point `STORAGE_BACKEND=s3` at your own S3/MinIO to use object storage —
the MinIO *images* are no longer pullable anonymously (ADR-020).

| Service | Port (host) | Notes |
| --- | --- | --- |
| frontend (nginx) | `WEB_PORT` = 5173 | SPA + `/api` proxy to the backend; the only place the API origin lives |
| backend | `API_PORT` = 8000 | FastAPI, `/healthz` + `/readyz` for probes, `/docs` for OpenAPI |
| postgres | `PG_PORT` = 5432 | `brokerhub` database, RLS policies from the migrations |
| redis | — | broker + result backend for Celery |
| objects (`objectsdata`) | — | attachments on disk, `STORAGE_DIR=/srv/objects`; `STORAGE_BACKEND=s3` switches to a bucket |
| mailpit | 1025 / 8025 | dev SMTP sink + web UI — every notification email lands here, nothing leaves the laptop |

The WhatsApp/call **simulators are in-process** (`GET /api/v1/dev/sim`, `POST /api/v1/dev/sim/{whatsapp,call}`),
not separate containers: they must run the same intake code (phone normalization → insured/lead matching),
and a second service would duplicate that logic. See `docs/decisions/011-monitoring-and-mailpit.md`.

## Commands

```bash
cp infra/.env.example infra/.env            # then set JWT_SECRET
docker compose -f infra/docker-compose.yml up -d --build
docker compose -f infra/docker-compose.yml exec backend python -m app.seed   # demo data (fictitious)
open http://localhost:5173
```

`migrate` runs `alembic upgrade head` once and exits, gated with `service_completed_successfully`, so
`up -d` alone is enough for a clean start — only the seed is manual, so nobody accidentally seeds a shared
environment.

Useful follows:

```bash
docker compose -f infra/docker-compose.yml logs -f backend beat
docker compose -f infra/docker-compose.yml exec backend python -m pytest -q          # tests inside the image
docker compose -f infra/docker-compose.yml exec backend alembic revision --autogenerate -m "msg"
docker compose -f infra/docker-compose.yml down            # keep volumes
docker compose -f infra/docker-compose.yml down -v         # throw the data away
```

## Switching the simulator off

`INTEGRATIONS_DRIVER=sim` is the default here so the inbox can be exercised without vendor accounts
(`GET /api/v1/dev/sim` is the panel). For a live pilot set `INTEGRATIONS_DRIVER=live` and fill
`WHATSAPP_*` / `TELEPHONY_*` in `infra/.env`; the simulator endpoints then answer 403 by design.

Never commit real credentials: `.env` is git-ignored, and only `.env.example` is tracked.

## Production notes

- Terminate TLS in front of nginx and set `COOKIE_SECURE`/`COOKIE_DOMAIN` to match the public origin.
- `DEBUG=false` and a strong `JWT_SECRET` are required — the app refuses to serve the simulator in
  non-`sim` + non-`DEBUG` mode, and refuses default secrets at startup where it can.
- Backups: `pg_dump` on a schedule plus the MinIO bucket (voice notes and recordings live there).
- Migrations are forward-only: never edit a migration that has been merged (add a new one instead).
