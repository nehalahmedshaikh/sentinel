"""Validated contracts for quality rules, findings, and assessments."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RuleType(StrEnum):
    """Supported single-column and whole-row rule identifiers."""

    REQUIRED_COLUMN = "required_column"
    DATA_TYPE = "data_type"
    NOT_NULL = "not_null"
    NULL_PERCENTAGE = "null_percentage"
    UNIQUE = "unique"
    MIN_VALUE = "min_value"
    MAX_VALUE = "max_value"
    RANGE = "range"
    ACCEPTED_VALUES = "accepted_values"
    DUPLICATE_ROWS = "duplicate_rows"


class Severity(StrEnum):
    """Operational importance, independent of the result status."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ResultStatus(StrEnum):
    """Status of an individual rule evaluation or whole assessment."""

    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"


class RuleParamsBase(BaseModel):
    """Base for strictly typed rule-specific parameter models."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    column_name: str | None = Field(default=None, min_length=1, max_length=250)


class RequiredColumnParams(RuleParamsBase):
    kind: Literal[RuleType.REQUIRED_COLUMN]
    column_name: str = Field(min_length=1, max_length=250)


class DataTypeParams(RuleParamsBase):
    kind: Literal[RuleType.DATA_TYPE]
    column_name: str = Field(min_length=1, max_length=250)
    expected_type: str = Field(min_length=1, max_length=50)


class NotNullParams(RuleParamsBase):
    kind: Literal[RuleType.NOT_NULL]
    column_name: str = Field(min_length=1, max_length=250)


class NullPercentageParams(RuleParamsBase):
    kind: Literal[RuleType.NULL_PERCENTAGE]
    column_name: str = Field(min_length=1, max_length=250)
    max_percentage: float = Field(ge=0, le=100, allow_inf_nan=False)


class UniqueParams(RuleParamsBase):
    kind: Literal[RuleType.UNIQUE]
    column_name: str = Field(min_length=1, max_length=250)


class MinValueParams(RuleParamsBase):
    kind: Literal[RuleType.MIN_VALUE]
    column_name: str = Field(min_length=1, max_length=250)
    minimum: int | float = Field(allow_inf_nan=False)


class MaxValueParams(RuleParamsBase):
    kind: Literal[RuleType.MAX_VALUE]
    column_name: str = Field(min_length=1, max_length=250)
    maximum: int | float = Field(allow_inf_nan=False)


class RangeParams(RuleParamsBase):
    kind: Literal[RuleType.RANGE]
    column_name: str = Field(min_length=1, max_length=250)
    minimum: int | float = Field(allow_inf_nan=False)
    maximum: int | float = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_order(self) -> RangeParams:
        if self.minimum > self.maximum:
            raise ValueError("range minimum must be less than or equal to maximum")
        return self


class AcceptedValuesParams(RuleParamsBase):
    kind: Literal[RuleType.ACCEPTED_VALUES]
    column_name: str = Field(min_length=1, max_length=250)
    values: list[str | int | float | bool] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_finite_values(self) -> AcceptedValuesParams:
        if any(
            isinstance(value, float) and (value != value or abs(value) == float("inf"))
            for value in self.values
        ):
            raise ValueError("accepted values must be finite")
        return self


class DuplicateRowsParams(RuleParamsBase):
    kind: Literal[RuleType.DUPLICATE_ROWS]
    column_name: None = None


RuleParameters = Annotated[
    RequiredColumnParams
    | DataTypeParams
    | NotNullParams
    | NullPercentageParams
    | UniqueParams
    | MinValueParams
    | MaxValueParams
    | RangeParams
    | AcceptedValuesParams
    | DuplicateRowsParams,
    Field(discriminator="kind"),
]


class QualityRuleInput(BaseModel):
    """Validated rule create or update input."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    rule_type: RuleType
    parameters: RuleParameters
    severity: Severity = Severity.ERROR
    enabled: bool = True

    @model_validator(mode="after")
    def parameters_match_type(self) -> QualityRuleInput:
        if self.parameters.kind != self.rule_type:
            raise ValueError("parameters.kind must match rule_type")
        return self


class QualityRuleView(BaseModel):
    """Public persisted rule representation."""

    id: UUID
    dataset_id: UUID
    name: str
    rule_type: RuleType
    parameters: RuleParameters
    severity: Severity
    enabled: bool
    created_at: datetime
    updated_at: datetime


class FindingResult(BaseModel):
    """Infrastructure-independent result of evaluating one rule."""

    rule_id: UUID | None
    rule_snapshot: dict[str, object]
    status: ResultStatus
    severity: Severity
    evaluated_records: int | None
    violating_records: int | None
    violation_percentage: float | None
    evidence: dict[str, object]
    message: str


class QualityFindingView(FindingResult):
    """Persisted finding view."""

    id: UUID
    assessment_id: UUID


class QualityAssessmentView(BaseModel):
    """Immutable persisted assessment representation."""

    id: UUID
    dataset_version_id: UUID
    ruleset_fingerprint: str
    rules_snapshot: list[dict[str, object]]
    status: ResultStatus
    total_rules: int
    passed_rules: int
    failed_rules: int
    error_rules: int
    evaluated_at: datetime
    message: str | None = None


class AssessmentResult(BaseModel):
    """Assessment and its findings returned by evaluation."""

    assessment: QualityAssessmentView
    findings: list[QualityFindingView]
