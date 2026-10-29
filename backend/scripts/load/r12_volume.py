"""R12 volume: ≥20k Requests across whatsapp/call/email/manual with media, for the channels load suite.

Bulk ORM inserts (the load DB only). Also writes real object-storage files for a slice of attachments so
signed-URL downloads have something to serve, and sets the email `inbound_secret` the intake storm signs
with (env R12_INBOUND_SECRET, default a load-only value — never a real secret).

    PYTHONPATH=. .venv/bin/python scripts/load/r12_volume.py --requests 20000
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import uuid
from datetime import UTC, datetime, timedelta

from app.core.db import get_sessionmaker
from app.domain.enums import AttachmentOwner, Channel, ClientStatus, ClientType, MessageDirection, RequestStatus
from app.integrations.storage import storage
from app.models.auth import Role, User
from app.models.clients import Client
from app.models.requests import Attachment, RequestMessage, ServiceRequest
from app.services import integrations_settings
from sqlalchemy import func, select

RNG = random.Random(1207)
CHANNELS = [Channel.WHATSAPP] * 4 + [Channel.CALL] * 2 + [Channel.EMAIL] * 2 + [Channel.MANUAL] * 2
PDF = b"%PDF-1.4 r12-load-fixture\n" + b"x" * 2048


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=int, default=20000)
    args = parser.parse_args()
    secret = os.getenv("R12_INBOUND_SECRET", "load-inbound-secret")
    async with get_sessionmaker()() as session:
        manager = (await session.execute(select(User).where(User.email == "manager@brokerhub.test"))).scalar_one()
        await integrations_settings.update(session, manager, "email", secrets={"inbound_secret": secret})
        # WhatsApp signature verification in the intake storm verifies against this saved app_secret
        await integrations_settings.update(
            session,
            manager,
            "whatsapp",
            config={"phone_number_id": "12345"},
            secrets={
                "access_token": "load-token",
                "app_secret": os.getenv("R12_WA_SECRET", "load-wa-secret"),
                "verify_token": "load-verify",
            },
        )
        await integrations_settings.update(session, manager, "telephony", secrets={"webhook_secret": "load-call-secret"})
        reps = list(
            (await session.execute(select(User).join(Role, User.role_id == Role.id).where(Role.code == "sales_rep"))).scalars().all()
        )
        have = int((await session.execute(select(func.count(Client.id)))).scalar_one())
        clients: list[Client] = []
        for i in range(max(0, 400 - have)):
            client = Client(
                type=ClientType.COMPANY,
                name=f"شركة حمل R12 {have + i + 1}",
                status=ClientStatus.ACTIVE,
                account_owner_id=RNG.choice(reps).id if reps else None,
                since_date=(datetime.now(UTC) - timedelta(days=RNG.randint(30, 2000))).date(),
            )
            session.add(client)
            clients.append(client)
        await session.flush()
        all_clients = list((await session.execute(select(Client))).scalars().all())
        # next request number
        number = int((await session.execute(select(func.coalesce(func.max(ServiceRequest.number), 0)))).scalar_one())
        now = datetime.now(UTC)
        made = 0
        for batch_start in range(0, args.requests, 500):
            reqs: list[ServiceRequest] = []
            for i in range(batch_start, min(batch_start + 500, args.requests)):
                number += 1
                channel = RNG.choice(CHANNELS)
                req = ServiceRequest(
                    number=number,
                    client_id=RNG.choice(all_clients).id,
                    channel=channel,
                    subject=f"طلب حمل {channel.value} #{number}",
                    description="بيانات حمل اصطناعية — ليست بيانات حقيقية",
                    contact_name="عميل حمل",
                    contact_mobile=f"+2010{RNG.randint(10000000, 99999999)}",
                    status=RNG.choice(list(RequestStatus)),
                    assigned_to=RNG.choice(reps).id if reps and i % 3 else None,
                    external_thread_id=f"load-{channel.value}-{number}" if channel != Channel.MANUAL else None,
                    last_activity_at=now - timedelta(minutes=RNG.randint(0, 40000)),
                )
                session.add(req)
                reqs.append(req)
            await session.flush()
            store = storage()
            for req in reqs:
                for j in range(RNG.randint(1, 3)):
                    msg = RequestMessage(
                        request_id=req.id,
                        direction=MessageDirection.IN,
                        channel=req.channel,
                        body=f"رسالة حمل {j}",
                        external_id=f"load-msg-{req.number}-{j}",
                    )
                    session.add(msg)
                    await session.flush()
                    if j == 0 and RNG.random() < 0.30:
                        key = f"requests/{req.id}/load-fixture.pdf"
                        store.put_object(key, PDF, "application/pdf")
                        session.add(
                            Attachment(
                                id=uuid.uuid4(),
                                owner_type=AttachmentOwner.REQUEST,
                                owner_id=msg.id,
                                request_id=req.id,
                                filename="load-fixture.pdf",
                                object_key=key,
                                mime="application/pdf",
                                size=len(PDF),
                            )
                        )
                made += 1
            await session.commit()
        from app.models.requests import RequestNumberCounter

        counter = (await session.execute(select(RequestNumberCounter).where(RequestNumberCounter.key == "requests"))).scalar_one_or_none()
        if counter is None:
            session.add(RequestNumberCounter(key="requests", last_value=number))
        else:
            counter.last_value = max(counter.last_value, number)
        await session.commit()
        total = int((await session.execute(select(func.count(ServiceRequest.id)))).scalar_one())
        atts = int((await session.execute(select(func.count(Attachment.id)))).scalar_one())
        print(f"requests_total={total} attachments_total={atts} made_now={made}")


if __name__ == "__main__":
    asyncio.run(main())
