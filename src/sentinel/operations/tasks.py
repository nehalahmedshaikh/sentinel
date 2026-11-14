"""Celery task adapters; all durable state is committed to PostgreSQL."""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, ParamSpec, TypeVar
from uuid import UUID

from celery import shared_task  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from sentinel.domain.contracts import DatasetInput
from sentinel.incidents.service import IncidentService
from sentinel.ingestion.service import DatasetService
from sentinel.operations.celery_app import celery_app
from sentinel.operations.contracts import JobStatus, JobType, transition_job
from sentinel.operations.models import (
    JobRow,
    MonitoringScheduleRow,
    OutboxRow,
    ScheduleExecutionRow,
    utcnow,
)
from sentinel.operations.service import dispatch_due_schedules, dispatch_outbox
from sentinel.persistence.database import create_database_engine, session_factory
from sentinel.persistence.models import DatasetVersionRow
from sentinel.quality.service import QualityService
from sentinel.reliability.contracts import (
    BaselineStrategy,
    ReliabilityAssessmentInput,
)
from sentinel.reliability.service import ReliabilityService
from sentinel.settings import get_settings
from sentinel.storage.filesystem import FileStore

logger = logging.getLogger("sentinel.worker")
engine = create_database_engine()
make_session = session_factory(engine)
P = ParamSpec("P")
R = TypeVar("R")


