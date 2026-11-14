"""Celery application configured for Redis dispatch and periodic PostgreSQL dispatchers."""

from typing import cast

from celery import Celery  # type: ignore[import-untyped]

from sentinel.settings import get_settings


def create_celery_app() -> Celery:
    settings = get_settings()
    app = Celery("sentinel", broker=settings.redis_url)
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_ignore_result=True,
        worker_concurrency=settings.celery_concurrency,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        broker_connection_retry_on_startup=True,
        beat_schedule={
            "sentinel-outbox-dispatch": {
                "task": "sentinel.dispatch_outbox",
                "schedule": settings.outbox_dispatch_seconds,
            },
            "sentinel-schedule-dispatch": {
                "task": "sentinel.dispatch_schedules",
                "schedule": settings.schedule_dispatch_seconds,
            },
            "sentinel-recover-stale-jobs": {
                "task": "sentinel.recover_stale_jobs",
                "schedule": max(30, settings.stale_job_seconds // 3),
            },
        },
        include=["sentinel.operations.tasks"],
    )
    return cast(Celery, app)


celery_app = create_celery_app()
