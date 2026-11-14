"""Validated contracts for durable jobs and monitoring schedules."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class JobType(StrEnum):
    INGEST = "ingest"
    QUALITY = "quality"
    RELIABILITY = "reliability"
    INCIDENTS = "incidents"
    MONITOR = "monitor"
    AI_INVESTIGATION = "ai_investigation"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


JOB_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.QUEUED: {JobStatus.RUNNING},
    JobStatus.RUNNING: {JobStatus.QUEUED, JobStatus.SUCCEEDED, JobStatus.FAILED},
    JobStatus.SUCCEEDED: set(),
    JobStatus.FAILED: set(),
}


def transition_job(current: JobStatus, target: JobStatus) -> JobStatus:
    """Validate a job state transition and return its new state."""
    if target not in JOB_TRANSITIONS[current]:
        raise ValueError(f"Invalid job transition: {current.value} -> {target.value}")
    return target


class JobView(BaseModel):
    id: UUID
    job_type: JobType
    status: JobStatus
    dataset_id: UUID | None
    dataset_version_id: UUID | None
    policy_id: UUID | None
    incident_id: UUID | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    attempt_count: int
    progress: dict[str, Any]
    error_class: str | None
    error_message: str | None
    result_metadata: dict[str, Any] | None
    correlation_id: str


class JobPage(BaseModel):
    items: list[JobView]
    total: int
    offset: int
    limit: int


class ScheduleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: UUID
    reliability_policy_id: UUID | None = None
    incident_policy_id: UUID | None = None
    interval_seconds: int = Field(ge=60, le=31_536_000)


class ScheduleView(BaseModel):
    id: UUID
    dataset_id: UUID
    reliability_policy_id: UUID | None
    incident_policy_id: UUID | None
    interval_seconds: int
    enabled: bool
    next_run_at: datetime
    last_run_at: datetime | None
    last_outcome: str | None
    created_at: datetime
    updated_at: datetime


class ScheduleExecutionView(BaseModel):
    id: UUID
    schedule_id: UUID
    occurrence_at: datetime
    job_id: UUID
    outcome: str
    created_at: datetime


class OutboxStats(BaseModel):
    pending: int
    published: int
