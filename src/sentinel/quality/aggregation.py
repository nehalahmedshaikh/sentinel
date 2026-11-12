"""Ruleset fingerprints and assessment aggregation."""

import hashlib
import json
from collections import Counter

from sentinel.quality.contracts import FindingResult, QualityRuleInput, ResultStatus


def rule_snapshot(rule_id: str, rule: QualityRuleInput) -> dict[str, object]:
    """Create the canonical historical definition used by an assessment."""
    return {
        "id": rule_id,
        "name": rule.name,
        "rule_type": rule.rule_type.value,
        "parameters": rule.parameters.model_dump(mode="json"),
        "severity": rule.severity.value,
        "enabled": rule.enabled,
    }


def ruleset_fingerprint(snapshots: list[dict[str, object]]) -> str:
    """Hash a canonical ordering of the enabled rule definitions."""
    ordered = sorted(snapshots, key=lambda snapshot: str(snapshot["id"]))
    canonical = json.dumps(ordered, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assessment_summary(
    findings: list[FindingResult], snapshots: list[dict[str, object]]
) -> tuple[ResultStatus, int, int, int, int, str | None]:
    """Aggregate rule results with FAIL > ERROR > PASS precedence."""
    counts = Counter(finding.status for finding in findings)
    if not findings:
        return (
            ResultStatus.ERROR,
            0,
            0,
            0,
            0,
            "No enabled rules; no data-quality assertions were evaluated.",
        )
    if counts[ResultStatus.FAIL]:
        status = ResultStatus.FAIL
    elif counts[ResultStatus.ERROR]:
        status = ResultStatus.ERROR
    else:
        status = ResultStatus.PASS
    return (
        status,
        len(snapshots),
        counts[ResultStatus.PASS],
        counts[ResultStatus.FAIL],
        counts[ResultStatus.ERROR],
        None,
    )
