"""End-to-end smoke over the seeded app (in-process ASGI, no server needed).

Prints each endpoint's response shape and asserts the basics. Useful in CI without Postgres
and while building the UI: `python scripts/smoke_api.py [--keys]`.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import pathlib
import sys
import tempfile
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DB = pathlib.Path(tempfile.gettempdir()) / "bh_smoke.db"
#: this file's contract is "a seeded throwaway database", so it owns `DATABASE_URL` rather than inheriting
#: one: a CI job that exports a PostgreSQL DSN (for pytest) would otherwise send the smoke pass at an empty
#: production-shaped database, where `create_dev_schema` and the seed are not what the run is about.
#: `SMOKE_DATABASE_URL` is the way to point it somewhere else on purpose.
os.environ["DATABASE_URL"] = os.environ.get("SMOKE_DATABASE_URL") or f"sqlite+aiosqlite:///{DB}"
os.environ["ENV"] = "dev"
os.environ.setdefault("INTEGRATIONS_DRIVER", "sim")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "1")
os.environ.setdefault("STORAGE_DIR", str(pathlib.Path(tempfile.gettempdir()) / "bh_media"))
os.environ.setdefault("MEDIA_ROOT", str(pathlib.Path(tempfile.gettempdir()) / "bh_media"))


def _prepare_db() -> None:
    """Schema + seed in ONE loop: the engine is cached on the module, and a second `asyncio.run` would reuse
    connections created by the first loop — which is how a smoke pass dies with "Event loop is closed"."""
    import asyncio

    from app.core.db import create_dev_schema, get_sessionmaker

    async def _build() -> None:
        from app.seed import seed

        await create_dev_schema()
        async with get_sessionmaker()() as session:
            await seed(session)

    asyncio.run(_build())


# ---------------------------------------------------------------------------------------------
# exhaustive pass: every route in the document must answer, not just the curated list above
# ---------------------------------------------------------------------------------------------

#: routes a smoke run cannot exercise honestly, each with the reason a reviewer can check
EXEMPT: dict[tuple[str, str], str] = {
    ("GET", "/api/v1/media/{key}"): "a signed one-shot URL, exercised for real in the block above",
    ("GET", "/api/v1/portal/attachments/{attachment_id}"): "the id belongs to the portal's own attachment list — tests/test_portal.py",
    # an SSE body ends when the client leaves, and `TestClient` waits for the end — the first version of this
    # pass sat on this route for fifteen minutes. The stream's real contract (first frame, auth refusal,
    # replay after `?since=`) is pinned over a loopback socket in tests/test_realtime.py; the curated block
    # below asserts the refusal and the exhaustive pass still counts the route as documented.
    ("GET", "/api/v1/realtime/stream"): "a stream that never ends — tests/test_realtime.py drives it over a loopback socket",
}

#: who may see what, so a 403 never reads as a broken endpoint
ADMIN_ONLY_PREFIXES = ("/api/v1/users", "/api/v1/roles", "/api/v1/permissions", "/api/v1/settings", "/api/v1/imports")

#: routes whose contract is a query string rather than a path (the Meta handshake)
QUERY: dict[tuple[str, str], str] = {
    ("GET", "/api/v1/webhooks/whatsapp"): "?hub.mode=subscribe&hub.verify_token=smoke-token&hub.challenge=4242",
}


def _first_id(client: Any, headers: dict[str, str], path: str, key: str = "id") -> str:
    """An id taken from the list endpoint, so a detail route is asked for something that really exists.

    Pages come back in three shapes in this API (`[...]`, `{items: [...]}`, `{page: {items: [...]}}`) —
    all three are read here rather than assuming one, because the smoke pass is exactly the place a
    response-shape surprise should surface.
    """
    response = client.get(path, headers=headers)
    if response.status_code != 200:
        return ""
    body = response.json()
    for _ in range(3):
        if isinstance(body, list):
            items = body
            break
        if not isinstance(body, dict):
            return ""
        if isinstance(body.get("items"), list):
            items = body["items"]
            break
        body = body.get("page", {})
    else:
        return ""
    if items and isinstance(items[0], dict):
        return str(items[0].get(key, ""))
    return ""


def _items(client: Any, headers: dict[str, str], path: str) -> list[dict[str, Any]]:
    response = client.get(path, headers=headers)
    if response.status_code != 200:
        return []
    body = response.json()
    for _ in range(3):
        if isinstance(body, list):
            return [row for row in body if isinstance(row, dict)]
        if isinstance(body, dict) and isinstance(body.get("items"), list):
            return [row for row in body["items"] if isinstance(row, dict)]
        body = body.get("page", {}) if isinstance(body, dict) else {}
    return []


def _params_for(client: Any, headers: dict[str, str], known: dict[str, str]) -> dict[str, str]:
    """Parameter name → a value that exists. The names are unique across this API except `key`, which is
    why the feature-flag key is looked up by path in `_fill` instead."""
    table: dict[str, str] = {
        "client_id": _first_id(client, headers, "/api/v1/clients"),
        "lead_id": _first_id(client, headers, "/api/v1/leads"),
        "request_id": _first_id(client, headers, "/api/v1/requests"),
        "policy_id": _first_id(client, headers, "/api/v1/policies"),
        "case_id": _first_id(client, headers, "/api/v1/issuing-cases"),
        "user_id": _first_id(client, headers, "/api/v1/users"),
        "role_id": _first_id(client, headers, "/api/v1/roles"),
        "notification_id": _first_id(client, headers, "/api/v1/notifications"),
        "provider": "whatsapp",
        "period": "2020-01",
        "key": "phase2.enabled",
        "kind": "insurer-performance",
    }
    request_id = table["request_id"]
    if request_id:
        table["call_id"] = _first_id(client, headers, f"/api/v1/requests/{request_id}/calls")
    # the period-close DELETE needs a month that is actually closed (Score-8): close a throwaway one
    client.post("/api/v1/finance/period-closes", headers=headers, json={"period": "2020-01", "note": "smoke"})
    # an attachment id must belong to the same request (download-url checks the pairing), and the seed
    # ships no attachments — upload one through the real staff endpoint, which also smokes the upload
    for row in _items(client, headers, "/api/v1/requests?page_size=25"):
        found = _first_id(client, headers, f"/api/v1/requests/{row['id']}/attachments")
        if found:
            table["attachment_id"] = found
            table["request_id"] = str(row["id"])
            break
    if "attachment_id" not in table and table.get("request_id"):
        rid = table["request_id"]
        up = client.post(
            f"/api/v1/requests/{rid}/attachments",
            headers=headers,
            files={"file": ("smoke-fixture.pdf", b"%PDF-1.4 smoke fixture", "application/pdf")},
        )
        if up.status_code == 201:
            table["attachment_id"] = str(up.json()["id"])
    # an installment lives under one policy, so it cannot come from a global list — take it from the plan of
    # the first policy the seed knows (the curated block overwrites `policy_id` with a fresh issued one)
    if table["policy_id"]:
        table["installment_id"] = _first_id(client, headers, f"/api/v1/policies/{table['policy_id']}/installments")
    # the money layer's two instance ids (R10): a commission to approve/pay and a ledger row to void
    table["commission_id"] = _first_id(client, headers, "/api/v1/finance/commissions")
    table["entry_id"] = _first_id(client, headers, "/api/v1/finance/ledger")
    for row in _items(client, headers, "/api/v1/clients"):
        insured = _first_id(client, headers, f"/api/v1/clients/{row['id']}/insured")
        if insured:
            table["insured_id"] = insured
            break
    table.update({name: value for name, value in known.items() if value})
    return {name: value for name, value in table.items() if value}


def _fill(path: str, params: dict[str, str]) -> str:
    out = path
    for name, value in params.items():
        out = out.replace("{" + name + "}", value)
    return "" if "{" in out else out


def exhaustive_pass(
    client: Any, headers: dict[str, str], admin_headers: dict[str, str], portal_headers: dict[str, str]
) -> tuple[list[str], str]:
    """Ask every documented route for its documented behaviour.

    The rules are blunt on purpose: a GET whose instance the seed can name must answer 200 (a 403 means the
    permission gate is wrong, a 404 means the route or its serializer is), and any 5xx is a failure. Writes
    are sent an empty body, so they answer with validation — the point is that the router, the auth
    dependency and the serializer all work together, not that a business rule is satisfied (pytest does that).
    """
    document = create_app_document()
    known: dict[str, str] = dict(SMOKE_IDS)
    params = _params_for(client, admin_headers, known)
    # A global table cannot answer these, because the same parameter name means a different row in a
    # different table: `job_id` is an insured-import job for `/import-jobs/{job_id}` and a lead-import job
    # for `/leads/import/{job_id}`, and `user_id` is any staff account for `/users/{user_id}` but only an
    # office seat for `/sales-team/members/{user_id}`. Each gets the instance the curated block created.
    per_path = {
        "/api/v1/leads/import/{job_id}": ("job_id", "lead_job_id"),
        "/api/v1/leads/import/{job_id}/commit": ("job_id", "lead_job_id"),
        "/api/v1/sales-team/members/{user_id}": ("user_id", "team_member_id"),
    }
    overrides: dict[str, dict[str, str]] = {path: {name: known[key]} for path, (name, key) in per_path.items() if known.get(key)}
    # the portal-account routes need *both* an id that is a portal login and the client it is pinned to —
    # a staff `user_id` from `/users` is a real uuid that belongs to no portal account, so it answers 404
    for suffix in ("password", ""):
        path = f"/api/v1/clients/{{client_id}}/portal-accounts/{{user_id}}{'/' + suffix if suffix else ''}"
        if known.get("portal_user_id") and known.get("portal_client_id"):
            overrides[path] = {"user_id": known["portal_user_id"], "client_id": known["portal_client_id"]}
    if known.get("portal_user_id") and known.get("portal_client_id"):
        overrides["/api/v1/clients/{client_id}/portal-accounts/{user_id}/username"] = {
            "user_id": known["portal_user_id"],
            "client_id": known["portal_client_id"],
        }
    failures: list[str] = []
    exercised = 0
    for path, operations in sorted(document["paths"].items()):
        for method, _operation in operations.items():
            if (method.upper(), path) in EXEMPT:
                print(f"skip {method.upper():6} {path} — {EXEMPT[(method.upper(), path)]}")
                continue
            client_path = path
            if "{" in path:
                client_path = _fill(path, {**params, **overrides.get(path, {})})
                if not client_path:
                    failures.append(f"{method.upper()} {path}: no instance could be resolved")
                    continue
            if path == "/api/v1/feature-flags/{key}" and method == "patch":
                client_path = client_path.replace("phase2.enabled", "ai.enabled")
            who = (
                portal_headers
                if client_path.startswith("/api/v1/portal/")
                else admin_headers
                if client_path.startswith(ADMIN_ONLY_PREFIXES)
                else headers
            )
            client_path += QUERY.get((method.upper(), path), "")
            body: dict[str, Any] | None = {} if method in ("post", "put", "patch") else None
            response = client.request(method.upper(), client_path, headers=who, json=body)
            exercised += 1
            if method == "get":
                ok = response.status_code == 200
            else:
                # 405 = the document promised a verb the router does not have; 404 = the instance the seed
                # was supposed to name is unreachable; 5xx is never acceptable
                ok = response.status_code < 500 and response.status_code not in (404, 405)
            mark = "ok  " if ok else "FAIL"
            print(f"{mark} {method.upper():6} {client_path} -> {response.status_code}")
            if not ok:
                failures.append(f"{method.upper()} {client_path} -> {response.status_code} {response.text[:160]}")
    total = sum(len(v) for v in document["paths"].values())
    print(f"\nexhaustive pass: {exercised} of {total} documented calls answered")
    return failures, f"{exercised}/{total}"


def create_app_document() -> Any:
    """The app's own OpenAPI document — the list of promises this pass holds it to."""
    from app.main import create_app

    return create_app().openapi()