def registered_task(*, name: str, **options: Any) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Preserve Python signatures while registering tasks with Celery."""

    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        shared_task(name=name, **options)(function)
        return function

    return decorate


@registered_task(name="sentinel.dispatch_outbox")
def outbox_tick() -> int:
    with make_session() as session:
        return dispatch_outbox(session, celery_app)


@registered_task(name="sentinel.dispatch_schedules")
def schedule_tick() -> int:
    with make_session() as session:
        return dispatch_due_schedules(session)


@registered_task(name="sentinel.recover_stale_jobs")
def recover_stale_jobs() -> int:
    cutoff = utcnow() - timedelta(seconds=get_settings().stale_job_seconds)
    recovered = 0
    with make_session() as session, session.begin():
        stale = list(
            session.scalars(
                select(JobRow)
                .where(JobRow.status == JobStatus.RUNNING.value, JobRow.started_at < cutoff)
                .with_for_update(skip_locked=True)
            )
        )
        for job in stale:
            job.status = transition_job(JobStatus(job.status), JobStatus.QUEUED).value
            job.started_at = None
            job.progress = {"stage": "recovered_after_worker_interruption"}
            job.error_class = "WorkerInterrupted"
            job.error_message = "Worker stopped before completing this job; execution was requeued."
            outbox = session.scalar(
                select(OutboxRow).where(OutboxRow.job_id == job.id).with_for_update()
            )
            if outbox is not None:
                outbox.published_at = None
                outbox.last_error = None
            else:
                session.add(OutboxRow(job_id=job.id, task_name=_task_name(JobType(job.job_type))))
            recovered += 1
    return recovered


@registered_task(name="sentinel.run_ingest_job", acks_late=True)
def run_ingest_job(job_id: str) -> None:
    _execute(UUID(job_id))


@registered_task(name="sentinel.run_quality_job", acks_late=True)
def run_quality_job(job_id: str) -> None:
    _execute(UUID(job_id))


@registered_task(name="sentinel.run_reliability_job", acks_late=True)
def run_reliability_job(job_id: str) -> None:
    _execute(UUID(job_id))


@registered_task(name="sentinel.run_incidents_job", acks_late=True)
def run_incidents_job(job_id: str) -> None:
    _execute(UUID(job_id))


@registered_task(name="sentinel.run_monitor_job", acks_late=True)
def run_monitor_job(job_id: str) -> None:
    _execute(UUID(job_id))


@registered_task(name="sentinel.run_ai_investigation_job", acks_late=True)
def run_ai_investigation_job(job_id: str) -> None:
    _execute(UUID(job_id))


def _execute(job_id: UUID) -> None:
    """Claim once, execute with existing services, and persist bounded retries/failures."""
    with make_session() as session, session.begin():
        job = session.scalar(select(JobRow).where(JobRow.id == job_id).with_for_update())
        if job is None or job.status in {JobStatus.SUCCEEDED.value, JobStatus.FAILED.value}:
            return
        if job.status == JobStatus.RUNNING.value:
            return
        job.status = transition_job(JobStatus(job.status), JobStatus.RUNNING).value
        job.started_at = utcnow()
        job.attempt_count += 1
        job.progress = {"stage": "running", "attempt": job.attempt_count}
        job.error_class = None
        job.error_message = None
        kind = JobType(job.job_type)
        payload = dict(job.payload)
    try:
        result = _run_service(kind, job_id, payload)
    except Exception as exc:
        if _finish_failure(job_id, exc) and kind == JobType.INGEST:
            _remove_staged_upload(payload)
        return
    with make_session() as session, session.begin():
        job = session.scalar(select(JobRow).where(JobRow.id == job_id).with_for_update())
        if job is None or job.status != JobStatus.RUNNING.value:
            return
        job.status = transition_job(JobStatus(job.status), JobStatus.SUCCEEDED).value
        job.completed_at = utcnow()
        job.progress = {"stage": "complete"}
        job.result_metadata = result
        _schedule_outcome(session, job, "SUCCEEDED")
    if kind == JobType.INGEST:
        _remove_staged_upload(payload)


def _run_service(kind: JobType, job_id: UUID, payload: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    storage = FileStore(Path(settings.data_dir))
    with make_session() as session:
        if kind == JobType.INGEST:
            ingest_request = DatasetInput.model_validate(payload["dataset"])
            registration = DatasetService(session, storage).ingest(
                Path(str(payload["source_path"])), ingest_request
            )
            return {
                "dataset_id": str(registration.dataset.id),
                "dataset_version_id": str(registration.version.id),
                "version_number": registration.version.version_number,
            }
        if kind == JobType.QUALITY:
            quality_result = QualityService(session, storage).evaluate_version(
                UUID(payload["version_id"])
            )
            if payload.get("incident_policy_id"):
                IncidentService(session).process_quality_assessment(
                    quality_result.assessment.id, UUID(payload["incident_policy_id"])
                )
            return {
                "assessment_id": str(quality_result.assessment.id),
                "status": quality_result.assessment.status.value,
            }
        if kind == JobType.RELIABILITY:
            reliability_request = ReliabilityAssessmentInput.model_validate(payload["request"])
            reliability_result = ReliabilityService(session, storage).run_assessment(
                reliability_request
            )
            if payload.get("incident_policy_id"):
                IncidentService(session).process_reliability_assessment(
                    reliability_result.assessment.id, UUID(payload["incident_policy_id"])
                )
            return {
                "assessment_id": str(reliability_result.assessment.id),
                "status": reliability_result.assessment.status.value,
            }
        if kind == JobType.INCIDENTS:
            service = IncidentService(session)
            if payload["source_type"] == "quality":
                incident_result = service.process_quality_assessment(
                    UUID(payload["assessment_id"]), UUID(payload["policy_id"])
                )
            else:
                incident_result = service.process_reliability_assessment(
                    UUID(payload["assessment_id"]), UUID(payload["policy_id"])
                )
            return {
                "assessment_id": str(incident_result.processed_assessment_id),
                "incidents": len(incident_result.incidents),
            }
        if kind == JobType.MONITOR:
            return _run_monitor(session, job_id, payload, storage)
        if kind == JobType.AI_INVESTIGATION:
            from sentinel.ai.service import run_investigation

            return run_investigation(session, job_id, UUID(payload["incident_id"]), payload)
    raise ValueError(f"Unsupported job type: {kind}")


def _run_monitor(
    session: Session, job_id: UUID, payload: dict[str, Any], storage: FileStore
) -> dict[str, Any]:
    schedule = session.get(MonitoringScheduleRow, UUID(payload["schedule_id"]))
    if schedule is None:
        raise ValueError("Monitoring schedule no longer exists")
    version = session.scalar(
        select(DatasetVersionRow)
        .where(DatasetVersionRow.dataset_id == schedule.dataset_id)
        .order_by(DatasetVersionRow.version_number.desc())
        .limit(1)
    )
    if version is None:
        raise ValueError("Scheduled dataset has no ingested versions")
    result: dict[str, Any] = {"dataset_version_id": str(version.id)}
    session.commit()
    quality = QualityService(session, storage).evaluate_version(version.id)
    result["quality_assessment_id"] = str(quality.assessment.id)
    if schedule.reliability_policy_id:
        reliability = ReliabilityService(session, storage).run_assessment(
            ReliabilityAssessmentInput(
                dataset_version_id=version.id,
                policy_id=schedule.reliability_policy_id,
                baseline_strategy=BaselineStrategy.PREVIOUS,
            )
        )
        result["reliability_assessment_id"] = str(reliability.assessment.id)
        if schedule.incident_policy_id:
            IncidentService(session).process_reliability_assessment(
                reliability.assessment.id, schedule.incident_policy_id
            )
    if schedule.incident_policy_id:
        IncidentService(session).process_quality_assessment(
            quality.assessment.id, schedule.incident_policy_id
        )
    with session.begin():
        schedule = session.scalar(
            select(MonitoringScheduleRow)
            .where(MonitoringScheduleRow.id == schedule.id)
            .with_for_update()
        )
        if schedule is not None:
            schedule.last_outcome = "SUCCEEDED"
        job = session.get(JobRow, job_id)
        if job is not None:
            _schedule_outcome(session, job, "SUCCEEDED")
    return result


def _finish_failure(job_id: UUID, exc: Exception) -> bool:
    settings = get_settings()
    transient = isinstance(exc, (TimeoutError, ConnectionError, SQLAlchemyError))
    with make_session() as session, session.begin():
        job = session.scalar(select(JobRow).where(JobRow.id == job_id).with_for_update())
        if job is None:
            return False
        job.error_class = type(exc).__name__
        if transient and job.attempt_count <= settings.task_max_retries:
            job.status = transition_job(JobStatus(job.status), JobStatus.QUEUED).value
            job.started_at = None
            job.next_retry_at = utcnow() + timedelta(
                seconds=settings.task_retry_delay_seconds * job.attempt_count
            )
            job.progress = {"stage": "retry_pending", "attempt": job.attempt_count}
            outbox = session.scalar(
                select(OutboxRow).where(OutboxRow.job_id == job.id).with_for_update()
            )
            if outbox is not None:
                outbox.published_at = None
                outbox.last_error = type(exc).__name__
        else:
            job.status = transition_job(JobStatus(job.status), JobStatus.FAILED).value
            job.completed_at = utcnow()
            job.error_message = _safe_error(exc)
            job.progress = {"stage": "failed"}
            _schedule_outcome(session, job, "FAILED")
    logger.error(
        "job execution failed", extra={"job_id": str(job_id), "error_class": type(exc).__name__}
    )
    return not (transient and job is not None and job.attempt_count <= settings.task_max_retries)


def _remove_staged_upload(payload: dict[str, Any]) -> None:
    source = payload.get("source_path")
    if not isinstance(source, str):
        return
    incoming = (Path(get_settings().data_dir) / "incoming").resolve()
    candidate = Path(source).resolve()
    if not candidate.is_relative_to(incoming) or candidate == incoming:
        return
    candidate.unlink(missing_ok=True)
    try:
        candidate.parent.rmdir()
    except OSError:
        pass


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, (ValueError, TypeError)):
        return "The operation was rejected because its input or referenced state is invalid."
    return "The operation failed. Inspect server logs using the correlation identifier."


def _schedule_outcome(session: Session, job: JobRow, outcome: str) -> None:
    schedule_id = job.payload.get("schedule_id")
    if schedule_id:
        occurrence = datetime.fromisoformat(job.payload["occurrence_at"])
        execution = session.scalar(
            select(ScheduleExecutionRow).where(
                ScheduleExecutionRow.schedule_id == UUID(schedule_id),
                ScheduleExecutionRow.occurrence_at == occurrence,
            )
        )
        if execution is not None:
            execution.outcome = outcome
        schedule = session.get(MonitoringScheduleRow, UUID(schedule_id))
        if schedule is not None:
            schedule.last_outcome = outcome


def _task_name(kind: JobType) -> str:
    return f"sentinel.run_{kind.value}_job"
