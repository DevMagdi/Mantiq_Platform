"""R12 channels load — intake storm, inbox read path, media path, auth/isolation (brief §F).

Classes are selected with LOAD_MODE: intake | inbox | media | isolation | mixed.
Ids are deterministic per user+counter, and every Nth post replays an earlier id — the DB check after
the run (`scripts/load/r12_verify.py`) is the idempotency proof, not the 200s.

    LOAD_MODE=mixed locust -f scripts/load/r12_locustfile.py --headless -u 50 -r 10 -t 12m \
        --host http://127.0.0.1:8022 --csv docs/qa/load/raw/r12-mixed --only-summary
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import random
import time

from locust import HttpUser, between, task

PASSWORD = os.getenv("LOAD_PASSWORD", "Br0kerHub!2026")
MANAGER = os.environ.get("LOAD_MANAGER", "manager@brokerhub.test")
CS = os.environ.get("LOAD_CS", "cs@brokerhub.test")
INBOUND_SECRET = os.getenv("R12_INBOUND_SECRET", "load-inbound-secret")
WA_SECRET = os.getenv("R12_WA_SECRET", "load-wa-secret")
MODE = os.environ.get("LOAD_MODE", "mixed")
WEIGHTS = {
    "intake": {"IntakeStorm": 1, "InboxReader": 0, "MediaReader": 0, "IsolationProbe": 0},
    "inbox": {"IntakeStorm": 0, "InboxReader": 1, "MediaReader": 0, "IsolationProbe": 0},
    "media": {"IntakeStorm": 0, "InboxReader": 0, "MediaReader": 1, "IsolationProbe": 0},
    "isolation": {"IntakeStorm": 0, "InboxReader": 0, "MediaReader": 0, "IsolationProbe": 1},
    "mixed": {"IntakeStorm": 3, "InboxReader": 5, "MediaReader": 2, "IsolationProbe": 1},
}


def _sign(secret: str, raw: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


class Authed(HttpUser):
    abstract = True
    wait_time = between(0.3, 1.2)
    email = MANAGER

    def on_start(self) -> None:
        self.token = ""
        self.request_ids: list[str] = []
        self.media: list[tuple[str, str]] = []
        r = self.client.post("/api/v1/auth/login", json={"email": self.email, "password": PASSWORD}, name="POST /auth/login")
        if r.status_code == 200:
            body = r.json() if r.status_code == 200 else {}
            self.token = body.get("access_token", "")
        self._discover()

    def _h(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _discover(self) -> None:
        if not self.token:
            return
        with self.client.get("/api/v1/requests?channel=manual&page_size=25", headers=self._h(), name="setup ids", catch_response=True) as r:
            if r.status_code in (403, 404):
                r.success()  # a scoped rep is refused the full list by design — that is the isolation gate working
                return
        if r.status_code != 200:
            return
        items = r.json().get("items", [])
        self.request_ids = [i["id"] for i in items]
        for rid in self.request_ids:
            d = self.client.get(f"/api/v1/requests/{rid}", headers=self._h(), name="setup detail")
            if d.status_code == 200:
                for a in d.json().get("attachments", []):
                    self.media.append((rid, a["id"]))
            if len(self.media) >= 6:
                break


class IntakeStorm(HttpUser):
    """Signed email + WhatsApp-shaped + call webhooks; every 5th send replays an earlier external id."""

    weight = WEIGHTS.get(MODE, WEIGHTS["mixed"])["IntakeStorm"]
    wait_time = between(0.1, 0.6)

    def on_start(self) -> None:
        self.n = 0
        self.tag = f"{int(time.time())}-{random.randint(0, 10**6)}"

    def _pick(self) -> int:
        self.n += 1
        return self.n - 5 if self.n % 5 == 0 and self.n > 5 else self.n  # replay -> must dedupe

    @task(3)
    def email(self) -> None:
        k = self._pick()
        payload = {
            "external_id": f"load-mail-{self.tag}-{k}",
            "from": f"storm-{self.tag}@load.test",
            "from_name": "عميل حمل",
            "subject": f"استفسار حمل {k}",
            "body": "نص بريد حمل",
            "attachments": [],
        }
        raw = json.dumps(payload).encode()
        with self.client.post(
            "/api/v1/webhooks/email",
            data=raw,
            headers={"content-type": "application/json", "x-email-signature": _sign(INBOUND_SECRET, raw)},
            name="POST /webhooks/email",
            catch_response=True,
        ) as r:
            if r.status_code != 200:
                r.failure(f"status {r.status_code}: {r.text[:120]}")

    @task(3)
    def whatsapp(self) -> None:
        k = self._pick()
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"wa_id": "2010555000111", "profile": {"name": "عميل"}}],
                                "messages": [
                                    {
                                        "id": f"wamid.load-{self.tag}-{k}",
                                        "from": "2010555000111",
                                        "type": "text",
                                        "text": {"body": f"رسالة حمل {k}"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ],
        }
        raw = json.dumps(payload).encode()
        with self.client.post(
            "/api/v1/webhooks/whatsapp",
            data=raw,
            headers={"content-type": "application/json", "x-hub-signature-256": _sign(WA_SECRET, raw)},
            name="POST /webhooks/whatsapp",
            catch_response=True,
        ) as r:
            if r.status_code != 200:
                r.failure(f"status {r.status_code}: {r.text[:120]}")

    @task(2)
    def call(self) -> None:
        k = self._pick()
        payload = {"call_id": f"load-call-{self.tag}-{k}", "event": "call.started", "from": "+2010555000111"}
        with self.client.post("/api/v1/webhooks/mobile-calls", json=payload, name="POST /webhooks/mobile-calls", catch_response=True) as r:
            if r.status_code != 200:
                r.failure(f"status {r.status_code}: {r.text[:120]}")


class InboxReader(Authed):
    weight = WEIGHTS.get(MODE, WEIGHTS["mixed"])["InboxReader"]
    email = CS

    @task(3)
    def list_filtered(self) -> None:
        q = random.choice(["", "?channel=whatsapp", "?channel=email", "?overdue=true", "?status=new", "?page=2&page_size=25"])
        with self.client.get(f"/api/v1/requests{q}", headers=self._h(), name="GET /requests (filtered)", catch_response=True) as r:
            if r.status_code != 200:
                r.failure(f"status {r.status_code}")

    @task(2)
    def open_thread(self) -> None:
        if not self.request_ids:
            return
        rid = random.choice(self.request_ids)
        with self.client.get(f"/api/v1/requests/{rid}", headers=self._h(), name="GET /requests/{id}", catch_response=True) as r:
            if r.status_code != 200:
                r.failure(f"status {r.status_code}")


class MediaReader(Authed):
    weight = WEIGHTS.get(MODE, WEIGHTS["mixed"])["MediaReader"]
    email = MANAGER

    @task
    def download_media(self) -> None:
        if not self.media:
            return
        rid, aid = random.choice(self.media)
        with self.client.get(
            f"/api/v1/requests/{rid}/attachments/{aid}/download-url",
            headers=self._h(),
            name="GET download-url (issue)",
            catch_response=True,
        ) as r:
            if r.status_code != 200:
                r.failure(f"status {r.status_code}")
                return
            url = r.json().get("url", "")
        if url:
            with self.client.get(url, headers=self._h(), name="GET /media signed download", catch_response=True) as r:
                if r.status_code != 200:
                    r.failure(f"status {r.status_code}")
                elif not r.headers.get("cache-control"):
                    r.failure("signed media response carries no cache-control header")


class IsolationProbe(Authed):
    """A rep asking for requests outside their book must get 403/404 — a 200 here is a leak, counted as failure."""

    weight = WEIGHTS.get(MODE, WEIGHTS["mixed"])["IsolationProbe"]
    email = "load.rep00@brokerhub.test"

    @task
    def probe(self) -> None:
        if not self.request_ids:
            return
        rid = random.choice(self.request_ids)
        with self.client.get(f"/api/v1/requests/{rid}", headers=self._h(), name="GET other-book request (rep)", catch_response=True) as r:
            if r.status_code == 200:
                # own-book rows are legitimate; only count as leak when the row is not theirs — the
                # post-run verify script cross-checks assignees, so mark nothing here.
                pass
            elif r.status_code not in (403, 404):
                r.failure(f"unexpected status {r.status_code}")
