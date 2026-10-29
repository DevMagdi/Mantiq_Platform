"""ERP load (ADR-031): 20 concurrent finance readers against ≥50k posted ledger rows.

    locust -f scripts/load/erp_locustfile.py --headless -u 20 -r 5 -t 3m30s \
        --host http://127.0.0.1:8021 --csv docs/qa/load/raw/erp-r11 --only-summary
A small write slice posts+voids throwaway expenses on the load DB only (never production).
"""

from __future__ import annotations

import itertools
import os

from locust import HttpUser, between, task

PASSWORD = os.environ.get("LOAD_PASSWORD", "Br0kerHub!2026")
MANAGER = os.environ.get("LOAD_MANAGER", "manager@brokerhub.test")
PERIODS = ["month", "quarter", "semiannual", "year"]
_counter = itertools.count()


class ErpReader(HttpUser):
    wait_time = between(0.3, 1.0)

    def on_start(self) -> None:
        r = self.client.post("/api/v1/auth/login", json={"email": MANAGER, "password": PASSWORD}, name="POST /auth/login")
        token = r.json().get("access_token", "")
        self.client.headers.update({"Authorization": f"Bearer {token}"})
        self.i = 0

    @task(4)
    def spend(self) -> None:
        period = PERIODS[self.i % len(PERIODS)]
        self.i += 1
        with self.client.get(
            f"/api/v1/finance/spend-by-period?period={period}", name="GET /finance/spend-by-period", catch_response=True
        ) as r:
            if r.status_code != 200 or "by_type" not in r.text:
                r.failure(f"{r.status_code} {r.text[:120]}")

    @task(3)
    def ledger(self) -> None:
        page = (self.i % 20) + 1
        self.client.get(f"/api/v1/finance/ledger?page={page}&page_size=50", name="GET /finance/ledger?page")

    @task(2)
    def cashbox_ar(self) -> None:
        self.client.get("/api/v1/finance/cashbox", name="GET /finance/cashbox")
        self.client.get("/api/v1/finance/ar/summary", name="GET /finance/ar/summary")

    @task(2)
    def executive(self) -> None:
        self.client.get("/api/v1/executive/dashboard", name="GET /executive/dashboard")

    @task(1)
    def write_slice(self) -> None:
        n = next(_counter)
        with self.client.post(
            "/api/v1/finance/ledger",
            json={
                "entry_type": "expense",
                "direction": "out",
                "amount": 10,
                "method": "cash",
                "note": f"load-write-{n}",
            },
            name="POST /finance/ledger (load slice)",
            catch_response=True,
        ) as r:
            if r.status_code != 201:
                r.failure(f"{r.status_code} {r.text[:120]}")
                return
            entry_id = r.json()["id"]
        self.client.post(
            f"/api/v1/finance/ledger/{entry_id}/void",
            json={"reason": "load slice cleanup"},
            name="POST /finance/ledger/{id}/void (load slice)",
        )
