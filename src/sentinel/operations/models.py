"""Persistence mappings for background jobs, schedules, and AI investigations."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from sentinel.persistence.models import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class JobRow(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_jobs_idempotency_key"),
        CheckConstraint(
            "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')", name="ck_jobs_status"
        ),
        Index("ix_jobs_status_created", "status", "created_at"),
        Index("ix_jobs_dataset_created", "dataset_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="QUEUED")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    dataset_id: Mapped[UUID | None] = mapped_column(ForeignKey("datasets.id", ondelete="SET NULL"))
    dataset_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="SET NULL")
    )
    policy_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    incident_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error_class: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    result_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    idempotency_key: Mapped[str] = mapped_column(String(300), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(100), nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutboxRow(Base):
    __tablename__ = "job_outbox"
    __table_args__ = (Index("ix_job_outbox_pending", "published_at", "created_at"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    task_name: Mapped[str] = mapped_column(String(150), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


class MonitoringScheduleRow(Base):
    __tablename__ = "monitoring_schedules"
    __table_args__ = (
        CheckConstraint("interval_seconds >= 60", name="ck_monitoring_interval_minimum"),
        Index("ix_monitoring_schedules_due", "enabled", "next_run_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    dataset_id: Mapped[UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    reliability_policy_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("detection_policies.id", ondelete="RESTRICT")
    )
    incident_policy_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("incident_policies.id", ondelete="RESTRICT")
    )
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_outcome: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScheduleExecutionRow(Base):
    __tablename__ = "schedule_executions"
    __table_args__ = (
        UniqueConstraint("schedule_id", "occurrence_at", name="uq_schedule_occurrence"),
        Index("ix_schedule_executions_history", "schedule_id", "occurrence_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    schedule_id: Mapped[UUID] = mapped_column(
        ForeignKey("monitoring_schedules.id", ondelete="CASCADE"), nullable=False
    )
    occurrence_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False, default="QUEUED")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class AIInvestigationRow(Base):
    __tablename__ = "ai_investigations"
    __table_args__ = (Index("ix_ai_investigations_incident_created", "incident_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    incident_id: Mapped[UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False
    )
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="QUEUED")
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    provider: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(200))
    failure_class: Mapped[str | None] = mapped_column(String(100))
    failure_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
