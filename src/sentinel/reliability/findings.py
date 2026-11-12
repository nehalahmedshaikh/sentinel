"""Detector finding contract used before persistence."""

from typing import Any

from pydantic import BaseModel

from sentinel.reliability.contracts import FindingStatus, Severity


class FindingData(BaseModel):
    """A structured detector observation without database identifiers."""

    detector: str
    finding_type: str
    status: FindingStatus
    severity: Severity = Severity.INFO
    column_name: str | None = None
    baseline_value: Any = None
    target_value: Any = None
    evidence: dict[str, Any] = {}
    message: str
