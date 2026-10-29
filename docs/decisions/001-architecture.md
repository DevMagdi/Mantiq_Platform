# ADR-001: Broker Hub Architecture (layers, isolation, providers, SLA)

- Status: Accepted
- Date: 2026-10-26
- Deciders: Principal Architect (auto), pending owner review
- Milestone: M0

## Context

Broker Hub replaces a paper/Excel/WhatsApp driven insurance-brokerage workflow in Egypt. The system must serve
three very different audiences with hard data-isolation requirements from one codebase:

1. Internal staff (sales reps, sales manager, customer-service agents, account managers).
2. Client-company owners (`portal_role: owner|contact`) who must see only their own company.
3. External event sources (WhatsApp Cloud API webhooks, telephony/central events) that write into the same
   ticket stream.

The product spec (`PROMPT.md`, mirrored in `README.md`) fixes the stack: React 18 + TS strict + Vite + Tailwind +
TanStack Query + RHF/Zod + dnd-kit + Recharts + i18next (Arabic-first RTL) on the front; FastAPI + Pydantic v2 +
SQLAlchemy 2 async + Alembic + PostgreSQL 16 + Redis + Celery + MinIO on the back.

## Decision

### 1. Backend layering (strict, one-way dependencies)

```
api  ->  services  ->  repositories  ->  models (SQLAlchemy)
  |          |
  |          -> domain (pure logic: phone, SLA, lead FSM, conversion)
  -> integrations (abstract providers)  -> workers (Celery tasks; call services only)
```

- `app/api` — FastAPI routers. No SQL, no business rules. Only: request validation (Pydantic), dependency
  injection (`CurrentPerms`, `DbSession`, `Scope`), response models, HTTP status mapping.
- `app/services` — use-cases and transactions (`auth`, `leads`, `clients`, `insured`, `requests`, `dashboard`,
  `portal`, `webhooks`, `users`, `imports`). Owns `async with session.begin()`.
- `app/repositories` — the *only* place SQLAlchemy `select()` is written. Every list/detail query takes a
  `Scope` argument, so isolation is structurally impossible to forget.
- `app/domain` — framework-free, fully unit-testable: E.164 phone normalisation, SLA arithmetic, lead status
  machine, conversion rules. No imports from FastAPI/SQLAlchemy.
- `app/models` — table definitions + enums only.
- `app/integrations` — `WhatsAppProvider` / `TelephonyProvider` / `StorageProvider` / `NotifierProvider`
  protocols with `sim` + real implementations (see §3).
- `app/core` — settings, security, logging, errors, DB engine, OpenAPI metadata.

### 2. Data-isolation strategy (single central chokepoint)

Requirement: "a sales rep sees/edits only leads where `assigned_to = me` and clients where
`account_owner_id = me`; an out-of-scope id returns **404**, never 403."

Implementation: `app/core/scope.py::build_scope(current_user, perms)` produces an immutable `Scope`
(`mode: all | owner | seeded | portal_client | insurer`), injected by a FastAPI dependency. Repositories accept
`scope` as a required keyword and append the predicate to *both* list and by-id lookups:

- list → `WHERE <scope predicate>` (so counts/Kanban stats/board share the identical predicate);
- by id → `SELECT ... WHERE id = :id AND <scope predicate>`; empty result ⇒ `404 ResourceNotFound`.

Because the by-id path is the same code as the list path, filters, search, sorting, paging, KPI counters,
Kanban boards and exports cannot leak by being implemented separately. `PATCH /leads/{id}/assign` additionally
requires the `leads.assign` permission (manager only) — enforced by `require_perm`.

Row-Level Security is layered on `leads` and `clients` as a *second* net (migration `0002`; see ADR-003 for the current wiring gap on the request path). Portal users get a
`Scope(mode="portal_client", client_id=…)` built from `client_portal_users`, which also strips internal notes,
commissions, rep identity and call recordings at the *serializer* level.

### 3. Providers (every external system behind an interface)

```python
class WhatsAppProvider(Protocol):    # verify_signature, fetch_media, send_text, send_template
class TelephonyProvider(Protocol):   # verify_signature
class StorageProvider(Protocol):     # put_object, signed_get_url(ttl), delete
class NotifierProvider(Protocol):    # email, inapp (, sms — interface only)
class AIProvider(Protocol):          # Phase 2: transcribe, summarize, classify, draft_reply
```

