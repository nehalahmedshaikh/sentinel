"""Validated public contracts for reliability policies and assessments."""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _default_multivariate_detectors() -> list[Literal["isolation_forest", "local_outlier_factor"]]:
    return ["isolation_forest", "local_outlier_factor"]


class BaselineStrategy(StrEnum):
    """How historical comparison versions are selected."""

    PREVIOUS = "previous"
    INITIAL = "initial"
    ROLLING = "rolling"
    EXPLICIT = "explicit"


class AssessmentStatus(StrEnum):
    """Overall reliability analysis completion state."""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    ERROR = "ERROR"
    NO_BASELINE = "NO_BASELINE"


class FindingStatus(StrEnum):
    """Detector outcome; unavailable evidence is distinct from no change."""

    DETECTED = "DETECTED"
    NOT_DETECTED = "NOT_DETECTED"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"


class Severity(StrEnum):
    """Operational importance of a reliability finding."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class SchemaPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = True
    added_severity: Severity = Severity.WARNING
    removed_severity: Severity = Severity.ERROR
    type_change_severity: Severity = Severity.ERROR


class NumericDriftPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = True
    ks_alpha: float = Field(default=0.05, gt=0, lt=1, allow_inf_nan=False)
    ks_statistic_threshold: float = Field(default=0.1, ge=0, le=1, allow_inf_nan=False)
    psi_threshold: float = Field(default=0.2, ge=0, allow_inf_nan=False)
    wasserstein_threshold: float = Field(default=1.0, ge=0, allow_inf_nan=False)
    bin_count: int = Field(default=10, ge=2, le=100)
    minimum_samples: int = Field(default=20, ge=2)
    severity: Severity = Severity.WARNING


class CategoricalDriftPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = True
    js_divergence_threshold: float = Field(default=0.1, ge=0, le=1, allow_inf_nan=False)
    psi_threshold: float = Field(default=0.2, ge=0, allow_inf_nan=False)
    minimum_samples: int = Field(default=10, ge=1)
    severity: Severity = Severity.WARNING


class UnivariateAnomalyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = True
    numeric_method: Literal["iqr", "modified_z_score"] = "iqr"
    iqr_multiplier: float = Field(default=1.5, gt=0, allow_inf_nan=False)
    modified_z_threshold: float = Field(default=3.5, gt=0, allow_inf_nan=False)
    categorical_rare_rate: float = Field(default=0.01, ge=0, lt=1, allow_inf_nan=False)
    categorical_minimum_count: int = Field(default=2, ge=1)
    severity: Severity = Severity.WARNING


class MultivariateAnomalyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = False
    detectors: list[Literal["isolation_forest", "local_outlier_factor"]] = Field(
        default_factory=_default_multivariate_detectors, min_length=1
    )
    contamination: float = Field(default=0.05, gt=0, le=0.5, allow_inf_nan=False)
    neighbors: int = Field(default=20, ge=2, le=500)
    minimum_samples: int = Field(default=20, ge=5)
    max_features: int = Field(default=20, ge=1, le=500)
    random_state: int = 42
    severity: Severity = Severity.WARNING


class DetectionPolicyConfig(BaseModel):
    """Complete configurable detector selection and threshold set."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_drift: SchemaPolicy = Field(default_factory=SchemaPolicy, serialization_alias="schema")
    numeric_drift: NumericDriftPolicy = Field(default_factory=NumericDriftPolicy)
    categorical_drift: CategoricalDriftPolicy = Field(default_factory=CategoricalDriftPolicy)
    univariate_anomaly: UnivariateAnomalyPolicy = Field(default_factory=UnivariateAnomalyPolicy)
    multivariate_anomaly: MultivariateAnomalyPolicy = Field(
        default_factory=MultivariateAnomalyPolicy
    )


class DetectionPolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    config: DetectionPolicyConfig = Field(default_factory=DetectionPolicyConfig)
    enabled: bool = True


class DetectionPolicyView(BaseModel):
    id: UUID
    dataset_id: UUID
    name: str
    config: DetectionPolicyConfig
    fingerprint: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


class ReliabilityAssessmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_version_id: UUID
    policy_id: UUID
    baseline_strategy: BaselineStrategy = BaselineStrategy.PREVIOUS
    explicit_baseline_version_id: UUID | None = None
    rolling_window: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def validate_baseline_arguments(self) -> ReliabilityAssessmentInput:
        if self.baseline_strategy == BaselineStrategy.EXPLICIT:
            if self.explicit_baseline_version_id is None:
                raise ValueError("explicit baseline strategy requires explicit_baseline_version_id")
        elif self.explicit_baseline_version_id is not None:
            raise ValueError("explicit_baseline_version_id is only valid for explicit strategy")
        if self.baseline_strategy == BaselineStrategy.ROLLING:
            if self.rolling_window is None:
                raise ValueError("rolling baseline strategy requires rolling_window")
        elif self.rolling_window is not None:
            raise ValueError("rolling_window is only valid for rolling strategy")
        return self


class BaselineVersionView(BaseModel):
    id: UUID
    version_number: int


class ReliabilityFindingView(BaseModel):
    id: UUID
    assessment_id: UUID
    detector: str
    finding_type: str
    status: FindingStatus
    severity: Severity
    column_name: str | None
    baseline_value: object | None
    target_value: object | None
    evidence: dict[str, object]
    message: str


class ReliabilityAssessmentView(BaseModel):
    id: UUID
    dataset_id: UUID
    target_version_id: UUID
    target_version_number: int
    baseline_strategy: BaselineStrategy
    baseline_versions: list[BaselineVersionView]
    policy_id: UUID
    policy_fingerprint: str
    policy_snapshot: dict[str, object]
    identity_fingerprint: str
    status: AssessmentStatus
    finding_count: int
    evaluated_at: datetime
    message: str | None = None


class ReliabilityAssessmentResult(BaseModel):
    assessment: ReliabilityAssessmentView
    findings: list[ReliabilityFindingView]


class ChangeOnsetView(BaseModel):
    dataset_id: UUID
    detector: str
    finding_type: str
    column_name: str | None
    first_version_id: UUID
    first_version_number: int
    assessment_id: UUID
    evidence: dict[str, object]


class InvestigationCorrelation(BaseModel):
    kind: str
    column_name: str | None
    reliability_finding_ids: list[UUID]
    quality_assessment_id: UUID | None
    quality_finding_ids: list[UUID]
    description: str
    causal_claim: Literal[False] = False


class InvestigationView(BaseModel):
    dataset_id: UUID
    dataset_version_id: UUID
    version_number: int
    reliability_assessment_ids: list[UUID]
    quality_assessment_ids: list[UUID]
    affected_columns: list[str]
    correlations: list[InvestigationCorrelation]
    observed_facts: list[str]
    limitations: list[str]
