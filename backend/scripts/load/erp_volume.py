"""ERP load volume (ADR-031): ≥200 clients and ≥50,000 posted ledger rows over 12 months.

Run once on a fresh load database after `app.seed`. Bulk Core inserts (not ORM post_entry) because
seeding 50k rows through the audited API would measure the seed, not the reads under load.

Usage:
    DATABASE_URL=postgresql+asyncpg://postgres@/brokerhub_load?host=/tmp/erppg \
        python scripts/load/erp_volume.py --rows 50000 --clients 200
"""

from __future__ import annotations

import argparse
import asyncio
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from app.core.db import get_sessionmaker
from app.domain.enums import ClientStatus, ClientType
from app.models.clients import Client
from app.models.finance import LedgerEntry
from sqlalchemy import func, insert, select

RNG = random.Random(20261028)
COMBOS = [
    ("premium_receivable", "in"),
    ("commission_receivable", "in"),
    ("commission_payable", "out"),
    ("expense", "out"),
    ("adjustment", "in"),
    ("adjustment", "out"),
]
METHODS = ["cash", "bank", "wallet", "other", None]


async def main(rows: int, clients: int) -> None:
    maker = get_sessionmaker()
    async with maker() as session:
        existing_clients = int((await session.execute(select(func.count()).select_from(Client))).scalar_one() or 0)
        made_clients = []
        for i in range(max(0, clients - existing_clients)):
            c = Client(name=f"عميل حمل ERP {i:03d}", type=ClientType.COMPANY, status=ClientStatus.ACTIVE)
            session.add(c)
            made_clients.append(c)
        await session.flush()
        client_ids = list((await session.execute(select(Client.id))).scalars().all())
        existing = int((await session.execute(select(func.count()).select_from(LedgerEntry))).scalar_one() or 0)
        print(f"clients total={len(client_ids)} ledger_before={existing}")
        now = datetime.now(UTC)
        batch: list[dict] = []
        for i in range(rows):
            entry_type, direction = COMBOS[i % len(COMBOS)]
            posted = now - timedelta(days=RNG.randrange(0, 365), minutes=RNG.randrange(0, 1440))
            batch.append(
                {
                    "id": uuid4(),
                    "entry_no": f"LE-L{i + existing:07d}",
                    "entry_type": entry_type,
                    "direction": direction,
                    "amount": Decimal(RNG.randrange(100, 50000)),
                    "currency": "EGP",
                    "status": "posted",
                    "client_id": RNG.choice(client_ids) if client_ids else None,
                    "method": RNG.choice(METHODS),
                    "posted_at": posted,
                    "created_at": posted,
                    "updated_at": posted,
                }
            )
            if len(batch) >= 5000:
                await session.execute(insert(LedgerEntry), batch)
                batch.clear()
        if batch:
            await session.execute(insert(LedgerEntry), batch)
        await session.commit()
        total = int((await session.execute(select(func.count()).select_from(LedgerEntry))).scalar_one() or 0)
        print(f"ledger_after={total} inserted={rows}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=50000)
    ap.add_argument("--clients", type=int, default=200)
    args = ap.parse_args()
    asyncio.run(main(args.rows, args.clients))
