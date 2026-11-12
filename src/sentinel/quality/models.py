"""Persistence mappings for quality rules and historical evaluations."""

from __future__ import annotations

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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sentinel.persistence.models import Base


class QualityRuleRow(Base):
    """Mutable dataset-specific rule definition."""

    __tablename__ = "quality_rules"
    __table_args__ = (
        UniqueConstraint("dataset_id", "name", name="uq_quality_rule_dataset_name"),
        Index("ix_quality_rules_dataset_enabled", "dataset_id", "enabled"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    dataset_id: Mapped[UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(40), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
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
    findings: Mapped[list[QualityFindingRow]] = relationship(back_populates="rule")


class QualityAssessmentRow(Base):
    """Immutable ruleset evaluation for one dataset version."""

    __tablename__ = "quality_assessments"
    __table_args__ = (
        UniqueConstraint(
            "dataset_version_id",
            "ruleset_fingerprint",
            name="uq_quality_assessment_version_ruleset",
        ),
        CheckConstraint(
            "total_rules = passed_rules + failed_rules + error_rules",
            name="ck_quality_assessment_counts",
        ),
        Index("ix_quality_assessments_version_evaluated", "dataset_version_id", "evaluated_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    dataset_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), nullable=False
    )
    ruleset_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    rules_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    total_rules: Mapped[int] = mapped_column(Integer, nullable=False)
    passed_rules: Mapped[int] = mapped_column(Integer, nullable=False)
    failed_rules: Mapped[int] = mapped_column(Integer, nullable=False)
    error_rules: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    message: Mapped[str | None] = mapped_column(Text)
    findings: Mapped[list[QualityFindingRow]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan"
    )


class QualityFindingRow(Base):
    """One immutable outcome and evidence record for an evaluated rule."""

    __tablename__ = "quality_findings"
    __table_args__ = (
        Index("ix_quality_findings_assessment", "assessment_id"),
        Index("ix_quality_findings_rule", "rule_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    assessment_id: Mapped[UUID] = mapped_column(
        ForeignKey("quality_assessments.id", ondelete="CASCADE"), nullable=False
    )
    rule_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("quality_rules.id", ondelete="SET NULL")
    )
    rule_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    evaluated_records: Mapped[int | None] = mapped_column(Integer)
    violating_records: Mapped[int | None] = mapped_column(Integer)
    violation_percentage: Mapped[float | None] = mapped_column()
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    assessment: Mapped[QualityAssessmentRow] = relationship(back_populates="findings")
    rule: Mapped[QualityRuleRow | None] = relationship(back_populates="findings")
