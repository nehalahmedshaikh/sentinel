"""Validated inputs and outputs for incident management."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class IncidentStatus(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    INVESTIGATING = "INVESTIGATING"
    RESOLVED = "RESOLVED"
    REOPENED = "REOPENED"


class IncidentSeverity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFORMATIONAL = "INFORMATIONAL"


class SourceType(StrEnum):
    QUALITY = "quality"
    RELIABILITY = "reliability"


class IncidentGenerationInput(BaseModel):
    """Request persistent incident qualification for a completed assessment."""

    model_config = ConfigDict(extra="forbid")
    source_type: SourceType
    assessment_id: UUID
    policy_id: UUID


class IncidentPolicyConfig(BaseModel):
    """Eligibility and conservative correlation/severity thresholds."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    finding_types: list[str] = Field(default_factory=list)
    minimum_source_severities: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "quality": ["ERROR", "CRITICAL"],
            "reliability": ["ERROR", "CRITICAL"],
        }
    )
    correlation_window_hours: int = Field(default=72, ge=1, le=8760)
    correlation_minimum_column_overlap: float = Field(default=1.0, gt=0, le=1)
    critical_affected_records: int = Field(default=10000, ge=1)
    high_affected_records: int = Field(default=1000, ge=1)
    critical_affected_columns: int = Field(default=10, ge=1)
    high_affected_columns: int = Field(default=5, ge=1)
    critical_drift_magnitude: float = Field(default=0.5, ge=0, allow_inf_nan=False)
    high_drift_magnitude: float = Field(default=0.2, ge=0, allow_inf_nan=False)
    recurrence_high_count: int = Field(default=3, ge=2)


class IncidentPolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    config: IncidentPolicyConfig = Field(default_factory=IncidentPolicyConfig)
    enabled: bool = True


class IncidentPolicyView(BaseModel):
    id: UUID
    dataset_id: UUID
    name: str
    config: IncidentPolicyConfig
    fingerprint: str
    enabled: bool


class IncidentView(BaseModel):
    id: UUID
    reference: str
    dataset_id: UUID
    dataset_slug: str
    title: str
    description: str
    finding_category: str
    status: IncidentStatus
    severity: IncidentSeverity
    affected_columns: list[str]
    first_observed_at: datetime
    last_observed_at: datetime
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    correlation_fingerprint: str
    merged_into_id: UUID | None = None


class IncidentEvidenceView(BaseModel):
    id: UUID
    incident_id: UUID
    source_type: SourceType
    assessment_id: UUID
    finding_id: UUID
    dataset_version_id: UUID
    finding_type: str
    source_severity: str
    affected_columns: list[str]
    metadata: dict[str, Any]
    associated_at: datetime


class IncidentEventView(BaseModel):
    id: UUID
    incident_id: UUID
    event_type: str
    occurred_at: datetime
    previous_value: Any = None
    new_value: Any = None
    details: dict[str, Any]


class IncidentDetail(BaseModel):
    incident: IncidentView
    evidence: list[IncidentEvidenceView]
    events: list[IncidentEventView]


class GenerationDecision(BaseModel):
    source_type: SourceType
    finding_id: UUID
    qualifies: bool
    reason: str
    incident_id: UUID | None = None


class GenerationResult(BaseModel):
    processed_assessment_id: UUID
    source_type: SourceType
    decisions: list[GenerationDecision]
    incidents: list[IncidentView]


class NoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    note: str = Field(min_length=1, max_length=10000)
    author: str | None = Field(default=None, max_length=200)


class ResolveInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    summary: str = Field(min_length=1, max_length=10000)
    reason: str = Field(min_length=1, max_length=5000)


class SeverityInput(BaseModel):
    severity: IncidentSeverity
    reason: str = Field(min_length=1, max_length=5000)


class MergeInput(BaseModel):
    surviving_incident_id: UUID
    reason: str = Field(min_length=1, max_length=5000)


class LinkInput(BaseModel):
    related_incident_id: UUID
    reason: str = Field(min_length=1, max_length=5000)


class ReportFact(BaseModel):
    category: str
    statement: str
    evidence_ids: list[UUID] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class InvestigationReport(BaseModel):
    incident: IncidentView
    evidence: list[IncidentEvidenceView]
    timeline: list[IncidentEventView]
    affected_versions: list[UUID]
    affected_columns: list[str]
    observed_facts: list[ReportFact]
    statistical_evidence: list[ReportFact]
    correlations: list[ReportFact]
    hypotheses: list[str]
    missing_evidence: list[str]
    resolution: dict[str, Any] | None


class HistoricalSummary(BaseModel):
    dataset_id: UUID
    incident_count: int
    by_status: dict[str, int]
    by_severity: dict[str, int]
    affected_columns: dict[str, int]
    finding_categories: dict[str, int]
    reopened_incidents: int
    repeated_version_failures: dict[str, int]
    severity_escalations: int
    incident_ids: list[UUID]
