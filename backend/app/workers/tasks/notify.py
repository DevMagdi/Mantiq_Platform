from __future__ import annotations

from app.workers._runtime import run_async
from app.workers.celery_app import celery_app


@celery_app.task(name="broker_hub.lead_followup_reminders")
def lead_followup_reminders() -> dict[str, int]:
    """Morning/afternoon: leads whose follow-up is overdue get an in-app notification for the owner."""

    async def body() -> dict[str, int]:
        from datetime import UTC, datetime

        import sqlalchemy as sa
        from sqlalchemy import select

        from app.core.db import get_sessionmaker
        from app.domain.enums import LeadStatus, NotificationType
        from app.models.leads import Lead
        from app.models.requests import Notification

        async with get_sessionmaker()() as session:
            now = datetime.now(UTC)
            overdue = (
                (
                    await session.execute(
                        select(Lead).where(
                            Lead.next_followup_at < now,
                            Lead.deleted_at.is_(None),
                            Lead.status.notin_([LeadStatus.WON, LeadStatus.LOST]),
                            Lead.assigned_to.is_not(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            created = 0
            for lead in overdue:
                exists = (
                    await session.execute(
                        sa.select(
                            sa.exists().where(
                                Notification.entity_id == lead.id,
                                Notification.type == NotificationType.LEAD_FOLLOWUP_DUE,
                                Notification.created_at >= now.replace(hour=0, minute=0, second=0, microsecond=0),
                            )
                        )
                    )
                ).scalar_one()
                if exists:
                    continue
                session.add(
                    Notification(
                        user_id=lead.assigned_to,
                        type=NotificationType.LEAD_FOLLOWUP_DUE,
                        title=f"متابعة متأخرة · {lead.name}",
                        body="الحاق موعد المتابعة",
                        entity_type="lead",
                        entity_id=lead.id,
                    )
                )
                created += 1
            await session.commit()
            return {"overdue": len(overdue), "notified": created}

    return run_async(body)()


@celery_app.task(name="broker_hub.send_email")
def send_email(to: str, subject: str, body: str) -> bool:
    async def run() -> bool:
        from app.integrations.notifier import email_notifier

        return await email_notifier().send_email(to=to, subject=subject, body=body)

    return run_async(lambda: run())()
