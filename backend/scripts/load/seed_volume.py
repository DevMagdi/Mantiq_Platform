"""R8 volume seed: the data volumes the load test's acceptance criteria assume.

`app.seed` is the *product's* seed — 63 leads, 6 clients, 24 requests — and it is deliberately small because
it exists for demos and for `live_matrix.py`. The load test needs more than that to be a load test:
`docs/qa/load/` requires ≥ 200 leads, ≥ 100 open requests and ≥ 50 clients before a single number is quoted.

This script adds exactly those volumes **on top of** the product seed, straight into PostgreSQL, so the
scenarios measure queries over a realistic table size instead of over a demo-sized one. It is idempotent per
run only in the sense that it appends: run it once on a fresh database.

Usage:
    DATABASE_URL=postgresql+asyncpg://postgres@/brokerhub_load?host=/tmp/r8pg \
        python scripts/load/seed_volume.py --leads 160 --clients 50 --requests 90
"""

from __future__ import annotations

import argparse
import asyncio
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.core.db import get_sessionmaker
from app.core.security import hash_password
from app.domain.enums import Channel, ClientStatus, ClientType, LeadStatus, RequestPriority, RequestStatus
from app.models.auth import Role, User
from app.models.clients import Client, ClientContact
from app.models.leads import Lead
from app.models.masters import InsuranceType, Insurer
from app.models.requests import RequestNumberCounter, ServiceRequest
from sqlalchemy import func, select

RNG = random.Random(20261028)
PASSWORD = "Br0kerHub!2026"
FIRST = ["منى", "كريم", "هبة", "طارق", "سلمى", "أحمد", "نور", "يوسف"]
LAST = ["عبد الله", "فتحي", "صبري", "الشريف", "منصور", "حسن"]
COMPANIES = ["النيل", "الدلتا", "الشرق", "المصرية", "الأهرام", "سينا", "برديس", "القناة"]
# `RequestStatus.open_statuses()` is the product's own definition; the volume seed must not invent a fourth one
OPEN = RequestStatus.open_statuses()


async def _reps(session) -> list[User]:  # type: ignore[no-untyped-def]
    role = (await session.execute(select(Role).where(Role.code == "sales_rep"))).scalar_one()
    return list((await session.execute(select(User).where(User.role_id == role.id))).scalars().all())


