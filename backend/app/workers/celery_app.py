"""Celery app: workers + beat schedule (spec §8)."""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

settings_module = "app.core.config"


def make_celery() -> Celery:
    from app.core.config import get_settings

    settings = get_settings()
    app = Celery(
        "broker_hub",
        broker=settings.REDIS_URL,
        backend=settings.REDIS_URL,
        include=["app.workers.tasks.sla", "app.workers.tasks.notify", "app.workers.tasks.imports", "app.workers.tasks.media"],
    )
    app.conf.update(
        task_always_eager=settings.CELERY_TASK_ALWAYS_EAGER,
        task_eager_propagates=False,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        timezone="Africa/Cairo",
        enable_utc=True,
        beat_schedule={
            "sla-sweep-every-minute": {"task": "broker_hub.sla_sweep", "schedule": 60.0},
            "lead-followup-reminders": {"task": "broker_hub.lead_followup_reminders", "schedule": crontab(hour="8,14", minute="0")},
            "media-retention": {"task": "broker_hub.purge_expired_recordings", "schedule": crontab(hour="3", minute="15")},
            "tmp-file-cleanup": {"task": "broker_hub.cleanup_tmp_files", "schedule": crontab(hour="4", minute="30")},
        },
    )
    return app


celery_app = make_celery()