#: filled in while the curated checks run, so the exhaustive pass asks for instances that exist
SMOKE_IDS: dict[str, str] = {}


def _shape(value: Any, depth: int = 0) -> Any:
    if isinstance(value, dict):
        return {k: _shape(v, depth + 1) for k, v in value.items()} if depth < 2 else "{...}"
    if isinstance(value, list):
        return [_shape(value[0], depth + 1)] if value else []
    return type(value).__name__


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keys", action="store_true", help="print full JSON instead of shapes")
    args = parser.parse_args()

    if DB.exists():
        DB.unlink()
    print(f"smoke database: {os.environ['DATABASE_URL']}")
    _prepare_db()

    from app.main import create_app
    from fastapi.testclient import TestClient

    client = TestClient(create_app())
    failures: list[str] = []

    def check(name: str, status: int, expected: tuple[int, ...] = (200,)) -> Any:
        if status not in expected:
            failures.append(f"{name} -> {status}")
        return None

    def get(path: str, token: str | None = None, label: str | None = None) -> Any:
        headers = {"authorization": f"Bearer {token}"} if token else {}
        response = client.get(path, headers=headers)
        name = label or f"GET {path}"
        if response.status_code not in (200, 201, 204):
            failures.append(f"{name} -> {response.status_code} {response.text[:160]}")
            print(f"FAIL {name}: {response.status_code}")
            return None
        payload = response.json() if response.content else None
        print(f"ok   {name}")
        print(f"     {json.dumps(payload if args.keys else _shape(payload), ensure_ascii=False, default=str)[:600]}")
        return payload

    login = client.post("/api/v1/auth/login", json={"email": "manager@brokerhub.test", "password": "Br0kerHub!2026"})
    if login.status_code != 200:
        print(f"login failed: {login.status_code} {login.text[:200]}")
        return 1
    token = login.json()["access_token"]

    # the change feed: documented, and closed to anonymous callers (see EXEMPT for why it is not read here)
    anonymous = client.get("/api/v1/realtime/stream")
    check("GET /api/v1/realtime/stream (anonymous)", anonymous.status_code, (401, 403))
    print(f"ok   GET    /api/v1/realtime/stream (anonymous) -> {anonymous.status_code}")
    revision = get("/api/v1/realtime/revision", token)
    if revision is not None and "revision" not in revision:
        failures.append("GET /api/v1/realtime/revision: no revision in the body")

    for path in [
        "/api/v1/auth/me",
        "/api/v1/dashboard/summary",
        "/api/v1/dashboard/channels",
        "/api/v1/dashboard/status-breakdown",
        "/api/v1/dashboard/recent-requests",
        "/api/v1/requests",
        "/api/v1/leads",
        "/api/v1/leads/board",
        "/api/v1/leads/stats",
        "/api/v1/sales/team",
        "/api/v1/clients",
        "/api/v1/clients/kpis",
        "/api/v1/insurers",
        "/api/v1/insurance-types",
        "/api/v1/lost-reasons",
        "/api/v1/sla-policies",
        "/api/v1/feature-flags",
        "/api/v1/notifications",
    ]:
        get(path, token)

    clients = client.get("/api/v1/clients", headers={"authorization": f"Bearer {token}"}).json()
    if clients.get("items"):
        client_id = clients["items"][0]["id"]
        get(f"/api/v1/clients/{client_id}/overview", token)
        get(f"/api/v1/clients/{client_id}/insured", token)
        get(f"/api/v1/clients/{client_id}/requests", token)

    leads = client.get("/api/v1/leads", headers={"authorization": f"Bearer {token}"}).json()
    if leads.get("items"):
        get(f"/api/v1/leads/{leads['items'][0]['id']}/activities", token)

    requests_ = client.get("/api/v1/requests", headers={"authorization": f"Bearer {token}"}).json()
    if requests_.get("items"):
        request_id = requests_["items"][0]["id"]
        get(f"/api/v1/requests/{request_id}/messages", token)
        get(f"/api/v1/requests/{request_id}/calls", token)
        get(f"/api/v1/requests/{request_id}/notes", token)

    portal_login = client.post("/api/v1/auth/login", json={"email": "owner.mantiq@company.test", "password": "Br0kerHub!2026"})
    if portal_login.status_code == 200:
        portal_token = portal_login.json()["access_token"]
        get("/api/v1/portal/overview", portal_token)
        get("/api/v1/portal/documents", portal_token)
        get("/api/v1/portal/requests", portal_token)

    # Phase-2 endpoints are flag-gated: 503 while `phase2.enabled` is off, then enabled for shape checks
    for path in ["/api/v1/policies", "/api/v1/commissions", "/api/v1/issuing-cases", "/api/v1/reports/insurer-performance"]:
        gated = client.get(path, headers={"authorization": f"Bearer {token}"})
        if gated.status_code == 503 and gated.json().get("code") == "feature_disabled":
            print(f"ok   GET {path} is feature-flagged off (503 feature_disabled)")
        else:
            failures.append(f"{path} should be flag-gated, got {gated.status_code}")

    admin_token = client.post("/api/v1/auth/login", json={"email": "admin@brokerhub.test", "password": "Br0kerHub!2026"}).json()[
        "access_token"
    ]
    admin = {"authorization": f"Bearer {admin_token}"}
    toggled = client.patch("/api/v1/feature-flags/phase2.enabled", json={"enabled": True}, headers=admin)
    if toggled.status_code == 200:
        for path in ["/api/v1/policies", "/api/v1/commissions", "/api/v1/issuing-cases", "/api/v1/reports/insurer-performance"]:
            get(path, admin_token)
        client.patch("/api/v1/feature-flags/phase2.enabled", json={"enabled": False}, headers=admin)
    else:
        failures.append(f"PATCH /api/v1/feature-flags/phase2.enabled -> {toggled.status_code}")
    for path in ["/api/v1/users", "/api/v1/roles", "/api/v1/permissions"]:
        get(path, admin_token)

    # Simulator (spec §8.4): inbound WhatsApp + inbound call
    wa = client.post("/api/v1/dev/sim/whatsapp", json={"mobile": "01001234567", "body": "عايز أزيد عدد الموظفين"})
    print(f"{'ok  ' if wa.status_code == 200 else 'FAIL'} POST /api/v1/dev/sim/whatsapp -> {wa.status_code}")
    print(f"     {wa.text[:280]}")
    call = client.post("/api/v1/dev/sim/call", json={"from_number": "01001234567", "duration_s": 95})
    print(f"{'ok  ' if call.status_code == 200 else 'FAIL'} POST /api/v1/dev/sim/call -> {call.status_code}")
    print(f"     {call.text[:280]}")
    if wa.status_code != 200:
        failures.append(f"sim whatsapp -> {wa.status_code}")
    if call.status_code != 200:
        failures.append(f"sim call -> {call.status_code}")

    # the two simulator events must land in the inbox as real requests (spec §8.4)
    if wa.status_code == 200 and call.status_code == 200:
        recent = client.get("/api/v1/requests?page_size=10&sort=created_at&order=desc", headers=admin).json()
        # the list is sorted newest-first, so take the first hit per channel (the simulator rows)
        by_channel: dict[str, dict[str, Any]] = {}
        for item in recent.get("items", []):
            if item["channel"] in {"whatsapp", "call"} and item["channel"] not in by_channel:
                by_channel[item["channel"]] = item
        if {"whatsapp", "call"} <= set(by_channel):
            print("ok   simulator → inbox: " + ", ".join(f"#{item['number']} [{item['channel']}]" for item in by_channel.values()))
        else:
            failures.append(f"simulator events missing from inbox: {sorted(by_channel)}")

        call_item = by_channel.get("call")
        if call_item:
            calls = client.get(f"/api/v1/requests/{call_item['id']}/calls", headers=admin).json()
            recording = next((row for row in calls if row.get("has_recording")), None)
            if recording is None:
                failures.append("the simulator call row carries no recording (has_recording=false)")
            else:
                url = client.get(f"/api/v1/calls/{recording['id']}/recording-url", headers=admin).json()
                print(f"ok   signed recording url issued (expires_at={url.get('expires_at')})")
                media = client.get(url["url"])
                if media.status_code == 200:
                    print(f"ok   signed media fetches ({len(media.content)} bytes)")
                else:
                    failures.append(f"signed media fetch -> {media.status_code}")
                # a rep without calls.listen must not be able to listen (spec §6.4)
                rep_token = client.post(
                    "/api/v1/auth/login", json={"email": "sara.elsayed@brokerhub.test", "password": "Br0kerHub!2026"}
                ).json()["access_token"]
                denied = client.get(f"/api/v1/calls/{recording['id']}/recording-url", headers={"authorization": f"Bearer {rep_token}"})
                if denied.status_code == 403:
                    print("ok   recording url without calls.listen -> 403")
                else:
                    failures.append(f"recording gate -> {denied.status_code}")

    # — instances the exhaustive pass cannot invent: an import job, an insured row, a provider with a token —
    if clients.get("items"):
        from openpyxl import Workbook

        client_id = str(clients["items"][0]["id"])
        book = Workbook()
        book.active.append(["name", "mobile", "department"])
        book.active.append(["سمير فؤاد", "01001234567", "المشتريات"])
        sheet = io.BytesIO()
        book.save(sheet)
        uploaded = client.post(
            f"/api/v1/clients/{client_id}/insured/import",
            headers=admin,
            files={"file": ("smoke.xlsx", sheet.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        if uploaded.status_code == 201:
            SMOKE_IDS["job_id"] = str(uploaded.json()["id"])
            print(f"ok   POST /api/v1/clients/{client_id}/insured/import -> dry run {SMOKE_IDS['job_id']}")
            committed = client.post(f"/api/v1/import-jobs/{SMOKE_IDS['job_id']}/commit", headers=admin, json={"confirm": True})
            print(
                f"{'ok  ' if committed.status_code == 200 else 'FAIL'} POST /api/v1/import-jobs/{{job_id}}/commit -> {committed.status_code}"
            )
            if committed.status_code != 200:
                failures.append(f"import commit -> {committed.status_code} {committed.text[:160]}")
            else:
                SMOKE_IDS["insured_id"] = _first_id(client, admin, f"/api/v1/clients/{client_id}/insured")
        else:
            failures.append(f"insured import -> {uploaded.status_code} {uploaded.text[:160]}")

    # — the two instances this round adds: a lead-import job of its own, and a seat in the office roster —
    lead_csv = "الاسم,الموبايل,الشركة,البوزيشن\nسمير المستورد,01007654321,شركة دخان,المدير المالي\n".encode()
    lead_upload = client.post("/api/v1/leads/import", headers=admin, files={"file": ("smoke-leads.csv", lead_csv, "text/csv")})
    if lead_upload.status_code == 201:
        preview = lead_upload.json()
        SMOKE_IDS["lead_job_id"] = str(preview["id"])
        print(f"ok   POST /api/v1/leads/import -> dry run {preview['stats']['ready']} ready, saved nothing")
        applied = client.post(f"/api/v1/leads/import/{SMOKE_IDS['lead_job_id']}/commit", headers=admin, json={})
        if applied.status_code == 200 and applied.json()["stats"]["created"] == 1:
            print("ok   POST /api/v1/leads/import/{job_id}/commit -> the row is in the leads table")
        else:
            failures.append(f"lead import commit -> {applied.status_code} {applied.text[:160]}")
    else:
        failures.append(f"lead import -> {lead_upload.status_code} {lead_upload.text[:160]}")

    # the roster answers `{members, assignable_roles}`, so the id is read from the key it lives under
    roster = client.get("/api/v1/sales-team/members", headers=admin).json()
    seat = str(roster["members"][0]["id"]) if roster.get("members") else ""
    offered = {row["code"] for row in roster.get("assignable_roles", [])}
    if seat and offered == {"sales_rep", "sales_manager", "customer_service", "account_manager"}:
        SMOKE_IDS["team_member_id"] = seat
        print("ok   GET /api/v1/sales-team/members -> a seat to move, and the roles the tab may assign")
    else:
        failures.append(f"GET /api/v1/sales-team/members -> seat={bool(seat)} assignable={sorted(offered)}")

    # the settings row is what the webhook handshake reads its token from (ADR-016 §1), so it is configured here
    configured = client.put(
        "/api/v1/settings/integrations/whatsapp",
        headers=admin,
        json={"config": {"phone_number_id": "smoke-phone"}, "secrets": {"verify_token": "smoke-token"}},
    )
    if configured.status_code == 200:
        print("ok   PUT /api/v1/settings/integrations/whatsapp stores config + a masked secret")
        secret_rows = [row for row in configured.json()["secrets"] if row["key"] == "verify_token"]
        if not secret_rows or secret_rows[0]["display"] != "••••••••" or "smoke-token" in configured.text:
            failures.append(f"the settings GET leaked or mislabelled a secret: {configured.text[:160]}")
    else:
        failures.append(f"PUT /settings/integrations/whatsapp -> {configured.status_code} {configured.text[:160]}")
    handshake = client.get("/api/v1/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=smoke-token&hub.challenge=4242")
    if handshake.status_code == 200 and "4242" in handshake.text:
        print("ok   Meta handshake echoes the challenge with the token from the settings row")
    else:
        failures.append(f"handshake -> {handshake.status_code} {handshake.text[:160]}")
    refused = client.get("/api/v1/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=4242")
    if refused.status_code == 403:
        events = client.get("/api/v1/settings/integrations/events", headers=admin).json()
        if any(row["event_type"] == "verification" and row["status"] == "rejected" for row in events):
            print("ok   a refused handshake is written to webhook_events, not only returned")
        else:
            failures.append("the refused handshake never reached webhook_events")
    else:
        failures.append(f"handshake with a wrong token -> {refused.status_code} (403 expected)")

    # every route is asked, not only the ones this file curated by hand — with Phase 2 open so the gated
    # half of the surface is answered for its real shape instead of a 503
    flags = client.get("/api/v1/feature-flags", headers=admin).json()
    keys = {row["key"] for row in flags} if isinstance(flags, list) else set(flags.get("items", []))
    opened: list[str] = []
    for flag in ("phase2.enabled", "ai.enabled"):
        if client.patch(f"/api/v1/feature-flags/{flag}", json={"enabled": True}, headers=admin).status_code == 200:
            opened.append(flag)
        else:
            failures.append(f"PATCH /api/v1/feature-flags/{flag} -> the exhaustive pass cannot open the gate")
    if "sim" not in os.environ.get("INTEGRATIONS_DRIVER", "sim"):  # pragma: no cover - the file pins sim above
        failures.append("the smoke run must not talk to a real provider")
    # — issuance (ADR-021): the file is the deliverable, so the smoke run downloads it through both doors —
    # The seeded policies carry no document (they were booked before issuance existed), and a GET document
    # for a policy with no file is honestly a 404. So one draft is created for the portal owner's company and
    # issued here; the exhaustive pass then asks for *its* document, for the staff route and the portal route.
    if portal_login.status_code == 200:
        owner_headers = {"authorization": f"Bearer {portal_token}"}
        overview = client.get("/api/v1/portal/overview", headers=owner_headers)
        owner_client = overview.json()["client"]["id"] if overview.status_code == 200 else ""
        if owner_client:
            draft = client.post(
                "/api/v1/policies",
                headers=admin,
                json={
                    "client_id": owner_client,
                    "start_date": "2026-11-01",
                    "end_date": "2027-10-31",
                    "premium_amount": 240000,
                    "headcount": 30,
                },
            )
            if draft.status_code == 201:
                policy_id = str(draft.json()["id"])
                issued = client.post(f"/api/v1/policies/{policy_id}/issue", headers=admin)
                if issued.status_code == 200 and issued.json().get("document", {}).get("size", 0) > 1000:
                    print(
                        f"ok   POST /api/v1/policies/{{id}}/issue -> {issued.json()['policy_no']} ({issued.json()['document']['size']} bytes)"
                    )
                    file = client.get(f"/api/v1/policies/{policy_id}/document", headers=admin)
                    if file.status_code == 200 and file.content.startswith(b"%PDF-"):
                        print(f"ok   GET /api/v1/policies/{{id}}/document -> a real PDF ({len(file.content)} bytes)")
                        SMOKE_IDS["policy_id"] = policy_id
                    else:
                        failures.append(f"policy document -> {file.status_code} {file.content[:40]!r}")
                    portal_file = client.get(f"/api/v1/portal/policies/{policy_id}/document", headers=owner_headers)
                    if portal_file.status_code == 200 and portal_file.content.startswith(b"%PDF-"):
                        print("ok   GET /api/v1/portal/policies/{id}/document -> the same file, one company only")
                    else:
                        failures.append(f"portal policy document -> {portal_file.status_code}")
                    # — the money half (R7): the schedule the issue produced, then one real payment —
                    plan = client.get(f"/api/v1/policies/{policy_id}/installments", headers=admin)
                    if plan.status_code == 200 and plan.json().get("items"):
                        rows = plan.json()["items"]
                        unpaid = [row for row in rows if row["status"] != "paid"]
                        print(f"ok   GET /api/v1/policies/{{id}}/installments -> {len(rows)} rows, {len(unpaid)} unpaid")
                        if unpaid:
                            paid = client.post(f"/api/v1/policies/{policy_id}/installments/{unpaid[0]['id']}/pay", headers=admin, json={})
                            paid_status = (paid.json().get("installment") or {}).get("status") if paid.status_code == 200 else None
                            if paid_status == "paid":
                                print("ok   POST /api/v1/policies/{id}/installments/{iid}/pay -> paid, once")
                                twice = client.post(
                                    f"/api/v1/policies/{policy_id}/installments/{unpaid[0]['id']}/pay", headers=admin, json={}
                                )
                                if twice.status_code == 409:
                                    print("ok   POST …/pay twice -> 409, not a second receipt")
                                else:
                                    failures.append(f"double pay -> {twice.status_code} (409 expected)")
                            else:
                                failures.append(f"pay -> {paid.status_code} {paid.text[:160]}")
                    else:
                        failures.append(f"installments -> {plan.status_code} {plan.text[:160]}")
                    reissued = client.post(f"/api/v1/policies/{policy_id}/issue", headers=admin)
                    if reissued.status_code == 409:
                        print("ok   POST /api/v1/policies/{id}/issue twice -> 409, not a second document")
                    else:
                        failures.append(f"re-issue -> {reissued.status_code} (409 expected)")
                    portal_list = client.get("/api/v1/portal/policies", headers=owner_headers)
                    if portal_list.status_code == 200 and any(row["id"] == policy_id for row in portal_list.json()):
                        print("ok   GET /api/v1/portal/policies -> the owner sees the issued contract")
                    else:
                        failures.append(f"portal policies -> {portal_list.status_code}")
                else:
                    failures.append(f"issue -> {issued.status_code} {issued.text[:160]}")
            else:
                failures.append(f"draft policy -> {draft.status_code} {draft.text[:160]}")

            # — portal accounts (ADR-022): a username login, created from the client file —
            made = client.post(
                f"/api/v1/clients/{owner_client}/portal-accounts",
                headers=admin,
                json={"username": "smoke.owner", "full_name": "مالك للاختبار"},
            )
            if made.status_code == 201 and made.json().get("temporary_password"):
                temp = made.json()["temporary_password"]
                signin = client.post("/api/v1/auth/login", json={"email": "smoke.owner", "password": temp})
                if signin.status_code == 200:
                    print("ok   POST /auth/login with a username (not an e-mail) -> 200")
                    again = client.get("/api/v1/portal/overview", headers={"authorization": f"Bearer {signin.json()['access_token']}"})
                    if again.status_code == 200 and again.json()["client"]["id"] == owner_client:
                        print("ok   the username session is pinned to the same company")
                    else:
                        failures.append(f"username session overview -> {again.status_code}")
                else:
                    failures.append(f"username login -> {signin.status_code} {signin.text[:160]}")
                leaked = client.get(f"/api/v1/clients/{owner_client}/portal-accounts", headers=admin)
                if "temporary_password" in leaked.text or temp in leaked.text:
                    failures.append("the portal-account list replayed the temporary password")
                else:
                    print("ok   GET /clients/{id}/portal-accounts never replays the password")
                SMOKE_IDS["portal_user_id"] = str(made.json()["id"])
                SMOKE_IDS["portal_client_id"] = str(owner_client)
            else:
                failures.append(f"create portal account -> {made.status_code} {made.text[:160]}")

            # a rep-facing check that the new lead filters narrow instead of widening (§6)
            rep_token_smoke = client.post("/api/v1/auth/login", json={"email": "sara.elsayed@brokerhub.test", "password": "Br0kerHub!2026"})
            if rep_token_smoke.status_code == 200:
                rep_headers = {"authorization": f"Bearer {rep_token_smoke.json()['access_token']}"}
                filtered = client.get("/api/v1/leads/board?unassigned=true", headers=rep_headers)
                rows = (
                    sum(len(column.get("items", [])) for column in filtered.json().get("columns", []))
                    if filtered.status_code == 200
                    else -1
                )
                if filtered.status_code == 200 and rows == 0:
                    print("ok   GET /leads/board?unassigned=true as a rep -> an empty board, not the pool")
                else:
                    failures.append(f"rep unassigned board -> {filtered.status_code} rows={rows}")

    # — the money layer (R10, ADR-030): a receipt, a void that reverses it, a payout paid once, and an
    #   executive screen whose numbers come from the same queries as the modules they summarise —
    ledger = get("/api/v1/finance/ledger?page_size=5", admin_token)
    if ledger is not None:
        receipts = [row for row in ledger["items"] if row["entry_type"] == "premium_receivable"]
        if receipts:
            print(f"ok   GET /api/v1/finance/ledger -> {ledger['total']} rows, newest {ledger['items'][0]['entry_no']}")
            SMOKE_IDS["ledger_entry_id"] = str(receipts[0]["id"])
        else:
            failures.append("GET /finance/ledger -> no premium receipt for the installment paid above")
    cash = get("/api/v1/finance/cashbox", admin_token)
    if cash is not None and not isinstance(cash.get("balance"), (int, float)):
        failures.append("GET /finance/cashbox -> no numeric balance")
    get("/api/v1/finance/ar/summary", admin_token)
    ar_clients_payload = get("/api/v1/finance/ar/clients?page_size=5", admin_token)
    if ar_clients_payload is not None:
        buckets = {row["bucket"] for row in (ar_clients_payload["items"][0]["buckets"] if ar_clients_payload["items"] else [])}
        if ar_clients_payload["items"] and not {"current", "d0_30", "d31_60", "d61_90", "d90_plus"} <= buckets:
            failures.append(f"GET /finance/ar/clients -> aging buckets {sorted(buckets)}")

    # a manual receipt, then the void that takes it back — the cashbox has to move both times
    before_balance = (cash or {}).get("balance")
    manual = client.post(
        "/api/v1/finance/ledger",
        headers=admin,
        json={"entry_type": "adjustment", "direction": "in", "amount": 1234, "method": "cash", "note": "smoke receipt"},
    )
    if manual.status_code == 201:
        entry_id = str(manual.json()["id"])
        SMOKE_IDS["ledger_entry_id"] = entry_id
        moved = client.get("/api/v1/finance/cashbox", headers=admin).json()["balance"]
        if before_balance is not None and round(moved - before_balance, 2) != 1234:
            failures.append(f"cashbox did not move by the posted amount: {before_balance} -> {moved}")
        else:
            print("ok   POST /api/v1/finance/ledger -> the cashbox balance moved by the amount")
        voided = client.post(f"/api/v1/finance/ledger/{entry_id}/void", headers=admin, json={"reason": "smoke void"})
        if voided.status_code == 200 and round(voided.json()["cashbox_balance"], 2) == round(before_balance or 0, 2):
            print("ok   POST /api/v1/finance/ledger/{id}/void -> the balance went back exactly")
        else:
            failures.append(
                f"void -> {voided.status_code} balance={voided.json().get('cashbox_balance') if voided.status_code == 200 else voided.text[:120]}"
            )
        twice = client.post(f"/api/v1/finance/ledger/{entry_id}/void", headers=admin, json={"reason": "again"})
        if twice.status_code != 409:
            failures.append(f"void twice -> {twice.status_code} (409 expected)")
    else:
        failures.append(f"POST /finance/ledger -> {manual.status_code} {manual.text[:160]}")
    for amount in (0, -5):
        refused = client.post(
            "/api/v1/finance/ledger",
            headers=admin,
            json={"entry_type": "expense", "direction": "out", "amount": amount, "method": "cash"},
        )
        if refused.status_code != 422:
            failures.append(f"ledger amount {amount} -> {refused.status_code} (422 expected)")

    commissions = get("/api/v1/finance/commissions?page_size=50", admin_token)
    if commissions is not None:
        pending = next((row for row in commissions["items"] if row["status"] == "accrued"), None)
        if pending:
            approved = client.post(f"/api/v1/finance/commissions/{pending['id']}/approve", headers=admin, json={})
            if approved.status_code == 200:
                paid = client.post(f"/api/v1/finance/commissions/{pending['id']}/pay", headers=admin, json={"method": "bank"})
                if paid.status_code == 200 and paid.json()["ledger_entry_no"]:
                    print(f"ok   POST /finance/commissions/{{id}}/pay -> {paid.json()['ledger_entry_no']}")
                    again = client.post(f"/api/v1/finance/commissions/{pending['id']}/pay", headers=admin, json={"method": "bank"})
                    if again.status_code != 409:
                        failures.append(f"double commission pay -> {again.status_code} (409 expected)")
                else:
                    failures.append(f"commission pay -> {paid.status_code} {paid.text[:160]}")
            else:
                failures.append(f"commission approve -> {approved.status_code} {approved.text[:160]}")
        else:
            failures.append("GET /finance/commissions -> no accrued commission to pay")

    dashboard = get("/api/v1/executive/dashboard", admin_token)
    if dashboard is not None:
        expected = {
            "collections_mtd",
            "collections_target",
            "outstanding_ar",
            "overdue_ar",
            "commissions_accrued",
            "commissions_paid_mtd",
            "open_requests",
            "overdue_requests",
            "new_leads_mtd",
            "converted_leads_mtd",
            "policies_issued_mtd",
            "cashbox_balance",
        }
        missing = expected - set(dashboard.get("kpis", {}))
        if missing:
            failures.append(f"GET /executive/dashboard -> missing kpis {sorted(missing)}")
        elif set(dashboard) != {
            "period",
            "kpis",
            "sales_performance",
            "ar_aging",
            "channel_mix",
            "renewals_next_30d",
            "alerts",
        }:
            failures.append(f"GET /executive/dashboard -> unexpected shape {sorted(dashboard)}")
        elif dashboard["kpis"]["collections_target"] is not None:
            failures.append("GET /executive/dashboard -> collections_target must stay null until targets exist")
        else:
            print("ok   GET /api/v1/executive/dashboard -> the fixed shape, and no invented target")

    # a rep holds no finance permission: the office cash position is refused, not blanked
    rep_cash = (
        client.get("/api/v1/finance/cashbox", headers={"authorization": f"Bearer {rep_token_smoke.json()['access_token']}"})
        if rep_token_smoke.status_code == 200
        else None
    )
    if rep_cash is not None and rep_cash.status_code != 403:
        failures.append(f"rep GET /finance/cashbox -> {rep_cash.status_code} (403 expected)")

    portal = {"authorization": f"Bearer {portal_token}"} if portal_login.status_code == 200 else admin
    smoke_failures, exercised_summary = exhaustive_pass(client, admin, admin, portal)
    failures += smoke_failures
    for flag in opened:
        client.patch(f"/api/v1/feature-flags/{flag}", json={"enabled": False}, headers=admin)
    client.put(
        "/api/v1/settings/integrations/whatsapp", headers=admin, json={"config": {"phone_number_id": ""}, "secrets": {"verify_token": None}}
    )
    print(f"\n(flags opened for the pass: {', '.join(sorted(opened)) or 'none'}; `keys` seen: {len(keys)})")

    print()
    if failures:
        print(f"SMOKE FAILED ({len(failures)}):")
        for line in failures:
            print(f"  - {line}")
        return 1
    print(f"SMOKE OK — {exercised_summary} documented calls answered, exemptions listed with a reason, on {os.environ['DATABASE_URL']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