`INTEGRATIONS_DRIVER=sim|live` selects the implementation in `app/integrations/registry.py`. The `sim` driver is
backed by the in-repo simulator service (`infra/simulator`) + dev endpoints, so the *entire* inbound→ticket→reply
loop and the call→recording→player loop run with zero external accounts. This is what makes the Playwright E2E
suite hermetic. Media always goes through `StorageProvider` (MinIO in compose, signed URLs of short TTL, every
listen audited).

### 4. SLA strategy

- `sla_policies(priority, first_response_minutes, resolution_minutes)` + `business_hours` (Fri/Sat weekend by
  default) drive `sla_due_at`, computed **at write time** by `domain/sla.py` and stored, so list queries never
  recompute (index-friendly, sortable, exportable).
- `first_response_at` is stamped on the first *outbound* `request_messages` row.
- Celery beat `sla_sweep` (every 60 s) is the only writer of `requests.is_overdue` and the only source of
  "due in N min" / "late by N h" notifications; it is idempotent and re-computes from stored fields, so a missed
  tick self-heals. The UI reads `is_overdue` + `sla_due_at` and renders a red badge — it never re-derives rules
  client-side (single source of truth on the server).

### 5. Frontend architecture

Feature-sliced (`src/features/{auth,dashboard,inbox,leads,clients,insured,portal,users,phase2}`), each owning its
routes, queries, and components; `src/components` holds only the shared design-system primitives required by the
spec (KPI, Badge, Stepper, Kanban, Toggle, DataTable, Chip, Avatar, Segmented). Server state is TanStack Query
only; no Redux. **All API types are generated** from the backend's OpenAPI document
(`openapi-typescript`) — handwritten response DTOs are forbidden in review. Arabic RTL is the default document
direction and spacing utilities are logical (`ms-*/ps-*`) so LTR English is a config change, not a refactor.

### 6. Database portability rule (enables local dev without Docker)

UUIDs are generated in Python (`uuid4`), `requests.number` comes from a counter row updated with
`SELECT … FOR UPDATE` (portable, no PG sequence), `sa.Uuid`/`sa.JSON`/`sa.Numeric` are used instead of PG-only
types, search uses `lower() LIKE`. Anything genuinely PG-only (RLS, partial unique indexes on active rows,
`citext`) lives in its own guarded migration (`if dialect == "postgresql"`). Consequence: `pytest` runs against
SQLite locally *and* PostgreSQL 16 in CI, and `uvicorn` boots on a laptop with no Docker.

### 7. Migrations

Alembic with one forward-only baseline per milestone. Rule from the spec: a migration that has been merged is
never edited — new migrations only. `scripts/check_migrations.py` in CI fails on edits to merged revisions.

## Consequences

**Positive**

- Isolation is one function + one test matrix instead of N ad-hoc `if user.role == …` branches.
- Sim/live providers make the full Phase-1 loop demoable and E2E-testable offline.
- SLA stored ⇒ dashboards are cheap SQL, and the numbers in UI, API and exports agree by construction.
- Portability rule means contributors can start with `pip install -r requirements.txt` alone.

**Negative / accepted trade-offs**

- Repository layer adds boilerplate for trivial CRUD — accepted for the isolation guarantee.
- Python-side UUID generation loses DB defaults — negligible.
- RLS and partial unique indexes only enforce on Postgres: production (PG) has both nets; SQLite dev has one.
  CI always runs PG, so drift is caught.
- Storing `sla_due_at` requires a recompute when priority changes; handled inside `requests` service in the same
  transaction.

## Alternatives considered

1. **Policy checks in routers (`Depends(require_owner)` sprinkled per route).** Rejected: forgettable, and it
   cannot cover aggregates/Kanban counts uniformly — the spec explicitly requires KPI/Board/export scoping.
2. **Row-Level Security as the primary mechanism.** Rejected as primary (hard to express UI-side filters and
   portal roles, breaks admin tooling, untestable on SQLite), accepted as a secondary net.
3. **Django + DRF (all-in-one framework).** Rejected: spec fixes FastAPI; also weaker fit for async Celery-heavy
   event intake and Pydantic/Zod end-to-end validation symmetry.
4. **Server-rendered admin (e.g. FastAPI + Jinja).** Rejected: Kanban drag-and-drop, three-pane inbox and portal
   demand a client app; spec fixes React SPA.
5. **Hand-written API DTOs in the frontend.** Rejected per spec — generated from OpenAPI, verified in CI.
6. **Compute SLA on read.** Rejected: unsortable, unfilterable, and dashboard-vs-list drift.
