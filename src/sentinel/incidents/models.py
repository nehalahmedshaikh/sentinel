"""SQLAlchemy persistence mappings for incidents, evidence, and event history."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from sentinel.persistence.models import Base


class IncidentPolicyRow(Base):
    __tablename__ = "incident_policies"
    __table_args__ = (
        UniqueConstraint("dataset_id", "name", name="uq_incident_policy_dataset_name"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    dataset_id: Mapped[UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)


class IncidentRow(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        Index("ix_incidents_dataset_status_severity", "dataset_id", "status", "severity"),
        Index("ix_incidents_dataset_observed", "dataset_id", "last_observed_at"),
        Index(
            "ix_incidents_correlation_candidates",
            "dataset_id",
            "finding_category",
            "status",
            "last_observed_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    reference: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    dataset_id: Mapped[UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    finding_category: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    severity_decision: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    affected_columns: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    correlation_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    merged_into_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="RESTRICT")
    )


class IncidentEvidenceRow(Base):
    __tablename__ = "incident_evidence"
    __table_args__ = (
        UniqueConstraint("source_type", "finding_id", name="uq_incident_evidence_source_finding"),
        Index("ix_incident_evidence_incident_associated", "incident_id", "associated_at"),
        Index("ix_incident_evidence_version", "dataset_version_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    incident_id: Mapped[UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    assessment_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    finding_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    quality_assessment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("quality_assessments.id", ondelete="RESTRICT")
    )
    quality_finding_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("quality_findings.id", ondelete="RESTRICT")
    )
    reliability_assessment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("reliability_assessments.id", ondelete="RESTRICT")
    )
    reliability_finding_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("reliability_findings.id", ondelete="RESTRICT")
    )
    dataset_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="RESTRICT"), nullable=False
    )
    finding_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_severity: Mapped[str] = mapped_column(String(30), nullable=False)
    affected_columns: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    evidence_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    associated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class IncidentEventRow(Base):
    __tablename__ = "incident_events"
    __table_args__ = (
        Index("ix_incident_events_incident_time", "incident_id", "occurred_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    incident_id: Mapped[UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    previous_value: Mapped[Any | None] = mapped_column(JSONB)
    new_value: Mapped[Any | None] = mapped_column(JSONB)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class IncidentGenerationDecisionRow(Base):
    __tablename__ = "incident_generation_decisions"
    __table_args__ = (
        UniqueConstraint("source_type", "finding_id", name="uq_incident_decision_source_finding"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    assessment_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    finding_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    quality_assessment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("quality_assessments.id", ondelete="RESTRICT")
    )
    quality_finding_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("quality_findings.id", ondelete="RESTRICT")
    )
    reliability_assessment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("reliability_assessments.id", ondelete="RESTRICT")
    )
    reliability_finding_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("reliability_findings.id", ondelete="RESTRICT")
    )
    qualifies: Mapped[bool] = mapped_column(nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    incident_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
