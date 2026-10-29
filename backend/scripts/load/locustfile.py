"""R8 — the API load mix, as Locust users that exercise the *product's* real read/write paths.

Why Locust and not k6: this sandbox can reach `pypi.org`, `registry.npmjs.org` and `github.com` but **not**
`release-assets.githubusercontent.com`, which is where k6 binaries live (`curl` → `SSL_ERROR_SYSCALL`).
Locust arrives from pypi as a normal wheel and drives the same HTTP load; §3 of the R8 brief allows it.

The mix is the screen mix, not a random URL list: every task below is a request a real screen makes when a
manager/rep/CS opens the dashboard, the kanban, the inbox or a client file. Response *shapes* are checked
(status code and a couple of key names), because a load test that counts a 200 with an error body as a
success measures nothing.

Run (headless, HTML+CSV out):
    locust -f scripts/load/locustfile.py --headless -u 50 -r 10 -t 3m \
        --host http://127.0.0.1:8020 --csv docs/qa/load/raw/s2-50 --html docs/qa/load/raw/s2-50.html \
        --only-summary

Ramp of S2 is expressed by running the same file at 10/25/50/75/100 users (one run each), which keeps every
step's CSV separate — the report quotes one file per level.
"""

from __future__ import annotations

import os
import random
import time
from typing import Any

from locust import HttpUser, between, events, task

PASSWORD = os.environ.get("LOAD_PASSWORD", "Br0kerHub!2026")
MANAGER = os.environ.get("LOAD_MANAGER", "manager@brokerhub.test")
CS = os.environ.get("LOAD_CS", "cs@brokerhub.test")
REP_COUNT = int(os.environ.get("LOAD_REP_COUNT", "10"))
PORTAL = os.environ.get("LOAD_PORTAL", "owner.mantiq@company.test")

#: The access token lives 15 minutes (`ACCESS_TOKEN_TTL_MIN`). A browser keeps the session alive by calling
#: `POST /auth/refresh` when a request comes back 401 (`src/lib/auth.tsx` → `onUnauthorized`); a load user that
#: logs in once and then runs for 25 minutes does **not** behave like the product, and S6 proved it: at 10:41:37
#: — 15 min into the run — every authenticated call turned into `401 Session expired` for the last 10 minutes.
#: So the user refreshes on the same schedule the SPA does: every `LOAD_TOKEN_REFRESH_S` seconds (12 min,
#: comfortably inside the 15-minute window), and falls back to a fresh login only if the cookie is refused.
TOKEN_REFRESH_S = int(os.environ.get("LOAD_TOKEN_REFRESH_S", "720"))


#: Which users take part. The mix is the default; `write` puts writers in the majority (S5), `read` leaves
#: them out (S2/S6), and a mode simply sets the other classes' weight to zero — same code, no second file.
MODE = os.environ.get("LOAD_MODE", "mixed")
WEIGHTS: dict[str, dict[str, int]] = {
    "mixed": {"manager": 5, "rep": 10, "cs": 5, "portal": 3, "write": 2},
    "write": {"manager": 6, "rep": 0, "cs": 2, "portal": 0, "write": 4},
    "read": {"manager": 5, "rep": 10, "cs": 5, "portal": 3, "write": 0},
}


def _weight(role: str) -> int:
    return WEIGHTS.get(MODE, WEIGHTS["mixed"]).get(role, 0)


def _rep_email() -> str:
    return f"load.rep{random.randint(0, REP_COUNT - 1):02d}@brokerhub.test"


