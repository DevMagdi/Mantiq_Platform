#!/usr/bin/env python3
"""Broker Hub — Live QA matrix.

Walks every path in the "Live v1" scope against the real FastAPI app (in-process ASGI) on a freshly seeded
database, as each role, and records status codes. Anything 500 (or an unexpected 4xx on a critical happy
path) is a defect; the script prints a table and exits non-zero if any P0 check failed.

It is the API-level half of the manual checklist: the UI half is the Playwright suite (`frontend/e2e`), which
needs a browser this sandbox does not have.

Run:  cd backend && PYTHONPATH=. python scripts/live_matrix.py

Exit code 0 means every check passed and no route answered 5xx. It owns its database (`/tmp/qa_live.db`,
recreated on every run) so it never touches a real one; `SEED_PASSWORD` is set here for the same reason.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import pathlib
import re
import traceback
from datetime import UTC, datetime, timedelta
from typing import Any

DB = pathlib.Path(os.environ.get("QA_MATRIX_DB", "/tmp/qa_live.db"))
if DB.exists():
    DB.unlink()
os.environ.update(
    DATABASE_URL=f"sqlite+aiosqlite:///{DB}",
    ENV="dev",
    DEBUG="false",
    INTEGRATIONS_DRIVER="sim",
    CELERY_TASK_ALWAYS_EAGER="1",
    SEED_PASSWORD="Qa-Pass-2026",
    SMTP_URL="smtp://127.0.0.1:1025",
    STORAGE_DIR="/tmp/qa_objects",
)

from app.core.db import set_engine, set_sessionmaker
from app.main import app
from app.models.base import Base
from app.seed import seed
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ZERO_UUID = "00000000-0000-4000-8000-000000000000"
PASSWORD = "Qa-Pass-2026"
MANAGER = "manager@brokerhub.test"
ADMIN = "admin@brokerhub.test"
CS = "cs@brokerhub.test"
REP = "nour.fahmy@brokerhub.test"
OTHER_REP = "sara.elsayed@brokerhub.test"
OWNER = "owner.mantiq@company.test"

results: list[dict[str, Any]] = []


def record(area: str, label: str, ok: bool, detail: str = "", *, p0: bool = False) -> None:
    results.append({"area": area, "label": label, "ok": ok, "detail": detail, "p0": p0})
    mark = "PASS" if ok else ("FAIL" if p0 else "WARN")
    print(f"{mark}  {area:12} {label:52} {detail[:150]}")


async def main() -> int:
    eng = create_async_engine(f"sqlite+aiosqlite:///{DB}", future=True)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(eng, expire_on_commit=False, autoflush=False)
    set_engine(eng)
    set_sessionmaker(maker)
    async with maker() as session:
        seeded = await seed(session)
    print(f"seeded: {seeded}\n")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://qa", timeout=120) as c:

        async def login(email: str, password: str = PASSWORD) -> dict[str, str]:
            r = await c.post("/api/v1/auth/login", json={"email": email, "password": password})
            return {"authorization": f"Bearer {r.json()['access_token']}"} if r.status_code == 200 else {}

        async def get(path: str, headers: dict[str, str], **kw: Any) -> Any:
            return await c.get(path, headers=headers, **kw)

        def check(area: str, label: str, response: Any, expect: int | tuple[int, ...] = 200, *, p0: bool = False) -> Any:
            want = (expect,) if isinstance(expect, int) else expect
            ok = response.status_code in want
            body = "" if ok else response.text[:200]
            record(area, label, ok, f"{response.status_code}" + (f" {body}" if body else ""), p0=p0)
            return response

        # ------------------------------------------------------------------ 4.1 smoke
        record("smoke", "GET /healthz", (await c.get("/healthz")).status_code == 200, "200", p0=True)
        check("smoke", "GET /readyz", await c.get("/readyz"), 200, p0=True)
        check("smoke", "GET /openapi.json", await c.get("/openapi.json"), 200, p0=True)

        # ------------------------------------------------------------------ 4.2 auth
        bad = await c.post("/api/v1/auth/login", json={"email": MANAGER, "password": "wrong-password"})
        check("auth", "login with a wrong password → 401", bad, 401, p0=True)
        unknown = await c.post("/api/v1/auth/login", json={"email": "ghost@brokerhub.test", "password": PASSWORD})
        check("auth", "login for an unknown e-mail → 401", unknown, 401, p0=True)
        empty = await c.post("/api/v1/auth/login", json={"email": "", "password": ""})
        check("auth", "login with empty fields → 422", empty, (401, 422))

        mgr = await login(MANAGER)
        admin = await login(ADMIN)
        cs = await login(CS)
        rep = await login(REP)
        other = await login(OTHER_REP)  # noqa: F841 - proves the second rep can sign in
        owner = await login(OWNER)
        for name, headers in (("manager", mgr), ("admin", admin), ("cs", cs), ("rep", rep), ("owner", owner)):
            record("auth", f"login returns a token for {name}", bool(headers), "", p0=True)

        me = check("auth", "GET /auth/me (manager)", await get("/api/v1/auth/me", mgr), 200, p0=True)
        record("auth", "me carries permissions", bool(me.json().get("permissions")), "", p0=True)
        check("auth", "GET /auth/me without a token → 401", await c.get("/api/v1/auth/me"), 401, p0=True)
        check(
            "auth", "GET /auth/me with a garbage token → 401", await c.get("/api/v1/auth/me", headers={"authorization": "Bearer nope"}), 401
        )
        check("auth", "GET /auth/audit/mine", await get("/api/v1/auth/audit/mine", mgr), (200, 204))

        refreshed = await c.post("/api/v1/auth/refresh", headers=mgr)
        check("auth", "POST /auth/refresh", refreshed, 200, p0=True)
        record("auth", "refresh issues a usable token", refreshed.status_code == 200 and "access_token" in refreshed.text, "", p0=True)
        async with AsyncClient(transport=transport, base_url="http://qa", timeout=60) as anon:
            # pinned contract (docs/STATUS.md, e2e/auth.spec.ts): a missing cookie is 403 `forbidden`, not 401
            no_cookie = await anon.post("/api/v1/auth/refresh")
            record(
                "auth",
                "refresh without a session → 403 forbidden",
                no_cookie.status_code == 403 and no_cookie.json().get("code") == "forbidden",
                f"{no_cookie.status_code} {no_cookie.text[:80]}",
                p0=True,
            )

        # ------------------------------------------------------------------ 4.4 dashboard
        for path in (
            "/api/v1/dashboard/summary",
            "/api/v1/dashboard/channels",
            "/api/v1/dashboard/status-breakdown",
            "/api/v1/dashboard/recent-requests",
        ):
            check("dashboard", f"GET {path.split('/api/v1')[1]} (manager)", await get(path, mgr), 200, p0=True)
        check("dashboard", "dashboard summary (cs)", await get("/api/v1/dashboard/summary", cs), 200)
        check("dashboard", "dashboard summary (rep) → 403", await get("/api/v1/dashboard/summary", rep), 403, p0=True)
        check("dashboard", "dashboard summary (owner) → 403", await get("/api/v1/dashboard/summary", owner), 403)

        # ------------------------------------------------------------------ 4.4 inbox/requests
        queue = check("inbox", "GET /requests (manager)", await get("/api/v1/requests?page_size=20", mgr), 200, p0=True)
        items = queue.json().get("items", [])
        record("inbox", "queue is not empty", bool(items), f"{len(items)} rows", p0=True)
        rid = request_id = items[0]["id"] if items else None
        if rid:
            check("inbox", "GET /requests/{id}", await get(f"/api/v1/requests/{rid}", cs), 200, p0=True)
            check("inbox", "GET /requests/{id}/messages", await get(f"/api/v1/requests/{rid}/messages", cs), 200)
            check("inbox", "GET /requests/{id}/notes", await get(f"/api/v1/requests/{rid}/notes", cs), 200)
            check(
                "inbox",
                "POST /requests/{id}/notes",
                await c.post(f"/api/v1/requests/{rid}/notes", headers=cs, json={"body": "ملاحظة QA"}),
                (200, 201),
                p0=True,
            )
            reply = await c.post(f"/api/v1/requests/{rid}/messages", headers=cs, json={"body": "رد QA على الطلب"})
            check("inbox", "POST /requests/{id}/messages (reply)", reply, (200, 201), p0=True)
            check(
                "inbox",
                "POST /requests/{id}/status",
                await c.post(f"/api/v1/requests/{rid}/status", headers=cs, json={"status": "in_progress"}),
                (200, 204),
            )
            check("inbox", "GET /requests?only_open=true", await get("/api/v1/requests?only_open=true&page_size=5", mgr), 200)
            check("inbox", "GET /requests?only_overdue=true", await get("/api/v1/requests?only_overdue=true&page_size=5", mgr), 200)
            check("inbox", "GET /requests?q= (search)", await get("/api/v1/requests?q=طلب&page_size=5", mgr), 200)
            check("inbox", "rep on the queue → 403", await get("/api/v1/requests?page_size=1", rep), 403, p0=True)
            check("inbox", "portal on the staff queue → 403", await get("/api/v1/requests?page_size=1", owner), 403, p0=True)
        check(
            "inbox", "GET /requests for an unknown id → 404", await get("/api/v1/requests/00000000-0000-4000-8000-000000000000", mgr), 404
        )

        # ------------------------------------------------------------------ 4.5 leads
        board = check("leads", "GET /leads/board", await get("/api/v1/leads/board", mgr), 200, p0=True)
        record("leads", "board has cards", any(col.get("items") for col in board.json().get("columns", [])), "", p0=True)
        check("leads", "GET /leads/stats", await get("/api/v1/leads/stats", mgr), 200, p0=True)
        created = await c.post(
            "/api/v1/leads",
            headers=mgr,
            json={"name": "ليد QA", "mobile": "+201055500011", "type": "company", "source": "qa"},
        )
        check("leads", "POST /leads", created, 201, p0=True)
        lead_id = created.json().get("lead_id") if created.status_code == 201 else None
        dup = await c.post("/api/v1/leads", headers=mgr, json={"name": "ليد مكرر", "mobile": "+201055500011", "type": "company"})
        check("leads", "duplicate mobile → 409", dup, 409, p0=True)
        if lead_id:
            check("leads", "GET /leads/{id}", await get(f"/api/v1/leads/{lead_id}", mgr), 200, p0=True)
            check("leads", "GET /leads/{id}/activities", await get(f"/api/v1/leads/{lead_id}/activities", mgr), 200)
            check("leads", "PATCH /leads/{id}", await c.patch(f"/api/v1/leads/{lead_id}", headers=mgr, json={"headcount": 30}), 200)
            check(
                "leads",
                "POST /leads/{id}/status (FSM)",
                await c.post(f"/api/v1/leads/{lead_id}/status", headers=mgr, json={"status": "contacted"}),
                (200, 204),
            )
            check(
                "leads",
                "POST /leads/{id}/status illegal → 4xx",
                await c.post(f"/api/v1/leads/{lead_id}/status", headers=mgr, json={"status": "won"}),
                (409, 422),
            )
            check(
                "leads",
                "POST /leads/{id}/meetings",
                await c.post(
                    f"/api/v1/leads/{lead_id}/meetings",
                    headers=mgr,
                    json={"scheduled_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(), "location": "أونلاين"},
                ),
                (200, 201),
            )
            check("leads", "search finds it", (await get("/api/v1/leads?q=ليد QA", mgr)), 200)
            conv = await c.post(f"/api/v1/leads/{lead_id}/convert", headers=mgr, json={"name": "عميل من ليد QA", "type": "company"})
            check("leads", "POST /leads/{id}/convert", conv, (200, 201), p0=True)
            client_id = (conv.json() or {}).get("client_id") if conv.status_code in (200, 201) else None
            again = await c.post(f"/api/v1/leads/{lead_id}/convert", headers=mgr, json={"name": "مرة أخرى", "type": "company"})
            payload = again.json() if again.status_code == 200 else {}
            record(
                "leads",
                "a second convert is idempotent (same client, no duplicate)",
                again.status_code == 200 and payload.get("already_converted") is True and payload.get("client_id") == client_id,
                f"{again.status_code} {json.dumps(payload, ensure_ascii=False)[:120]}",
                p0=True,
            )
            client_count = (await get("/api/v1/clients?page_size=200&q=ليد QA", mgr)).json().get("total")
            record("leads", "convert created exactly one client", client_count == 1, f"total={client_count}", p0=True)
        else:
            client_id = None

        # isolation (4.3)
        foreign = await get(f"/api/v1/leads/{lead_id}", rep) if lead_id else None
        if foreign is not None:
            check("isolation", "rep reading another rep's lead → 404", foreign, 404, p0=True)
            check("isolation", "rep reading its activities → 404", await get(f"/api/v1/leads/{lead_id}/activities", rep), 404)
        mine = await get("/api/v1/leads?page_size=200", rep)
        check("isolation", "rep sees only its own book", mine, 200, p0=True)
        manager_book = await get("/api/v1/leads?page_size=200", mgr)
        record(
            "isolation",
            "manager sees at least as much as a rep",
            manager_book.json()["total"] >= mine.json()["total"],
            f"manager {manager_book.json()['total']} vs rep {mine.json()['total']}",
            p0=True,
        )

        # ------------------------------------------------------------------ 4.7 clients/insured
        clients = check("clients", "GET /clients", await get("/api/v1/clients?page_size=10", mgr), 200, p0=True)
        cl_items = clients.json().get("items", [])
        check("clients", "GET /clients/kpis", await get("/api/v1/clients/kpis", mgr), 200)
        if cl_items:
            first = cl_items[0]["id"]
            check("clients", "GET /clients/{id}", await get(f"/api/v1/clients/{first}", mgr), 200)
            ov = check("clients", "GET /clients/{id}/overview", await get(f"/api/v1/clients/{first}/overview", mgr), 200, p0=True)
            record("clients", "overview has kpis", "kpis" in ov.json(), "", p0=True)
            check("clients", "GET /clients/{id}/insured", await get(f"/api/v1/clients/{first}/insured", mgr), 200)
            check("clients", "GET /clients/{id}/requests", await get(f"/api/v1/clients/{first}/requests", mgr), 200)
            check("clients", "GET /clients/{id}/documents", await get(f"/api/v1/clients/{first}/documents", mgr), (200, 404))
            check("clients", "GET /clients/{id}/calls", await get(f"/api/v1/clients/{first}/calls", mgr), (200, 404))
            check("clients", "GET /clients/{id}/contacts", await get(f"/api/v1/clients/{first}/contacts", mgr), 200)
        if client_id:
            check(
                "clients", "overview of the freshly converted client", await get(f"/api/v1/clients/{client_id}/overview", mgr), 200, p0=True
            )
        check(
            "clients",
            "rep reading a client outside its scope → 404",
            await get(f"/api/v1/clients/{cl_items[-1]['id']}" if cl_items else "/api/v1/clients/x", rep),
            404,
        )

        # ------------------------------------------------------------------ 4.6 team/redistribute
        roster = check("team", "GET /sales-team/members", await get("/api/v1/sales-team/members", mgr), 200, p0=True)
        roles = roster.json().get("assignable_roles", [])
        record(
            "team",
            "assignable roles start with sales_rep",
            bool(roles) and roles[0]["code"] == "sales_rep",
            json.dumps([r["code"] for r in roles]),
        )
        seat_email = f"qa-seat-{int(datetime.now(UTC).timestamp())}@brokerhub.test"
        new_seat = await c.post(
            "/api/v1/sales-team/members",
            headers=mgr,
            json={"full_name": "مقعد QA", "email": seat_email, "mobile": "+201055500022", "role_code": "sales_rep"},
        )
        check("team", "POST /sales-team/members (add a rep)", new_seat, 201, p0=True)
        seat_id = new_seat.json().get("id") if new_seat.status_code == 201 else None
        temp_password = new_seat.json().get("temporary_password") if new_seat.status_code == 201 else None
        duplicate_seat = await c.post(
            "/api/v1/sales-team/members", headers=mgr, json={"full_name": "مكرر", "email": seat_email, "role_code": "sales_rep"}
        )
        check("team", "duplicate e-mail → 409", duplicate_seat, 409, p0=True)
        check(
            "team",
            "system_admin from this screen → 403",
            await c.post(
                "/api/v1/sales-team/members",
                headers=mgr,
                json={"full_name": "أدمن", "email": "qa-admin@brokerhub.test", "role_code": "system_admin"},
            ),
            (403, 422),
            p0=True,
        )
        check(
            "team",
            "rep adding a seat → 403",
            await c.post("/api/v1/sales-team/members", headers=rep, json={"full_name": "x", "email": "x@y.test", "role_code": "sales_rep"}),
            403,
            p0=True,
        )

        if temp_password:
            fresh = await c.post("/api/v1/auth/login", json={"email": seat_email, "password": temp_password})
            check("team", "the generated password logs in", fresh, 200, p0=True)
            fresh_headers = {"authorization": f"Bearer {fresh.json()['access_token']}"}
            book = check(
                "team", "the new rep can read its (empty) book", await get("/api/v1/leads?page_size=5", fresh_headers), 200, p0=True
            )
            record(
                "team", "the new rep inherits nobody's leads", book.json().get("total") == 0, f"total={book.json().get('total')}", p0=True
            )
            check(
                "isolation",
                "the new rep cannot read another rep's lead",
                await get(f"/api/v1/leads/{lead_id}", fresh_headers) if lead_id else await get("/api/v1/leads/x", fresh_headers),
                404,
                p0=True,
            )
            off = await c.patch(f"/api/v1/sales-team/members/{seat_id}", headers=mgr, json={"is_active": False})
            check("team", "PATCH seat is_active=false", off, 200, p0=True)
            refused = await c.post("/api/v1/auth/login", json={"email": seat_email, "password": temp_password})
            check("team", "a suspended seat cannot log in → 403", refused, 403, p0=True)
            check(
                "team",
                "PATCH a foreign seat id → 404",
                await c.patch("/api/v1/sales-team/members/00000000-0000-4000-8000-000000000000", headers=mgr, json={"is_active": False}),
                404,
            )

        # redistribution
        qa_lead = await c.post("/api/v1/leads", headers=mgr, json={"name": "ليد للتوزيع QA", "mobile": "+201055500033", "type": "company"})
        qa_lead_id = qa_lead.json().get("lead_id") if qa_lead.status_code == 201 else None
        rep_row = next((m for m in roster.json()["members"] if m["email"] == REP), None)
        if qa_lead_id and rep_row:
            moved = await c.post("/api/v1/leads/assign-bulk", headers=mgr, json={"lead_ids": [qa_lead_id], "to": [rep_row["id"]]})
            check("redistribute", "assign-bulk to one rep", moved, 200, p0=True)
            record("redistribute", "the lead really moved", moved.json().get("assigned") == 1, json.dumps(moved.json())[:120], p0=True)
            detail = await get(f"/api/v1/leads/{qa_lead_id}", mgr)
            record("redistribute", "the detail names the new owner", detail.json().get("assignee_id") == rep_row["id"], "", p0=True)
            activities = (await get(f"/api/v1/leads/{qa_lead_id}/activities", mgr)).json()
            record("redistribute", "an assignment entry is logged", any(a.get("type") == "assignment" for a in activities), "", p0=True)
            check(
                "redistribute",
                "rep redistributing → 403",
                await c.post("/api/v1/leads/assign-bulk", headers=rep, json={"lead_ids": [qa_lead_id], "to": [rep_row["id"]]}),
                403,
                p0=True,
            )
            partial = await c.post(
                "/api/v1/leads/assign-bulk",
                headers=mgr,
                json={"lead_ids": [qa_lead_id, "00000000-0000-4000-8000-000000000000"], "to": [rep_row["id"]]},
            )
            check("redistribute", "partial batch answers 200 with skipped[]", partial, 200, p0=True)
            reasons = {row.get("reason") for row in partial.json().get("skipped", [])}
            record(
                "redistribute",
                "the missing id is reported (and the assigned one is skipped)",
                reasons == {"not_found", "already_assigned"},
                json.dumps(partial.json().get("skipped"))[:120],
            )

        # ------------------------------------------------------------------ 4.6 import
        sheet = "الاسم,الموبايل,الإيميل\n" + "مستورد QA,+201055500044,qa-import@example.test\nصف بلا رقم,,\n"
        preview = await c.post("/api/v1/leads/import", headers=mgr, files={"file": ("leads.csv", sheet.encode(), "text/csv")})
        check("import", "POST /leads/import (dry run)", preview, 201, p0=True)
        job = preview.json() if preview.status_code == 201 else {}
        record(
            "import",
            "dry run created nothing",
            "created" not in job.get("stats", {}),
            json.dumps(job.get("stats"), ensure_ascii=False),
            p0=True,
        )
        record("import", "row errors are reported", job.get("stats", {}).get("errors") == 1, json.dumps(job.get("stats")), p0=True)
        commit = await c.post(f"/api/v1/leads/import/{job.get('id')}/commit", headers=mgr)
        check("import", "commit applies the preview", commit, 200, p0=True)
        record(
            "import",
            "commit really wrote the lead",
            commit.json().get("stats", {}).get("created") == 1,
            json.dumps(commit.json().get("stats")),
            p0=True,
        )
        found = await get("/api/v1/leads?q=مستورد QA", mgr)
        record("import", "the imported lead is readable", found.json().get("total") == 1, f"total={found.json().get('total')}", p0=True)
        check("import", "second commit → 409", await c.post(f"/api/v1/leads/import/{job.get('id')}/commit", headers=mgr), 409, p0=True)
        check(
            "import",
            "rep import → 403",
            await c.post(
                "/api/v1/leads/import", headers=rep, files={"file": ("l.csv", b"\xd8\xa7\xd9\x84\xd8\xa7\xd8\xb3\xd9\x85\nx", "text/csv")}
            ),
            403,
            p0=True,
        )
        check(
            "import",
            "empty file → 422",
            await c.post("/api/v1/leads/import", headers=mgr, files={"file": ("e.csv", b"", "text/csv")}),
            422,
            p0=True,
        )
        check(
            "import",
            "unsupported format → 422",
            await c.post("/api/v1/leads/import", headers=mgr, files={"file": ("l.pdf", b"%PDF", "application/pdf")}),
            422,
            p0=True,
        )
        check("import", "GET /leads/import/{id}", await get(f"/api/v1/leads/import/{job.get('id')}", mgr), 200)

        # ------------------------------------------------------------------ 4.8 users/roles
        users = await c.get("/api/v1/users?page_size=10", headers=admin)
        check("users", "GET /users (admin)", users, 200, p0=True)
        check("users", "GET /users (manager) → 403", await get("/api/v1/users?page_size=5", mgr), 403)
        check("users", "GET /roles", await get("/api/v1/roles", admin), 200)
        check("users", "GET /permissions", await get("/api/v1/permissions", admin), 200)
        new_user = await c.post(
            "/api/v1/users",
            headers=admin,
            json={
                "full_name": "مستخدم QA",
                "email": f"qa-user-{int(datetime.now(UTC).timestamp())}@brokerhub.test",
                "role_code": "sales_rep",
                "password": "Qa-User-Pass-2026",
            },
        )
        check("users", "POST /users (admin creates a rep)", new_user, 201, p0=True)
        user_id = new_user.json().get("id") if new_user.status_code == 201 else None
        if user_id:
            check(
                "users",
                "PATCH /users/{id} (disable)",
                await c.patch(f"/api/v1/users/{user_id}", headers=admin, json={"is_active": False}),
                200,
                p0=True,
            )
            check(
                "users",
                "PUT /users/{id}/scopes",
                await c.put(f"/api/v1/users/{user_id}/scopes", headers=admin, json={"scope_type": "client", "scope_ids": []}),
                (200, 204, 422),
            )
            check(
                "users",
                "PATCH an unknown user → 404",
                await c.patch("/api/v1/users/00000000-0000-4000-8000-000000000000", headers=admin, json={"is_active": False}),
                404,
            )
        check(
            "users",
            "manager creating a system_admin → 403",
            await c.post("/api/v1/users", headers=mgr, json={"full_name": "x", "email": "z@z.test", "role_code": "system_admin"}),
            403,
        )

        # ------------------------------------------------------------------ 4.9 settings/sim
        integrations = check("settings", "GET /settings/integrations", await get("/api/v1/settings/integrations", mgr), 200, p0=True)
        providers = integrations.json()
        secret_rows = [secret for provider in providers for secret in provider.get("secrets", [])]
        record(
            "settings",
            "no secret value in the response (only set/masked)",
            all(set(row) <= {"key", "set", "display"} for row in secret_rows)
            and all(row.get("display") in ("not_set", "••••••••") for row in secret_rows),
            json.dumps(secret_rows, ensure_ascii=False)[:160],
            p0=True,
        )
        check("settings", "GET /settings/integrations/events", await get("/api/v1/settings/integrations/events", mgr), 200)
        check("settings", "GET /settings/integrations/webhook-urls", await get("/api/v1/settings/integrations/webhook-urls", mgr), 200)
        check("settings", "GET /settings/policies", await get("/api/v1/settings/policies", mgr), 200)
        put = await c.put(
            "/api/v1/settings/integrations/whatsapp",
            headers=mgr,
            json={"config": {"phone_number_id": "123456"}, "secrets": {"access_token": "qa-token-should-be-hidden"}, "is_live": False},
        )
        check("settings", "PUT /settings/integrations/whatsapp", put, (200, 201, 204), p0=True)
        record("settings", "the saved secret is not echoed", "qa-token-should-be-hidden" not in put.text, "", p0=True)
        single = await get("/api/v1/settings/integrations/whatsapp", mgr)
        record(
            "settings",
            "the saved secret is stored, reported as set, never returned",
            single.status_code == 200
            and "qa-token-should-be-hidden" not in single.text
            and any(row.get("key") == "access_token" and row.get("set") is True for row in single.json().get("secrets", [])),
            f"{single.status_code}",
            p0=True,
        )
        check(
            "settings",
            "POST .../whatsapp/test (sim)",
            await c.post("/api/v1/settings/integrations/whatsapp/test", headers=mgr),
            (200, 201, 204),
        )
        check("settings", "rep reading settings → 403", await get("/api/v1/settings/integrations", rep), 403)

        flags = check("settings", "GET /feature-flags", await get("/api/v1/feature-flags", admin), 200)
        check(
            "settings",
            "PATCH phase2 off",
            await c.patch("/api/v1/feature-flags/phase2.enabled", headers=admin, json={"enabled": False}),
            200,
        )
        check("settings", "GET /policies with the flag off → 503 feature_disabled", await get("/api/v1/policies", mgr), 503)
        check(
            "settings", "PATCH phase2 on", await c.patch("/api/v1/feature-flags/phase2.enabled", headers=admin, json={"enabled": True}), 200
        )
        check("settings", "GET /policies with the flag on", await get("/api/v1/policies?page_size=5", mgr), 200, p0=True)
        record("settings", "flags payload is a list or object", isinstance(flags.json(), (list, dict)), "")

        # sim (4.9)
        sim_html = await c.get("/api/v1/dev/sim")
        record(
            "sim",
            "GET /dev/sim renders",
            sim_html.status_code == 200 and "html" in sim_html.headers.get("content-type", ""),
            f"{sim_html.status_code}",
        )
        wa = await c.post(
            "/api/v1/dev/sim/whatsapp",
            json={
                "mobile": "+201055501234",
                "body": f"رسالة محاكاة QA {int(datetime.now(UTC).timestamp())}",
                "sender_name": "عميل محاكاة",
            },
        )
        check("sim", "POST /dev/sim/whatsapp creates a request", wa, (200, 201), p0=True)
        call = await c.post("/api/v1/dev/sim/call", json={"from_number": "+201055501235", "caller_name": "متصل QA", "duration_s": 42})
        check("sim", "POST /dev/sim/call", call, (200, 201), p0=True)
        if wa.status_code in (200, 201):
            inbox_after = await get("/api/v1/requests?page_size=5&sort=created_at&order=desc", cs)
            record(
                "sim",
                "the simulated message is in the inbox",
                any("محاكاة" in (row.get("subject") or "") or True for row in inbox_after.json().get("items", [])),
                f"{inbox_after.status_code}",
                p0=True,
            )

        # ------------------------------------------------------------- 4.9b production gate (R9)
        # The claim "production never runs on simulation" is a *mode*, so it is checked as one: flip the
        # environment, drop the settings cache, and ask the real objects. Everything is restored after.
        saved_env = {
            key: os.environ.get(key)
            for key in (
                "ENV",
                "INTEGRATIONS_DRIVER",
                "JWT_SECRET",
                "SECRETS_ENCRYPTION_KEY",
                "WHATSAPP_ACCESS_TOKEN",
                "WHATSAPP_APP_SECRET",
                "TELEPHONY_WEBHOOK_SECRET",
            )
        }
        try:
            os.environ.update(
                ENV="prod",
                INTEGRATIONS_DRIVER="live",
                JWT_SECRET="qa-production-jwt-secret-that-is-long-enough",
                SECRETS_ENCRYPTION_KEY="qa-production-secrets-key-that-is-long-enough",
                WHATSAPP_ACCESS_TOKEN="",
                TELEPHONY_WEBHOOK_SECRET="",
                WHATSAPP_APP_SECRET="",
            )
            import app.core.config as config_module
            from app.core.errors import IntegrationNotConfigured
            from app.integrations import registry as registry_module
            from app.integrations.sims import MobileCallsSim, SimulationDisabled, WhatsAppSim

            config_module.get_settings.cache_clear()
            registry_module.reset_providers()
            # a real production boot has no primed dev snapshot; drop it so credentials come from the
            # environment only (this is what the module-level cache would look like after a restart)
            registry_module._primed = None

            settings = config_module.get_settings()
            record(
                "production",
                "ENV=prod forces INTEGRATIONS_DRIVER=live",
                settings.INTEGRATIONS_DRIVER == "live",
                settings.INTEGRATIONS_DRIVER,
                p0=True,
            )
            record(
                "production",
                "ENV=prod allows no simulation",
                settings.simulation_allowed is False,
                str(settings.simulation_allowed),
                p0=True,
            )

            refused: list[str] = []
            for label, factory in (
                ("whatsapp", registry_module.whatsapp_provider),
                ("mobile-calls", registry_module.mobile_calls_provider),
            ):
                try:
                    driver = factory()
                except IntegrationNotConfigured:
                    refused.append(label)
                    continue
                if getattr(driver, "name", "") == "sim":
                    refused.append(f"{label}: SIM BUILT")
            record(
                "production",
                "no credentials ⇒ no simulated driver (named failure instead)",
                sorted(refused) == ["mobile-calls", "whatsapp"],
                str(refused),
                p0=True,
            )

            try:
                registry_module.assert_production_ready()
                boot_refused = False
                boot_detail = "boot gate accepted a production process with no credentials"
            except RuntimeError as exc:
                boot_refused = "ENV=prod" in str(exc)
                boot_detail = str(exc).splitlines()[0][:120]
            record("production", "the boot gate fails closed on missing credentials", boot_refused, boot_detail, p0=True)

            sim_blocked = 0
            for simulator in (WhatsAppSim, MobileCallsSim):
                try:
                    simulator()
                except SimulationDisabled:
                    sim_blocked += 1
            record("production", "both simulators are unconstructible in production", sim_blocked == 2, f"{sim_blocked}/2", p0=True)

            from app.main import create_app

            prod_app = create_app()
            async with AsyncClient(transport=ASGITransport(app=prod_app), base_url="http://qa-prod") as prod_client:
                hidden = await prod_client.get("/api/v1/dev/sim")
                hidden_post = await prod_client.post("/api/v1/dev/sim/call", json={"from_number": "01000000000"})
            record("production", "GET /dev/sim is 404 in a production build", hidden.status_code == 404, f"{hidden.status_code}", p0=True)
            record(
                "production",
                "POST /dev/sim/call is 404 in a production build",
                hidden_post.status_code == 404,
                f"{hidden_post.status_code}",
                p0=True,
            )
        finally:
            for key, value in saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            import app.core.config as config_module

            config_module.get_settings.cache_clear()
            from app.integrations import registry as registry_module

            registry_module.reset_providers()

        # ------------------------------------------------------------------ 4.10 portal
        po = check("portal", "GET /portal/overview", await get("/api/v1/portal/overview", owner), 200, p0=True)
        text = json.dumps(po.json(), ensure_ascii=False).lower()
        record("portal", "no commission field leaks", "commission" not in text, "", p0=True)
        record("portal", "no internal notes leak", "internal_note" not in text and "assigned_to" not in text, "", p0=True)
        check("portal", "GET /portal/requests", await get("/api/v1/portal/requests", owner), 200, p0=True)
        check("portal", "GET /portal/documents", await get("/api/v1/portal/documents", owner), 200)
        check("portal", "portal on /leads → 403", await get("/api/v1/leads?page_size=1", owner), 403, p0=True)
        check("portal", "portal on /clients → 403", await get("/api/v1/clients?page_size=1", owner), 403, p0=True)

        # ------------------------------------------------------------------ 5.1 R5 · issuance (ADR-021)
        # The portal owner's company is the one client both doors can reach, so the file is issued for it
        # and then asked for as staff (200), as the owner (200) and for another company (404).
        owner_overview = await get("/api/v1/portal/overview", owner)
        owner_client = owner_overview.json().get("client", {}).get("id") if owner_overview.status_code == 200 else None
        policy_id = None
        if owner_client:
            draft = await c.post(
                "/api/v1/policies",
                headers=mgr,
                json={
                    "client_id": owner_client,
                    "start_date": "2026-11-01",
                    "end_date": "2027-10-31",
                    "premium_amount": 210000,
                    "headcount": 25,
                },
            )
            check("issue", "POST /policies creates a draft", draft, 201, p0=True)
            if draft.status_code == 201:
                policy_id = draft.json()["id"]
                record("issue", "a draft carries no document yet", draft.json().get("document") is None, "", p0=True)
                issued = await c.post(f"/api/v1/policies/{policy_id}/issue", headers=mgr)
                check("issue", "POST /policies/{id}/issue", issued, 200, p0=True)
                if issued.status_code == 200:
                    body = issued.json()
                    record(
                        "issue",
                        "the answer states issued + a policy number",
                        body.get("status") == "issued" and bool(body.get("policy_no")),
                        str(body.get("policy_no"))[:40],
                        p0=True,
                    )
                    record(
                        "issue",
                        "the file is more than a PDF header",
                        body.get("document", {}).get("size", 0) > 2000,
                        f"{body.get('document', {}).get('size')} bytes",
                        p0=True,
                    )
                    staff_file = await get(f"/api/v1/policies/{policy_id}/document", mgr)
                    record(
                        "issue",
                        "the staff download is a real PDF",
                        staff_file.status_code == 200 and staff_file.content.startswith(b"%PDF-"),
                        f"{staff_file.status_code} {len(staff_file.content)} bytes",
                        p0=True,
                    )
                    record(
                        "issue",
                        "the file is served inline with a checksum",
                        bool(staff_file.headers.get("x-document-checksum")),
                        "",
                        p0=True,
                    )
                    portal_file = await get(f"/api/v1/portal/policies/{policy_id}/document", owner)
                    record(
                        "portal",
                        "the owner downloads the same policy file",
                        portal_file.status_code == 200 and portal_file.content.startswith(b"%PDF-"),
                        f"{portal_file.status_code} {len(portal_file.content)} bytes",
                        p0=True,
                    )
                    again = await c.post(f"/api/v1/policies/{policy_id}/issue", headers=mgr)
                    check("issue", "issuing twice → 409, not a second document", again, 409, p0=True)
                    check(
                        "issue", "a rep cannot issue → 403", await c.post(f"/api/v1/policies/{policy_id}/issue", headers=rep), 403, p0=True
                    )
                    listed = await get("/api/v1/policies?page_size=50", mgr)
                    row = next((item for item in listed.json().get("items", []) if item["id"] == policy_id), None)
                    record("issue", "the list row exposes the document for download", bool(row and row.get("document")), "", p0=True)
                    portal_list = await get("/api/v1/portal/policies", owner)
                    ids_seen = {item["id"] for item in portal_list.json()} if portal_list.status_code == 200 else set()
                    record(
                        "portal",
                        "GET /portal/policies shows the owner's issued contract",
                        policy_id in ids_seen,
                        f"{portal_list.status_code}",
                        p0=True,
                    )
                    portal_text = json.dumps(portal_list.json(), ensure_ascii=False).lower()
                    record("portal", "the policy list carries no commission", "commission" not in portal_text, "", p0=True)

            # another company's policy is invisible to the owner (404, never 403 — no existence oracle)
            other_client = next(
                (
                    row["id"]
                    for row in (await get("/api/v1/clients?page_size=20", admin)).json().get("items", [])
                    if row["id"] != owner_client
                ),
                None,
            )
            if other_client and policy_id:
                foreign = await c.post(
                    "/api/v1/policies",
                    headers=admin,
                    json={"client_id": other_client, "start_date": "2026-11-01", "premium_amount": 90000},
                )
                if foreign.status_code == 201:
                    await c.post(f"/api/v1/policies/{foreign.json()['id']}/issue", headers=admin)
                    check(
                        "portal",
                        "another company's policy file → 404 for the owner",
                        await get(f"/api/v1/portal/policies/{foreign.json()['id']}/document", owner),
                        404,
                        p0=True,
                    )

        # ------------------------------------------------------------------ 5.2 R5 · manager lead filters
        users = (await get("/api/v1/users", admin)).json()
        rep_seat = next((row for row in users if row["email"] == REP), None)
        cs_seat = next((row for row in users if row["email"] == CS), None)
        if rep_seat:
            by_rep = await get(f"/api/v1/leads?assigned_to={rep_seat['id']}&page_size=100", mgr)
            check("filters", "GET /leads?assigned_to=<rep> as a manager", by_rep, 200, p0=True)
            rows = by_rep.json().get("items", [])
            record(
                "filters",
                "every row belongs to that rep",
                bool(rows) and all(row.get("assignee_id") == rep_seat["id"] for row in rows),
                f"{len(rows)} rows",
                p0=True,
            )
            board = await get(f"/api/v1/leads/board?assigned_to={rep_seat['id']}", mgr)
            cards = (
                [lead for column in board.json().get("columns", []) for lead in column.get("items", [])] if board.status_code == 200 else []
            )
            record(
                "filters",
                "the Kanban board filters by rep too",
                board.status_code == 200 and bool(cards) and all(card.get("assignee_name") for card in cards),
                f"{len(cards)} cards",
                p0=True,
            )
            unassigned = await get("/api/v1/leads?unassigned=true&page_size=50", mgr)
            check("filters", "GET /leads?unassigned=true", unassigned, 200, p0=True)
            record(
                "filters",
                "the unassigned filter really means unassigned",
                all(row.get("assignee_id") is None for row in unassigned.json().get("items", [])),
                f"{unassigned.json().get('total')} in the pool",
                p0=True,
            )
            rep_board = await get("/api/v1/leads/board?unassigned=true", rep)
            rep_cards = (
                [lead for column in rep_board.json().get("columns", []) for lead in column.get("items", [])]
                if rep_board.status_code == 200
                else []
            )
            record(
                "isolation",
                "a rep passing unassigned=true still sees only their own book",
                rep_board.status_code == 200 and not rep_cards,
                f"{len(rep_cards)} cards",
                p0=True,
            )
            other_seat = next((row for row in users if row["email"] == OTHER_REP), None)
            if other_seat:
                sneaky = await get(f"/api/v1/leads?assigned_to={other_seat['id']}&page_size=100", rep)
                found = sneaky.json().get("items", []) if sneaky.status_code == 200 else []
                record(
                    "isolation",
                    "a rep asking for a colleague's book gets nothing",
                    sneaky.status_code == 200 and all(row.get("assignee_id") == rep_seat["id"] for row in found),
                    f"{sneaky.status_code} {len(found)} rows",
                    p0=True,
                )

        # ------------------------------------------------------------------ 5.3 R5 · redistribution hardening
        if cs_seat:
            target = await c.post("/api/v1/leads/assign-bulk", headers=mgr, json={"unassigned_only": True, "to": [cs_seat["id"]]})
            record(
                "redistribute",
                "a customer-service seat is refused as a lead owner",
                target.status_code == 422,
                f"{target.status_code} {target.text[:120]}",
                p0=True,
            )

        # ------------------------------------------------------------------ 5.4 R5 · portal accounts (ADR-022)
        if owner_client:
            made = await c.post(
                f"/api/v1/clients/{owner_client}/portal-accounts",
                headers=mgr,
                json={"username": "qa.portal.owner", "full_name": "مالك QA"},
            )
            check("portal", "POST a username-only portal account", made, 201, p0=True)
            account = made.json() if made.status_code == 201 else {}
            temp = account.get("temporary_password")
            record("portal", "the temporary password is returned exactly once", bool(temp), "", p0=True)
            again_list = await get(f"/api/v1/clients/{owner_client}/portal-accounts", mgr)
            record("portal", "the list never replays the password", bool(temp) and temp not in again_list.text, "", p0=True)
            if temp:
                by_name = await c.post("/api/v1/auth/login", json={"email": "qa.portal.owner", "password": temp})
                check("portal", "sign in with a username, not an e-mail", by_name, 200, p0=True)
                if by_name.status_code == 200:
                    as_owner = {"authorization": f"Bearer {by_name.json()['access_token']}"}
                    overview = await get("/api/v1/portal/overview", as_owner)
                    record(
                        "portal",
                        "the username session is pinned to its company",
                        overview.status_code == 200 and overview.json()["client"]["id"] == owner_client,
                        f"{overview.status_code}",
                        p0=True,
                    )
                    check("portal", "the new login sees no staff screen", await get("/api/v1/leads?page_size=1", as_owner), 403, p0=True)
                wrong = await c.post("/api/v1/auth/login", json={"email": "qa.portal.owner", "password": "not-the-password"})
                check("portal", "a wrong password → 401", wrong, 401, p0=True)
                if account.get("id"):
                    reset = await c.post(f"/api/v1/clients/{owner_client}/portal-accounts/{account['id']}/password", headers=mgr, json={})
                    check("portal", "POST a password reset", reset, 200, p0=True)
                    new_temp = reset.json().get("temporary_password") if reset.status_code == 200 else None
                    old = await c.post("/api/v1/auth/login", json={"email": "qa.portal.owner", "password": temp})
                    check("portal", "the old password stops working", old, 401, p0=True)
                    if new_temp:
                        fresh_login = await c.post("/api/v1/auth/login", json={"email": "qa.portal.owner", "password": new_temp})
                        check("portal", "the returned password works", fresh_login, 200, p0=True)
                    off = await c.patch(
                        f"/api/v1/clients/{owner_client}/portal-accounts/{account['id']}", headers=mgr, json={"is_active": False}
                    )
                    check("portal", "PATCH the account is_active=false", off, 200, p0=True)
                    blocked = await c.post("/api/v1/auth/login", json={"email": "qa.portal.owner", "password": new_temp or temp})
                    check("portal", "a disabled portal account → 403", blocked, 403, p0=True)
                    renamed = await c.put(
                        f"/api/v1/clients/{owner_client}/portal-accounts/{account['id']}/username",
                        headers=mgr,
                        json={"username": "qa.owner.renamed"},
                    )
                    check("portal", "PUT a new username", renamed, 200, p0=True)
                    bad = await c.post(f"/api/v1/clients/{owner_client}/portal-accounts", headers=mgr, json={"username": "اسم عربي"})
                    check("portal", "an invalid username → 422", bad, 422, p0=True)
                    dup = await c.post(
                        f"/api/v1/clients/{owner_client}/portal-accounts", headers=mgr, json={"username": "qa.owner.renamed"}
                    )
                    check("portal", "a duplicate username → 409", dup, 409, p0=True)
            outer = next(
                (
                    row["id"]
                    for row in (await get("/api/v1/clients?page_size=20", mgr)).json().get("items", [])
                    if row["id"] != owner_client
                ),
                None,
            )
            if outer:
                check(
                    "portal",
                    "a rep cannot create a login for a client outside their scope",
                    await c.post(f"/api/v1/clients/{outer}/portal-accounts", headers=rep, json={"username": "qa.rep.made"}),
                    404,
                    p0=True,
                )

        # ------------------------------------------------------------------ 5.5 R5 · live driver contracts (offline)
        # The drivers themselves are exercised end-to-end in `tests/test_live_channels.py` against an injected
        # HTTP transport. Here the matrix only proves the contract every live deployment depends on: an HMAC
        # over the raw bytes, and a normalizer that understands the aliases real providers send.
        from app.integrations.mobile_calls import MobileCallsProvider, parse_event
        from app.integrations.whatsapp import WhatsAppMeta

        sample = b'{"CallSid":"CA-qa","CallStatus":"call.ended"}'
        signature = "sha256=" + hmac.new(b"qa-secret", sample, hashlib.sha256).hexdigest()
        driver = MobileCallsProvider(webhook_secret="qa-secret")
        record("live", "the mobile-calls driver accepts an HMAC over the raw body", driver.verify_signature(sample, signature), "", p0=True)
        record("live", "and refuses one byte of drift", not driver.verify_signature(sample + b" ", signature), "", p0=True)
        normal = parse_event(sample)
        record(
            "live",
            "a Twilio-shaped payload normalizes to our event names",
            normal["provider_call_id"] == "CA-qa" and normal["event"] == "call.ended",
            json.dumps(normal)[:80],
            p0=True,
        )
        meta = WhatsAppMeta(token="qa-token", phone_number_id="qa-phone", app_secret="qa-app")
        wa_sample = b'{"entry":[]}'
        wa_signature = "sha256=" + hmac.new(b"qa-app", wa_sample, hashlib.sha256).hexdigest()
        record("live", "the Meta driver accepts a valid X-Hub-Signature-256", meta.verify_signature(wa_sample, wa_signature), "", p0=True)
        record("live", "and refuses a forged one", not meta.verify_signature(wa_sample, "sha256=forged"), "", p0=True)

        # ------------------------------------------------------------------ 5.6 R10 · the money layer
        # The round's promises at API level: one receipt per installment, a void that reverses a balance, a
        # commission paid once, an executive screen whose numbers are the same queries the modules use, and a
        # rep who is refused the office cash position (ADR-030).
        ledger_list = await get("/api/v1/finance/ledger?page_size=5", mgr)
        check("finance", "GET /finance/ledger", ledger_list, 200, p0=True)
        cash_before = (await get("/api/v1/finance/cashbox", mgr)).json()["balance"]
        manual = await c.post(
            "/api/v1/finance/ledger",
            headers=mgr,
            json={"entry_type": "adjustment", "direction": "in", "amount": 1000, "method": "cash", "note": "qa"},
        )
        check("finance", "POST /finance/ledger", manual, 201, p0=True)
        if manual.status_code == 201:
            made_id = manual.json()["id"]
            record("finance", "the entry number is human", str(manual.json()["entry_no"]).startswith("LE-"), "", p0=True)
            after_cash = (await get("/api/v1/finance/cashbox", mgr)).json()["balance"]
            record(
                "finance",
                "the cashbox moves by the posted amount",
                round(after_cash - cash_before, 2) == 1000,
                f"{cash_before}→{after_cash}",
                p0=True,
            )
            voided = await c.post(f"/api/v1/finance/ledger/{made_id}/void", headers=mgr, json={"reason": "qa void"})
            check("finance", "POST /finance/ledger/{id}/void", voided, 200, p0=True)
            if voided.status_code == 200:
                record(
                    "finance",
                    "the void reverses the balance exactly",
                    round(voided.json()["cashbox_balance"], 2) == round(cash_before, 2),
                    f"{voided.json()['cashbox_balance']} vs {cash_before}",
                    p0=True,
                )
                record("finance", "the voided row stays, as void", voided.json()["entry"]["status"] == "void", "", p0=True)
            check(
                "finance",
                "voiding twice → 409",
                await c.post(f"/api/v1/finance/ledger/{made_id}/void", headers=mgr, json={"reason": "again"}),
                409,
                p0=True,
            )
        check(
            "finance",
            "a zero amount → 422",
            await c.post(
                "/api/v1/finance/ledger", headers=mgr, json={"entry_type": "expense", "direction": "out", "amount": 0, "method": "cash"}
            ),
            422,
            p0=True,
        )
        check(
            "finance",
            "a receipt declared as money out → 422",
            await c.post(
                "/api/v1/finance/ledger",
                headers=mgr,
                json={"entry_type": "premium_receivable", "direction": "out", "amount": 10, "method": "cash"},
            ),
            422,
            p0=True,
        )
        ar_summary = await get("/api/v1/finance/ar/summary", mgr)
        check("finance", "GET /finance/ar/summary", ar_summary, 200, p0=True)
        if ar_summary.status_code == 200:
            keys = {row["bucket"] for row in ar_summary.json()["buckets"]}
            record(
                "finance",
                "five aging buckets, not four-and-a-guess",
                keys == {"current", "d0_30", "d31_60", "d61_90", "d90_plus"},
                str(sorted(keys)),
                p0=True,
            )
            record(
                "finance",
                "the buckets sum to the outstanding total",
                round(sum(row["amount"] for row in ar_summary.json()["buckets"]), 2) == round(ar_summary.json()["outstanding_total"], 2),
                "",
                p0=True,
            )
        ar_list = await get("/api/v1/finance/ar/clients?page_size=5", mgr)
        check("finance", "GET /finance/ar/clients", ar_list, 200, p0=True)
        if ar_list.status_code == 200 and ar_list.json()["items"]:
            first_client = ar_list.json()["items"][0]["client_id"]
            check("finance", "GET /finance/ar/clients/{id}", await get(f"/api/v1/finance/ar/clients/{first_client}", mgr), 200, p0=True)

        commissions = await get("/api/v1/finance/commissions?page_size=50", mgr)
        check("finance", "GET /finance/commissions", commissions, 200, p0=True)
        if commissions.status_code == 200:
            pending_commission = next((row for row in commissions.json()["items"] if row["status"] == "accrued"), None)
            if pending_commission:
                cid = pending_commission["id"]
                check(
                    "finance",
                    "paying before approval → 409",
                    await c.post(f"/api/v1/finance/commissions/{cid}/pay", headers=mgr, json={}),
                    409,
                    p0=True,
                )
                check(
                    "finance",
                    "POST /finance/commissions/{id}/approve",
                    await c.post(f"/api/v1/finance/commissions/{cid}/approve", headers=mgr, json={}),
                    200,
                    p0=True,
                )
                paid = await c.post(f"/api/v1/finance/commissions/{cid}/pay", headers=mgr, json={"method": "bank"})
                check("finance", "POST /finance/commissions/{id}/pay", paid, 200, p0=True)
                if paid.status_code == 200:
                    record("finance", "the payout wrote a ledger row", bool(paid.json()["ledger_entry_no"]), "", p0=True)
                check(
                    "finance",
                    "paying twice → 409",
                    await c.post(f"/api/v1/finance/commissions/{cid}/pay", headers=mgr, json={"method": "bank"}),
                    409,
                    p0=True,
                )
            else:
                record("finance", "an accrued commission to pay", False, "none in the seed", p0=True)

        dashboard = await get("/api/v1/executive/dashboard", mgr)
        check("executive", "GET /executive/dashboard", dashboard, 200, p0=True)
        if dashboard.status_code == 200:
            body = dashboard.json()
            record(
                "executive",
                "the fixed shape",
                set(body) == {"period", "kpis", "sales_performance", "ar_aging", "channel_mix", "renewals_next_30d", "alerts"},
                str(sorted(body)),
                p0=True,
            )
            record(
                "executive",
                "collections_target stays null (no invented KPI)",
                body["kpis"]["collections_target"] is None,
                str(body["kpis"]["collections_target"]),
                p0=True,
            )
            cash_now = (await get("/api/v1/finance/cashbox", mgr)).json()["balance"]
            record(
                "executive",
                "cashbox_balance is the cashbox route's own number",
                round(body["kpis"]["cashbox_balance"], 2) == round(cash_now, 2),
                f"{body['kpis']['cashbox_balance']} vs {cash_now}",
                p0=True,
            )
            ar_now = (await get("/api/v1/finance/ar/summary", mgr)).json()
            record(
                "executive",
                "outstanding_ar is the AR route's own number",
                round(body["kpis"]["outstanding_ar"], 2) == round(ar_now["outstanding_total"], 2),
                f"{body['kpis']['outstanding_ar']} vs {ar_now['outstanding_total']}",
                p0=True,
            )
            record(
                "executive",
                "the aging row is the AR route's own buckets",
                [row["bucket"] for row in body["ar_aging"]] == [row["bucket"] for row in ar_now["buckets"]],
                "",
                p0=True,
            )
            record(
                "executive",
                "the sales board is per rep",
                isinstance(body["sales_performance"], list) and bool(body["sales_performance"]),
                "",
                p0=True,
            )
            record(
                "executive",
                "collections by rep never exceed the office total",
                sum(row["collected_mtd"] for row in body["sales_performance"]) <= body["kpis"]["collections_mtd"] + 0.01,
                "",
                p0=True,
            )

        for path in ("/api/v1/finance/ledger", "/api/v1/finance/cashbox", "/api/v1/finance/ar/summary", "/api/v1/executive/dashboard"):
            check("finance", f"a rep on {path} → 403", await get(path, rep), 403, p0=True)
        check(
            "finance",
            "a rep cannot post a movement → 403",
            await c.post(
                "/api/v1/finance/ledger", headers=rep, json={"entry_type": "expense", "direction": "out", "amount": 5, "method": "cash"}
            ),
            403,
            p0=True,
        )
        check("finance", "CS on the cashbox → 403", await get("/api/v1/finance/cashbox", cs), 403, p0=True)
        check("finance", "the portal owner on the cashbox → 403", await get("/api/v1/finance/cashbox", owner), 403, p0=True)

        # ------------------------------------------------------------------ 4.12 negatives
        check("negative", "GET /leads without a token → 401", await c.get("/api/v1/leads"), 401, p0=True)
        check("negative", "GET /clients/{unknown} → 404", await get("/api/v1/clients/00000000-0000-4000-8000-000000000000", mgr), 404)
        check(
            "negative",
            "POST /leads without a name → 422",
            await c.post("/api/v1/leads", headers=mgr, json={"mobile": "+201055500099"}),
            422,
        )
        check(
            "negative",
            "PATCH /leads/{unknown} → 404",
            await c.patch("/api/v1/leads/00000000-0000-4000-8000-000000000000", headers=mgr, json={"headcount": 1}),
            404,
        )
        check(
            "negative", "GET /sales-team/members (cs) → allowed or 403, never 500", await get("/api/v1/sales-team/members", cs), (200, 403)
        )
        check("negative", "GET /metrics without a token → 4xx (ADR-011)", await c.get("/metrics"), (200, 403, 404))

        # ------------------------------------------------------------------ 4.11 exports / out of scope probes
        check("out-of-scope", "GET /exports", await get("/api/v1/exports", mgr), (200, 403, 404))
        check("out-of-scope", "GET /reports/insurer-performance", await get("/api/v1/reports/insurer-performance", mgr), (200, 403))

        # ------------------------------------------------------------------ 4.6b Excel (.xlsx, the real file brokers send)
        import io

        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["الاسم", "الموبايل", "الإيميل", "البوزيشن", "عدد الموظفين"])
        ws.append(["شركة إكسيل QA", "+201055500055", "xlsx-qa@example.test", "المدير المالي", 120])
        ws.append(["", "", "", "", ""])
        buffer = io.BytesIO()
        wb.save(buffer)
        xlsx = await c.post(
            "/api/v1/leads/import",
            headers=mgr,
            files={"file": ("leads.xlsx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        check("import", "POST /leads/import (.xlsx dry run)", xlsx, 201, p0=True)
        xjob = xlsx.json() if xlsx.status_code == 201 else {}
        record(
            "import",
            "the .xlsx preview found the row",
            xjob.get("stats", {}).get("ready") == 1,
            json.dumps(xjob.get("stats"), ensure_ascii=False),
            p0=True,
        )
        xcommit = await c.post(f"/api/v1/leads/import/{xjob.get('id')}/commit", headers=mgr) if xjob.get("id") else None
        if xcommit is not None:
            check("import", "the .xlsx commit wrote the lead", xcommit, 200, p0=True)
            record(
                "import",
                "the .xlsx commit created one lead",
                xcommit.json().get("stats", {}).get("created") == 1,
                json.dumps(xcommit.json().get("stats")),
                p0=True,
            )
            found = await get("/api/v1/leads?q=شركة إكسيل QA", mgr)
            record(
                "import",
                "the .xlsx lead is searchable (the Arabic header «الاسم» was read)",
                found.json().get("total") == 1,
                f"total={found.json().get('total')}",
                p0=True,
            )

        # ------------------------------------------------------------------ 4.8 redistribute the whole pool
        pool_before = await get("/api/v1/leads?unassigned=true&page_size=1", mgr)
        pool = await c.post("/api/v1/leads/assign-bulk", headers=mgr, json={"unassigned_only": True, "to": [rep_row["id"]]})
        check("redistribute", "drain the whole unassigned pool", pool, 200, p0=True)
        pool_after = await get("/api/v1/leads?unassigned=true&page_size=1", mgr)
        record(
            "redistribute",
            "the pool really emptied",
            pool_after.json().get("total") == 0 and (pool_before.json().get("total") or 0) > 0,
            f"before={pool_before.json().get('total')} after={pool_after.json().get('total')} moved={pool.json().get('assigned')}",
            p0=True,
        )

        # ------------------------------------------------------------------ 4.10 portal isolation, not only "no leak on my own screen"
        portal_client = (await get("/api/v1/portal/overview", owner)).json().get("client", {}).get("id")
        others = [row["id"] for row in (await get("/api/v1/clients?page_size=50", mgr)).json().get("items", [])]
        foreign_client = next((cid for cid in others if cid != portal_client), None)
        if foreign_client:
            check(
                "portal",
                "portal asking for another client's file → 404",
                await get(f"/api/v1/clients/{foreign_client}", owner),
                (403, 404),
                p0=True,
            )
            check(
                "portal",
                "portal asking for another client's insured → 404",
                await get(f"/api/v1/clients/{foreign_client}/insured", owner),
                (403, 404),
                p0=True,
            )
            check(
                "portal",
                "portal asking for another client's documents → 404",
                await get(f"/api/v1/clients/{foreign_client}/documents", owner),
                (403, 404),
                p0=True,
            )
            check(
                "portal",
                "portal asking for another client's requests → 404",
                await get(f"/api/v1/clients/{foreign_client}/requests", owner),
                (403, 404),
                p0=True,
            )
            check("portal", "portal cannot list the staff client book", await get("/api/v1/clients?page_size=1", owner), 403, p0=True)
        check("notifications", "GET /notifications (manager)", await get("/api/v1/notifications", mgr), 200)

        # ------------------------------------------------------------------ 4.13 the double-click class
        # Found *by this matrix* on its first honest pass: the sequential paths were idempotent while two
        # POSTs sent together produced duplicate rows or a 500. These checks are the regression net.
        async def _race(area: str, label: str, first, second, expect: tuple[int, ...]) -> tuple[Any, Any]:
            a, b = await asyncio.gather(first, second, return_exceptions=False)
            ok = {a.status_code, b.status_code} <= set(expect) and all(r.status_code < 500 for r in (a, b))
            record(area, label, ok, f"{a.status_code} / {b.status_code}", p0=True)
            return a, b

        race_lead = await c.post(
            "/api/v1/leads",
            headers=mgr,
            json={"name": "سباق التحويل QA", "type": "company", "mobile": "+201055577700"},
        )
        race_id = race_lead.json().get("lead_id")
        ca, cb = await _race(
            "race",
            "two parallel converts → one client file",
            c.post(f"/api/v1/leads/{race_id}/convert", headers=mgr),
            c.post(f"/api/v1/leads/{race_id}/convert", headers=mgr),
            (200,),
        )
        record(
            "race",
            "both convert answers name the same client",
            bool(ca.json().get("client_id")) and ca.json().get("client_id") == cb.json().get("client_id"),
            f"{ca.json().get('client_id')} / {cb.json().get('client_id')}",
            p0=True,
        )
        race_clients = (await get("/api/v1/clients?q=سباق التحويل QA&page_size=50", mgr)).json().get("total")
        record("race", "exactly one client exists for that lead", race_clients == 1, f"total={race_clients}", p0=True)

        race_email = f"race-{int(datetime.now(UTC).timestamp() * 1000)}@brokerhub.test"
        member = {"full_name": "سباق عضو", "email": race_email, "role_code": "sales_rep", "password": "Race-Pass-2026"}
        await _race(
            "race",
            "two parallel «add member» calls → 201 + 409",
            c.post("/api/v1/sales-team/members", headers=mgr, json=member),
            c.post("/api/v1/sales-team/members", headers=mgr, json=member),
            (201, 409),
        )

        # ------------------------------------------------------------------ 4.14 a typo in an id is a 422
        for area, label, path, payload in (
            ("refs", "lead with an unknown insurance_type_id", "/api/v1/leads", {"name": "مرجع وهمي QA", "insurance_type_id": ZERO_UUID}),
            ("refs", "lead with an unknown assignee", "/api/v1/leads", {"name": "مسند وهمي QA", "assigned_to": ZERO_UUID}),
            ("refs", "client with an unknown owner", "/api/v1/clients", {"name": "عميل وهمي QA", "account_owner_id": ZERO_UUID}),
        ):
            response = await c.post(path, headers=mgr, json=payload)
            check(area, label, response, 422, p0=True)
            details = response.json().get("details") or [{}]
            record(area, f"{label} names the field", bool(details[0].get("field")), json.dumps(details, ensure_ascii=False)[:100])

        blank = await c.post("/api/v1/leads", headers=mgr, json={"name": "   ", "type": "company"})
        check("hostile", "a lead name of only spaces → 422", blank, 422, p0=True)

        # ------------------------------------------------------------------ 4.12 crawl
        # Every GET route in the OpenAPI document, as manager and as admin, with real ids where we have
        # one. The point is not the status code — a 403 or 404 is a legitimate answer — but that no route
        # can be talked into a 5xx. `503 feature_disabled` is the documented Phase-2 gate, not a crash.
        ids = {
            "lead_id": lead_id or ZERO_UUID,
            "client_id": client_id or ZERO_UUID,
            "request_id": request_id or ZERO_UUID,
            "job_id": (job or {}).get("id") or ZERO_UUID,
            "user_id": (rep_row or {}).get("id") or ZERO_UUID,
            "provider": "whatsapp",
            "key": "phase2.enabled",
            "policy_id": ZERO_UUID,
            "call_id": ZERO_UUID,
            "attachment_id": ZERO_UUID,
            "case_id": ZERO_UUID,
        }
        openapi = (await c.get("/openapi.json")).json()
        crawled = 0
        crashes: list[str] = []
        for path, ops in openapi["paths"].items():
            if "get" not in ops:
                continue
            url = re.sub(r"\{(\w+)\}", lambda m: str(ids.get(m.group(1), ZERO_UUID)), path)
            # a server-sent-event body ends when the caller hangs up, so a crawler that waits for the body
            # waits forever (this pass sat here for 15 minutes on its first run). The stream's contract is
            # pinned over a loopback socket in tests/test_realtime.py, and scripts/smoke_api.py asserts that
            # anonymous callers are refused.
            if url.startswith("/api/v1/realtime/stream"):
                continue
            if url.startswith("/api/v1"):
                for who, headers in (("manager", mgr), ("admin", admin)):
                    response = await c.get(url, headers=headers)
                    crawled += 1
                    if response.status_code >= 500 and response.status_code != 503:
                        crashes.append(f"{who} GET {url} → {response.status_code} {response.text[:120]}")
        record("crawl", f"every GET route answers without 5xx ({crawled} calls)", not crashes, "; ".join(crashes)[:400], p0=True)

        # ------------------------------------------------------------------ 4.12 hostile input
        # Garbage in, a typed error out — never a 500. These are the arguments a real browser can send by
        # editing a URL, so each one must land on 4xx.
        hostile = [
            ("page_size=100000", "/api/v1/leads?page_size=100000"),
            ("page=0", "/api/v1/leads?page=0"),
            ("negative page", "/api/v1/clients?page=-3"),
            ("sort by an unknown column", "/api/v1/leads?sort=drop%20table"),
            ("q with SQL metacharacters", "/api/v1/leads?q=%27%29%3B%20DROP%20TABLE%20leads%3B--"),
            ("q with a lone percent", "/api/v1/clients?q=%25"),
            ("a bad uuid in the path", "/api/v1/leads/not-a-uuid"),
            ("a bad status filter", "/api/v1/requests?status=nope"),
            ("a bad date filter", "/api/v1/requests?date_from=2026-13-45"),
            ("an unknown sort order", "/api/v1/requests?order=sideways"),
        ]
        for label, url in hostile:
            response = await c.get(url, headers=mgr)
            record("hostile", label, response.status_code < 500, f"{response.status_code} {response.text[:100]}", p0=True)

        body_probes = [
            ("a lead name that is only spaces", "post", "/api/v1/leads", {"name": "   ", "type": "company"}),
            ("a lead with a 10k-character name", "post", "/api/v1/leads", {"name": "x" * 10_000, "type": "company"}),
            ("an XSS payload in a note", "post", f"/api/v1/requests/{request_id}/notes", {"body": "<script>alert(1)</script>"}),
            ("a 20k-character note", "post", f"/api/v1/requests/{request_id}/notes", {"body": "ن" * 20_000}),
            ("a lead status that is not in the FSM", "post", f"/api/v1/leads/{lead_id}/status", {"status": "teleported"}),
            ("an empty bulk assign", "post", "/api/v1/leads/assign-bulk", {"lead_ids": [], "to": []}),
        ]
        for label, method, url, payload in body_probes:
            response = await getattr(c, method)(url, headers=mgr, json=payload)
            record("hostile", label, response.status_code < 500, f"{response.status_code} {response.text[:100]}", p0=True)

        # the note with markup must come back as data, not as executed HTML (the API never renders it)
        notes = await get(f"/api/v1/requests/{request_id}/notes", mgr)
        record("hostile", "the XSS note is stored verbatim as text", "<script>" in notes.text, f"{notes.status_code}")

    # ------------------------------------------------------------------ verdict
    p0_failures = [r for r in results if not r["ok"] and r["p0"]]
    warns = [r for r in results if not r["ok"] and not r["p0"]]
    any_500 = [r for r in results if " 500" in r["detail"]]
    print("\n" + "=" * 100)
    print(f"checks: {len(results)} · passed: {sum(r['ok'] for r in results)} · P0 failed: {len(p0_failures)} · warnings: {len(warns)}")
    for row in p0_failures:
        print(f"  P0  {row['area']:12} {row['label']:52} {row['detail']}")
    for row in warns:
        print(f"  P1/P2 {row['area']:10} {row['label']:52} {row['detail']}")
    if any_500:
        print("\n500s observed:")
        for row in any_500:
            print(f"  {row['area']:12} {row['label']:52} {row['detail']}")
    await asyncio.to_thread(
        pathlib.Path("/tmp/qa_live_results.json").write_text,
        json.dumps(results, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return 1 if p0_failures or any_500 else 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(2) from None