async def _extra_reps(session, count: int) -> list[User]:  # type: ignore[no-untyped-def]
    """Load-test reps, so 10 concurrent browser reps are 10 real seats rather than one seat reused."""
    role = (await session.execute(select(Role).where(Role.code == "sales_rep"))).scalar_one()
    made: list[User] = []
    for i in range(count):
        email = f"load.rep{i:02d}@brokerhub.test"
        existing = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if existing is None:
            existing = User(
                email=email,
                password_hash=hash_password(PASSWORD),
                full_name=f"بيع تحميل {i + 1:02d}",
                role_id=role.id,
            )
            session.add(existing)
            made.append(existing)
    await session.flush()
    return made


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--leads", type=int, default=160)
    parser.add_argument("--clients", type=int, default=50)
    parser.add_argument("--requests", type=int, default=90)
    parser.add_argument("--reps", type=int, default=10)
    args = parser.parse_args()

    async with get_sessionmaker()() as session:
        reps = await _reps(session)
        reps += await _extra_reps(session, args.reps)
        types = list((await session.execute(select(InsuranceType))).scalars().all())
        insurers = list((await session.execute(select(Insurer))).scalars().all())
        now = datetime.now(UTC)

        # --- clients (≥ 50 in total, the criterion) -------------------------------------------------
        have_clients = int((await session.execute(select(func.count(Client.id)))).scalar_one())
        made_clients: list[Client] = []
        for i in range(max(0, args.clients - have_clients)):
            client = Client(
                type=ClientType.COMPANY,
                name=f"{RNG.choice(COMPANIES)} للتأمين {have_clients + i + 1}",
                status=ClientStatus.ACTIVE,
                account_owner_id=RNG.choice(reps).id,
                since_date=(now - timedelta(days=RNG.randint(200, 3000))).date(),
                address=RNG.choice(["القاهرة", "الجيزة", "الإسكندرية", "طنطا", "أسيوط"]),
            )
            session.add(client)
            made_clients.append(client)
        await session.flush()
        for client in made_clients:
            session.add(
                ClientContact(
                    client_id=client.id,
                    name=f"{RNG.choice(FIRST)} {RNG.choice(LAST)}",
                    title="مدير الموارد البشرية",
                    mobile=f"+2010{RNG.randint(10000000, 99999999)}",
                )
            )

        all_clients = list((await session.execute(select(Client))).scalars().all())

        # --- leads (≥ 200 in total) -----------------------------------------------------------------
        have_leads = int((await session.execute(select(func.count(Lead.id)))).scalar_one())
        base_lead = have_leads * 10
        statuses = [
            LeadStatus.NEW,
            LeadStatus.INTERESTED,
            LeadStatus.MEETING_SCHEDULED,
            LeadStatus.QUOTE_SENT,
            LeadStatus.NEGOTIATION,
            LeadStatus.MEETING_DONE,
            LeadStatus.POSTPONED,
            LeadStatus.WON,
            LeadStatus.LOST,
        ]
        for i in range(max(0, args.leads - have_leads)):
            session.add(
                Lead(
                    type=ClientType.COMPANY if i % 5 else ClientType.INDIVIDUAL,
                    name=f"{RNG.choice(COMPANIES)} تحميل {i + 1}",
                    contact_name=f"{RNG.choice(FIRST)} {RNG.choice(LAST)}",
                    contact_title="مدير مشتريات",
                    # mobile is unique among active leads; a counter keeps the volume seed collision-free
                    mobile=f"+2011{base_lead + i:08d}",
                    email=f"load.lead{i}@company.test",
                    source=RNG.choice(["واتساب", "إحالة", "معرض", "فيسبوك"]),
                    insurance_type_id=RNG.choice(types).id,
                    headcount=RNG.randint(4, 300),
                    current_insurer_id=RNG.choice(insurers).id,
                    current_policy_expiry=(now + timedelta(days=RNG.randint(10, 330))).date(),
                    budget=Decimal(RNG.randint(50_000, 900_000)),
                    needs_text="حمل بيانات التحميل: تغطية طبية لعائلة كاملة",
                    status=statuses[i % len(statuses)] if i < max(0, args.leads - have_leads) - 40 else LeadStatus.NEW,
                    assigned_to=RNG.choice(reps).id if i % 4 else None,
                    next_followup_at=now + timedelta(days=RNG.randint(-8, 20)),
                )
            )
        await session.flush()

        # --- requests (≥ 100 *open* in total) -------------------------------------------------------
        have_open = int((await session.execute(select(func.count(ServiceRequest.id)).where(ServiceRequest.status.in_(OPEN)))).scalar_one())
        top_number = int((await session.execute(select(func.max(ServiceRequest.number)))).scalar_one() or 1000)
        made = 0
        for i in range(max(0, args.requests - have_open)):
            top_number += 1
            client = RNG.choice(all_clients)
            status = OPEN[i % len(OPEN)]
            session.add(
                ServiceRequest(
                    number=top_number,
                    client_id=client.id,
                    insurance_type_id=RNG.choice(types).id,
                    insurer_id=RNG.choice(insurers).id,
                    channel=RNG.choice([Channel.MANUAL, Channel.WHATSAPP, Channel.CALL]),
                    subject=f"طلب تحميل {top_number}: تجديد وثيقة",
                    description="بيانات حجم للاختبار — طلب مفتوح تحت الحمل",
                    contact_name=f"{RNG.choice(FIRST)} {RNG.choice(LAST)}",
                    contact_mobile=f"+2012{base_lead + i:08d}",
                    status=status,
                    priority=RNG.choice([RequestPriority.NORMAL, RequestPriority.HIGH, RequestPriority.URGENT]),
                    assigned_to=RNG.choice(reps).id if i % 3 else None,
                    sla_due_at=now + timedelta(hours=RNG.randint(-30, 60)),
                    last_activity_at=now - timedelta(minutes=RNG.randint(0, 4000)),
                )
            )
            made += 1
        await session.flush()

        # the counter row the request-number sequence uses — filtered by key: since R10 the ledger
        # keeps its own `ledger` counter row, so an unfiltered scalar_one_or_none raises
        # MultipleResultsFound and the whole load stack fails to boot (found by CI on PR #15)
        counter_row = (
            await session.execute(select(RequestNumberCounter).where(RequestNumberCounter.key == "requests"))
        ).scalar_one_or_none()
        if counter_row is None:
            session.add(RequestNumberCounter(key="requests", last_value=top_number))
        else:
            counter_row.last_value = max(int(counter_row.last_value or 0), top_number)

        await session.commit()

        totals = {
            "leads": int((await session.execute(select(func.count(Lead.id)))).scalar_one()),
            "clients": int((await session.execute(select(func.count(Client.id)))).scalar_one()),
            "requests_open": int(
                (await session.execute(select(func.count(ServiceRequest.id)).where(ServiceRequest.status.in_(OPEN)))).scalar_one()
            ),
            "requests_total": int((await session.execute(select(func.count(ServiceRequest.id)))).scalar_one()),
            "users": int((await session.execute(select(func.count(User.id)))).scalar_one()),
            "added_requests": made,
        }
    print(totals)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