class Brokered(HttpUser):
    """Shared login + the ids every task needs. One login per user, like a real session."""

    abstract = True
    wait_time = between(0.4, 1.4)

    def on_start(self) -> None:
        self.email = self._who()
        self.token = ""
        self.token_at = 0.0
        self.rep_ids: list[str] = []
        self.lead_ids: list[str] = []
        self.request_ids: list[str] = []
        self.client_ids: list[str] = []
        if not self._login():
            return
        self._discover()

    def _who(self) -> str:  # pragma: no cover - overridden
        return MANAGER

    def _login(self) -> bool:
        with self.client.post(
            "/api/v1/auth/login",
            json={"email": self.email, "password": PASSWORD, "remember_me": True},
            name="POST /auth/login",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"login {response.status_code}: {response.text[:120]}")
                return False
            body = response.json()
            if "access_token" not in body:
                response.failure("no access_token in the login body")
                return False
            self.token = body["access_token"]
            self.token_at = time.time()
            response.success()
        return True

    def _refresh(self) -> None:
        """Rotate the access token the way `src/lib/auth.tsx` does; sign in again only if the cookie is gone."""
        with self.client.post("/api/v1/auth/refresh", name="POST /auth/refresh", catch_response=True) as r:
            if r.status_code == 200 and "access_token" in r.text:
                self.token = r.json()["access_token"]
                self.token_at = time.time()
                r.success()
                return
            # a refused refresh means the cookie expired: the screen sends the user back to login, so do we
            r.failure(f"refresh {r.status_code}: {r.text[:120]}")
        self._login()

    def _h(self) -> dict[str, str]:
        """Auth header for the next call — and the one place every task renews its token through.

        Locust calls `TaskSet.wait()`, never `User.wait()`, so a per-task hook does not exist for a plain
        `HttpUser`; every task here builds its headers here instead, which is called immediately before the
        request that needs them — the closest thing to the SPA's `onUnauthorized` renewal.
        """
        if self.token_at and time.time() - self.token_at > TOKEN_REFRESH_S:
            self._refresh()
        return {"Authorization": f"Bearer {self.token}"}

    def _probe(self, path: str) -> Any:
        """A setup read that is allowed to be refused: a rep cannot list `/requests`, and that is not an error.

        Setup traffic is named `setup` so the per-endpoint table of the *load mix* stays honest, and every
        answer counts as success — discovery only needs whatever the role is allowed to see.
        """
        with self.client.get(path, headers=self._h(), name="setup (ids for the user's own screen)", catch_response=True) as response:
            response.success()
            return response.json() if response.status_code == 200 else {}

    def _discover(self) -> None:
        """Ids from the lists the screen would load anyway — no extra login, no fabricated uuid."""
        board = self._probe("/api/v1/leads/board")
        for column in board.get("columns", []) if isinstance(board, dict) else []:
            for lead in column.get("items", [])[:12]:
                self.lead_ids.append(lead["id"])
        requests_page = self._probe("/api/v1/requests?page_size=40&only_open=true")
        for row in requests_page.get("items", []) if isinstance(requests_page, dict) else []:
            self.request_ids.append(row["id"])
        clients_page = self._probe("/api/v1/clients?page_size=40")
        for row in clients_page.get("items", []) if isinstance(clients_page, dict) else []:
            self.client_ids.append(row["id"])
        team = self._probe("/api/v1/sales/team")
        for row in team if isinstance(team, list) else []:
            if row.get("rep_id"):
                self.rep_ids.append(row["rep_id"])

    def _check(self, response: Any, *, key: str | None = None) -> None:
        if response.status_code != 200:
            response.failure(f"{response.status_code}: {response.text[:120]}")
        elif key is not None and key not in response.text[:2000]:
            response.failure(f"200 but no `{key}` in the body")


class ManagerFlow(Brokered):
    """Screen 1 → 3 → 2 → 7: dashboard, kanban with a rep filter, inbox, sales team."""

    weight = _weight("manager")

    def _who(self) -> str:
        return MANAGER

    @task(4)
    def dashboard(self) -> None:
        for path in ("/api/v1/dashboard/summary", "/api/v1/dashboard/recent-requests", "/api/v1/dashboard/channels"):
            with self.client.get(path, headers=self._h(), name=f"GET {path.replace('/api/v1', '')}", catch_response=True) as r:
                self._check(r)

    @task(5)
    def kanban(self) -> None:
        with self.client.get("/api/v1/leads/board", headers=self._h(), name="GET /leads/board", catch_response=True) as r:
            self._check(r, key="columns")
        if self.rep_ids:
            rep = random.choice(self.rep_ids)
            with self.client.get(
                f"/api/v1/leads/board?assigned_to={rep}", headers=self._h(), name="GET /leads/board?assigned_to", catch_response=True
            ) as r:
                self._check(r, key="columns")
        with self.client.get("/api/v1/leads/stats", headers=self._h(), name="GET /leads/stats", catch_response=True) as r:
            self._check(r)
        with self.client.get("/api/v1/leads?page_size=25", headers=self._h(), name="GET /leads", catch_response=True) as r:
            self._check(r, key="items")

    @task(4)
    def inbox(self) -> None:
        with self.client.get(
            "/api/v1/requests?only_open=true&page_size=20", headers=self._h(), name="GET /requests?only_open=true", catch_response=True
        ) as r:
            self._check(r, key="items")
        if self.request_ids:
            req = random.choice(self.request_ids)
            with self.client.get(f"/api/v1/requests/{req}", headers=self._h(), name="GET /requests/{id}", catch_response=True) as r:
                self._check(r)

    @task(2)
    def team(self) -> None:
        with self.client.get("/api/v1/sales/team", headers=self._h(), name="GET /sales/team", catch_response=True) as r:
            self._check(r)

    @task(2)
    def clients(self) -> None:
        with self.client.get("/api/v1/clients?page_size=25", headers=self._h(), name="GET /clients", catch_response=True) as r:
            self._check(r, key="items")
        if self.client_ids:
            cid = random.choice(self.client_ids)
            with self.client.get(
                f"/api/v1/clients/{cid}/overview", headers=self._h(), name="GET /clients/{id}/overview", catch_response=True
            ) as r:
                self._check(r)


