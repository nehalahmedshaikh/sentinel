"""SQLAlchemy persistence for reliability policies and evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sentinel.persistence.models import Base, DatasetVersionRow


class DetectionPolicyRow(Base):
    """Mutable dataset-specific detector configuration."""

    __tablename__ = "detection_policies"
    __table_args__ = (
        UniqueConstraint("dataset_id", "name", name="uq_detection_policy_dataset_name"),
        Index("ix_detection_policies_dataset_enabled", "dataset_id", "enabled"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    dataset_id: Mapped[UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class ReliabilityAssessmentRow(Base):
    """Immutable evaluation against resolved historical baseline versions."""

    __tablename__ = "reliability_assessments"
    __table_args__ = (
        UniqueConstraint(
            "target_version_id", "identity_fingerprint", name="uq_reliability_assessment_identity"
        ),
        Index("ix_reliability_assessments_dataset_target", "dataset_id", "target_version_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    dataset_id: Mapped[UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    target_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), nullable=False
    )
    baseline_strategy: Mapped[str] = mapped_column(String(20), nullable=False)
    baseline_versions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    policy_id: Mapped[UUID] = mapped_column(
        ForeignKey("detection_policies.id", ondelete="RESTRICT"), nullable=False
    )
    policy_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    identity_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    finding_count: Mapped[int] = mapped_column(nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    message: Mapped[str | None] = mapped_column(Text)
    target_version: Mapped[DatasetVersionRow] = relationship()
    findings: Mapped[list[ReliabilityFindingRow]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan"
    )


class ReliabilityFindingRow(Base):
    """Immutable detector outcome with bounded structured evidence."""

    __tablename__ = "reliability_findings"
    __table_args__ = (
        Index("ix_reliability_findings_assessment", "assessment_id"),
        Index("ix_reliability_findings_detector_column", "detector", "column_name"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    assessment_id: Mapped[UUID] = mapped_column(
        ForeignKey("reliability_assessments.id", ondelete="CASCADE"), nullable=False
    )
    detector: Mapped[str] = mapped_column(String(50), nullable=False)
    finding_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    column_name: Mapped[str | None] = mapped_column(String(250))
    baseline_value: Mapped[Any | None] = mapped_column(JSONB)
    target_value: Mapped[Any | None] = mapped_column(JSONB)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    assessment: Mapped[ReliabilityAssessmentRow] = relationship(back_populates="findings")
