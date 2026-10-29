"""Post-run proof for the R12 load suite: no duplicate rows from replayed webhooks, and isolation over HTTP.

BASE=http://127.0.0.1:8022 PYTHONPATH=. .venv/bin/python scripts/load/r12_verify.py
"""

from __future__ import annotations

import asyncio
import os

import httpx
from app.core.db import get_sessionmaker
from app.models.requests import Attachment, ServiceRequest
from sqlalchemy import func, select, text

BASE = os.environ.get("BASE", "http://127.0.0.1:8022")
PASSWORD = os.getenv("LOAD_PASSWORD", "Br0kerHub!2026")


async def db_checks() -> dict[str, int]:
    async with get_sessionmaker()() as session:
        total = int((await session.execute(select(func.count(ServiceRequest.id)))).scalar_one())
        by_channel = dict((await session.execute(text("select channel, count(*) from requests group by channel"))).all())
        dup_msgs = int(
            (
                await session.execute(
                    text(
                        "select count(*) from (select external_id from request_messages where external_id is not null group by external_id having count(*) > 1) d"
                    )
                )
            ).scalar_one()
        )
        dup_calls = int(
            (
                await session.execute(
                    text("select count(*) from (select provider_call_id from call_records group by provider_call_id having count(*) > 1) d")
                )
            ).scalar_one()
        )
        atts = int((await session.execute(select(func.count(Attachment.id)))).scalar_one())
        return {
            "requests": total,
            "by_channel": by_channel,
            "duplicate_message_external_ids": dup_msgs,
            "duplicate_call_ids": dup_calls,
            "attachments": atts,
        }


async def login(client: httpx.AsyncClient, email: str) -> str:
    r = await client.post(f"{BASE}/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    r.raise_for_status()
    return r.json()["access_token"]


async def isolation_checks() -> dict[str, int]:
    async with httpx.AsyncClient(timeout=30) as client:
        mgr = await login(client, "manager@brokerhub.test")
        rep = await login(client, "load.rep00@brokerhub.test")
        # a request the rep does not own: manager sees all, pick one assigned to someone else / unassigned
        listing = await client.get(f"{BASE}/api/v1/requests?page_size=50", headers={"Authorization": f"Bearer {mgr}"})
        items = listing.json().get("items", [])
        rep_id = (await client.get(f"{BASE}/api/v1/auth/me", headers={"Authorization": f"Bearer {rep}"})).json()["id"]
        foreign = next((i for i in items if i.get("assigned_to") != rep_id), None)
        leak = -1
        if foreign:
            probe = await client.get(f"{BASE}/api/v1/requests/{foreign['id']}", headers={"Authorization": f"Bearer {rep}"})
            leak = probe.status_code
        portal = await login(client, os.environ.get("LOAD_PORTAL", "owner.mantiq@company.test"))
        portal_status = (await client.get(f"{BASE}/api/v1/requests", headers={"Authorization": f"Bearer {portal}"})).status_code
        return {"rep_foreign_request_status": leak, "portal_requests_status": portal_status}


async def main() -> None:
    print(await db_checks())
    print(await isolation_checks())


if __name__ == "__main__":
    asyncio.run(main())
