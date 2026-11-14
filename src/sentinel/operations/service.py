"""Transactional job enqueueing, outbox publication, and schedule dispatch."""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sentinel.domain.exceptions import DatasetNotFoundError
from sentinel.incidents.models import IncidentPolicyRow
from sentinel.operations.contracts import (
    JobPage,
    JobStatus,
    JobType,
    JobView,
    ScheduleExecutionView,
    ScheduleInput,
    ScheduleView,
)
from sentinel.operations.models import (
    JobRow,
    MonitoringScheduleRow,
    OutboxRow,
    ScheduleExecutionRow,
    utcnow,
)
from sentinel.persistence.models import DatasetRow
from sentinel.reliability.models import DetectionPolicyRow

TASKS = {kind.value: f"sentinel.run_{kind.value}_job" for kind in JobType}


class OperationsService:
    """Persist requests before asynchronously publishing them via the outbox."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def enqueue(
        self,
        job_type: JobType,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
        dataset_id: UUID | None = None,
        dataset_version_id: UUID | None = None,
        policy_id: UUID | None = None,
        incident_id: UUID | None = None,
        correlation_id: str | None = None,
    ) -> JobView:
        if not idempotency_key or len(idempotency_key) > 300:
            raise ValueError("idempotency_key must be 1 to 300 characters")
        try:
            with self.session.begin():
                existing = self.session.scalar(
                    select(JobRow).where(JobRow.idempotency_key == idempotency_key)
                )
                if existing is not None:
                    return _job_view(existing)
                job = JobRow(
                    job_type=job_type.value,
                    status=JobStatus.QUEUED.value,
                    payload=payload,
                    dataset_id=dataset_id,
                    dataset_version_id=dataset_version_id,
                    policy_id=policy_id,
                    incident_id=incident_id,
                    idempotency_key=idempotency_key,
                    correlation_id=correlation_id or str(uuid4()),
                    progress={"stage": "queued"},
                )
                self.session.add(job)
                self.session.flush()
                self.session.add(OutboxRow(job_id=job.id, task_name=TASKS[job_type.value]))
                self.session.flush()
                return _job_view(job)
        except IntegrityError:
            existing = self.session.scalar(
                select(JobRow).where(JobRow.idempotency_key == idempotency_key)
            )
            if existing is None:
                raise
            return _job_view(existing)

    def get_job(self, job_id: UUID) -> JobView:
        row = self.session.get(JobRow, job_id)
        if row is None:
            raise ValueError(f"Job not found: {job_id}")
        return _job_view(row)

    def list_jobs(
        self,
        *,
        status: JobStatus | None = None,
        job_type: JobType | None = None,
        dataset_id: UUID | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> JobPage:
        if offset < 0 or not 1 <= limit <= 200:
            raise ValueError("offset must be non-negative and limit must be between 1 and 200")
        query = select(JobRow)
        count = select(func.count()).select_from(JobRow)
        filters = []
        if status:
            filters.append(JobRow.status == status.value)
        if job_type:
            filters.append(JobRow.job_type == job_type.value)
        if dataset_id:
            filters.append(JobRow.dataset_id == dataset_id)
        query = query.where(*filters)
        count = count.where(*filters)
        items = self.session.scalars(
            query.order_by(JobRow.created_at.desc(), JobRow.id).offset(offset).limit(limit)
        )
        return JobPage(
            items=[_job_view(item) for item in items],
            total=self.session.scalar(count) or 0,
            offset=offset,
            limit=limit,
        )

    def create_schedule(self, data: ScheduleInput) -> ScheduleView:
        with self.session.begin():
            self._validate_schedule_refs(data)
            now = utcnow()
            row = MonitoringScheduleRow(
                dataset_id=data.dataset_id,
                reliability_policy_id=data.reliability_policy_id,
                incident_policy_id=data.incident_policy_id,
                interval_seconds=data.interval_seconds,
                enabled=True,
                next_run_at=now + timedelta(seconds=data.interval_seconds),
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)
            self.session.flush()
            return _schedule_view(row)

    def list_schedules(self, dataset_id: UUID | None = None) -> list[ScheduleView]:
        query = select(MonitoringScheduleRow).where(MonitoringScheduleRow.deleted_at.is_(None))
        if dataset_id:
            query = query.where(MonitoringScheduleRow.dataset_id == dataset_id)
        return [
            _schedule_view(row)
            for row in self.session.scalars(
                query.order_by(MonitoringScheduleRow.next_run_at, MonitoringScheduleRow.id)
            )
        ]

    def update_schedule(self, schedule_id: UUID, data: ScheduleInput) -> ScheduleView:
        with self.session.begin():
            row = self._schedule(schedule_id, lock=True)
            self._validate_schedule_refs(data)
            row.dataset_id = data.dataset_id
            row.reliability_policy_id = data.reliability_policy_id
            row.incident_policy_id = data.incident_policy_id
            row.interval_seconds = data.interval_seconds
            row.next_run_at = utcnow() + timedelta(seconds=data.interval_seconds)
            row.updated_at = utcnow()
            self.session.flush()
            return _schedule_view(row)

    def set_schedule_enabled(self, schedule_id: UUID, enabled: bool) -> ScheduleView:
        with self.session.begin():
            row = self._schedule(schedule_id, lock=True)
            row.enabled = enabled
            row.updated_at = utcnow()
            return _schedule_view(row)

    def delete_schedule(self, schedule_id: UUID) -> None:
        with self.session.begin():
            row = self._schedule(schedule_id, lock=True)
            row.enabled = False
            row.deleted_at = utcnow()
            row.updated_at = utcnow()

    def schedule_history(self, schedule_id: UUID) -> list[ScheduleExecutionView]:
        return [
            _execution_view(row)
            for row in self.session.scalars(
                select(ScheduleExecutionRow)
                .where(ScheduleExecutionRow.schedule_id == schedule_id)
                .order_by(ScheduleExecutionRow.occurrence_at.desc(), ScheduleExecutionRow.id)
            )
        ]

    def _validate_schedule_refs(self, data: ScheduleInput) -> None:
        if self.session.get(DatasetRow, data.dataset_id) is None:
            raise DatasetNotFoundError(f"Dataset not found: {data.dataset_id}")
        if data.reliability_policy_id is not None:
            policy = self.session.get(DetectionPolicyRow, data.reliability_policy_id)
            if policy is None or policy.dataset_id != data.dataset_id:
                raise ValueError("reliability policy must belong to the scheduled dataset")
        if data.incident_policy_id is not None:
            incident_policy = self.session.get(IncidentPolicyRow, data.incident_policy_id)
            if incident_policy is None or incident_policy.dataset_id != data.dataset_id:
                raise ValueError("incident policy must belong to the scheduled dataset")

    def _schedule(self, schedule_id: UUID, *, lock: bool = False) -> MonitoringScheduleRow:
        query = select(MonitoringScheduleRow).where(MonitoringScheduleRow.id == schedule_id)
        if lock:
            query = query.with_for_update()
        row = self.session.scalar(query)
        if row is None or row.deleted_at is not None:
            raise ValueError(f"Monitoring schedule not found: {schedule_id}")
        return row


def dispatch_outbox(session: Session, sender: Any, *, batch_size: int = 100) -> int:
    """Publish pending rows. A crash after publish causes safe duplicate delivery."""
    dispatched = 0
    with session.begin():
        rows = list(
            session.scalars(
                select(OutboxRow)
                .join(JobRow, JobRow.id == OutboxRow.job_id)
                .where(
                    OutboxRow.published_at.is_(None),
                    (JobRow.next_retry_at.is_(None) | (JobRow.next_retry_at <= utcnow())),
                )
                .order_by(OutboxRow.created_at, OutboxRow.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        )
        for row in rows:
            row.attempts += 1
            try:
                sender.send_task(row.task_name, args=[str(row.job_id)])
            except Exception as exc:
                row.last_error = type(exc).__name__
            else:
                row.published_at = utcnow()
                row.last_error = None
                dispatched += 1
    return dispatched


def dispatch_due_schedules(session: Session, *, now: datetime | None = None) -> int:
    """Create one stable job per due occurrence and advance schedules under row locks."""
    current = now or utcnow()
    created = 0
    with session.begin():
        rows = list(
            session.scalars(
                select(MonitoringScheduleRow)
                .where(
                    MonitoringScheduleRow.enabled.is_(True),
                    MonitoringScheduleRow.next_run_at <= current,
                )
                .order_by(MonitoringScheduleRow.next_run_at, MonitoringScheduleRow.id)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        )
        for schedule in rows:
            occurrence = schedule.next_run_at
            key = f"schedule:{schedule.id}:{occurrence.isoformat()}"
            job = session.scalar(select(JobRow).where(JobRow.idempotency_key == key))
            if job is None:
                job = JobRow(
                    job_type=JobType.MONITOR.value,
                    status=JobStatus.QUEUED.value,
                    payload={
                        "schedule_id": str(schedule.id),
                        "occurrence_at": occurrence.isoformat(),
                    },
                    dataset_id=schedule.dataset_id,
                    policy_id=schedule.reliability_policy_id,
                    idempotency_key=key,
                    correlation_id=str(uuid4()),
                    progress={"stage": "scheduled"},
                )
                session.add(job)
                session.flush()
                session.add(OutboxRow(job_id=job.id, task_name=TASKS[JobType.MONITOR.value]))
                session.add(
                    ScheduleExecutionRow(
                        schedule_id=schedule.id,
                        occurrence_at=occurrence,
                        job_id=job.id,
                        outcome="QUEUED",
                    )
                )
                created += 1
            schedule.last_run_at = occurrence
            schedule.last_outcome = "QUEUED"
            while schedule.next_run_at <= current:
                schedule.next_run_at += timedelta(seconds=schedule.interval_seconds)
            schedule.updated_at = current
    return created


def _job_view(row: JobRow) -> JobView:
    return JobView(
        id=row.id,
        job_type=JobType(row.job_type),
        status=JobStatus(row.status),
        dataset_id=row.dataset_id,
        dataset_version_id=row.dataset_version_id,
        policy_id=row.policy_id,
        incident_id=row.incident_id,
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        attempt_count=row.attempt_count,
        progress=row.progress,
        error_class=row.error_class,
        error_message=row.error_message,
        result_metadata=row.result_metadata,
        correlation_id=row.correlation_id,
    )


def _schedule_view(row: MonitoringScheduleRow) -> ScheduleView:
    return ScheduleView.model_validate(row, from_attributes=True)


def _execution_view(row: ScheduleExecutionRow) -> ScheduleExecutionView:
    return ScheduleExecutionView.model_validate(row, from_attributes=True)