class RepFlow(Brokered):
    """Screens 3/6 + inbox: the rep's own board, a lead file, and their queue."""

    weight = _weight("rep")

    def _who(self) -> str:
        return _rep_email()

    @task(6)
    def kanban(self) -> None:
        with self.client.get("/api/v1/leads/board", headers=self._h(), name="GET /leads/board", catch_response=True) as r:
            self._check(r, key="columns")
        with self.client.get("/api/v1/leads?page_size=25", headers=self._h(), name="GET /leads", catch_response=True) as r:
            self._check(r, key="items")

    @task(4)
    def lead_file(self) -> None:
        if not self.lead_ids:
            return
        lead = random.choice(self.lead_ids)
        with self.client.get(f"/api/v1/leads/{lead}", headers=self._h(), name="GET /leads/{id}", catch_response=True) as r:
            if r.status_code == 404:
                r.success()  # a rep asking for a lead outside their book is *supposed* to see nothing
            else:
                self._check(r)
        with self.client.get(
            f"/api/v1/leads/{lead}/activities", headers=self._h(), name="GET /leads/{id}/activities", catch_response=True
        ) as r:
            if r.status_code != 404:
                self._check(r)
            else:
                r.success()

    @task(3)
    def my_pipeline(self) -> None:
        """Not the inbox: `sales_rep` holds no `requests.view`, so `GET /requests` answers 403 by design.

        R8 measured that (`docs/qa/load/r8-load-*.md`, finding F1) and **did not** widen the role to make a
        load script pass — the permission model is a product decision, and the acceptance criterion here is
        about capacity, not about who may see what.
        """
        with self.client.get("/api/v1/leads/stats", headers=self._h(), name="GET /leads/stats", catch_response=True) as r:
            self._check(r)


class CsFlow(Brokered):
    """Screen 2: the service desk's queue and one request at a time."""

    weight = _weight("cs")

    def _who(self) -> str:
        return CS

    @task(6)
    def inbox(self) -> None:
        with self.client.get(
            "/api/v1/requests?only_open=true&page_size=20", headers=self._h(), name="GET /requests?only_open=true", catch_response=True
        ) as r:
            self._check(r, key="items")

    @task(4)
    def request_file(self) -> None:
        if not self.request_ids:
            return
        req = random.choice(self.request_ids)
        with self.client.get(f"/api/v1/requests/{req}", headers=self._h(), name="GET /requests/{id}", catch_response=True) as r:
            self._check(r)
        with self.client.get(
            f"/api/v1/requests/{req}/messages", headers=self._h(), name="GET /requests/{id}/messages", catch_response=True
        ) as r:
            self._check(r)


class WriteFlow(Brokered):
    """S5 — writes: a new lead, a note on a request, a small assignment. 409/422 are answers, not failures."""

    weight = _weight("write")
    wait_time = between(1.0, 2.5)

    def _who(self) -> str:
        return MANAGER

    @task(3)
    def new_lead(self) -> None:
        counter = random.randint(10_000_000, 99_999_999)
        with self.client.post(
            "/api/v1/leads",
            headers=self._h(),
            json={
                "type": "company",
                "name": f"ليد تحميل {counter}",
                "contact_name": "منى تحميل",
                "mobile": f"01{counter}",
                "headcount": 42,
            },
            name="POST /leads",
            catch_response=True,
        ) as r:
            if r.status_code in (201, 200):
                r.success()
            elif r.status_code in (409, 422):
                r.success()  # a duplicate mobile / a validation answer is the product behaving
            else:
                r.failure(f"{r.status_code}: {r.text[:120]}")

    @task(3)
    def note(self) -> None:
        if not self.request_ids:
            return
        req = random.choice(self.request_ids)
        with self.client.post(
            f"/api/v1/requests/{req}/notes",
            headers=self._h(),
            json={"body": "ملاحظة داخلية تحت الحمل"},
            name="POST /requests/{id}/notes",
            catch_response=True,
        ) as r:
            if r.status_code in (200, 201, 404, 409, 422):
                r.success()
            else:
                r.failure(f"{r.status_code}: {r.text[:120]}")

    @task(1)
    def assign(self) -> None:
        if not (self.lead_ids and self.rep_ids):
            return
        lead, rep = random.choice(self.lead_ids), random.choice(self.rep_ids)
        with self.client.post(
            f"/api/v1/leads/{lead}/assign",
            headers=self._h(),
            json={"assigned_to": rep},
            name="POST /leads/{id}/assign",
            catch_response=True,
        ) as r:
            if r.status_code in (200, 201, 403, 404, 409, 422):
                r.success()
            else:
                r.failure(f"{r.status_code}: {r.text[:120]}")


class PortalFlow(Brokered):
    """The client portal: its own login, its own (much smaller) surface."""

    weight = _weight("portal")

    def _who(self) -> str:
        return PORTAL

    @task(6)
    def overview(self) -> None:
        with self.client.get("/api/v1/portal/overview", headers=self._h(), name="GET /portal/overview", catch_response=True) as r:
            self._check(r)
        with self.client.get("/api/v1/portal/policies", headers=self._h(), name="GET /portal/policies", catch_response=True) as r:
            self._check(r)

    @task(4)
    def requests(self) -> None:
        with self.client.get("/api/v1/portal/requests", headers=self._h(), name="GET /portal/requests", catch_response=True) as r:
            self._check(r)


@events.quitting.add_listener
def _fail_the_run_on_5xx(environment: Any, **_kwargs: Any) -> None:
    """A green-looking HTML with a 5xx inside is not a pass: the run's exit code carries the verdict."""
    stats = environment.stats.total
    if stats.num_failures:
        environment.process_exit_code = 1
