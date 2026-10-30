"""R15 read paths under load: the dashboard, the queue, the board, the client list.

Four screens, weighted the way an office uses them during a morning: the queue most, the dashboard on every
return to the tab, the board while a manager works a campaign, the client list while somebody looks a company
up. Each request is measured under its route name (`GET /dashboard/home`, not `/dashboard/home?days=7`), so
the report can quote one line per gate.

    LOAD_MODE=mixed locust -f scripts/load/r15_locustfile.py --headless -u 25 -r 5 -t 90s \\
        --host http://127.0.0.1:8021 --csv docs/perf/raw/r15-2w-25u --only-summary

`LOAD_MODE` selects a single path for the per-endpoint gates (`dashboard`, `requests`, `board`, `clients`);
`mixed` runs all four at the weights below, which is the number quoted for 1-vs-2 workers.
"""

from __future__ import annotations

import os

from locust import HttpUser, between, task

PASSWORD = os.getenv("LOAD_PASSWORD", "Mantiq!2026")
MANAGER = os.environ.get("LOAD_MANAGER", "manager@mantiq.test")
MODE = os.getenv("LOAD_MODE", "mixed")

#: how much each screen is asked for. The queue leads because it is the screen that is *open all day*; the
#: dashboard is second because every tab return re-mounts it.
WEIGHT = {"dashboard": 3, "requests": 5, "board": 3, "clients": 2}
if MODE in WEIGHT:
    WEIGHT = {key: (value if key == MODE else 0) for key, value in WEIGHT.items()}


class OfficeUser(HttpUser):
    wait_time = between(0.4, 1.1)

    def on_start(self) -> None:
        self.token = ""
        response = self.client.post("/api/v1/auth/login", json={"email": MANAGER, "password": PASSWORD}, name="POST /auth/login")
        if response.status_code == 200:
            self.token = response.json()["access_token"]

    @property
    def auth(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.token}"}

    @task(WEIGHT["dashboard"])
    def dashboard(self) -> None:
        self.client.get("/api/v1/dashboard/home", headers=self.auth, name="GET /dashboard/home")

    @task(WEIGHT["requests"])
    def queue(self) -> None:
        self.client.get(
            "/api/v1/requests",
            params={"only_open": "true", "page_size": 25, "sort": "last_activity_at", "order": "desc"},
            headers=self.auth,
            name="GET /requests?only_open",
        )

    @task(WEIGHT["board"])
    def board(self) -> None:
        self.client.get("/api/v1/leads/board", params={"per_column": 60}, headers=self.auth, name="GET /leads/board")

    @task(WEIGHT["clients"])
    def clients(self) -> None:
        self.client.get("/api/v1/clients", params={"page_size": 25}, headers=self.auth, name="GET /clients")
