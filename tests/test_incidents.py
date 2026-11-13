"""Unit tests for stable incident correlation and configured qualification."""

from uuid import uuid4

from sentinel.incidents.contracts import IncidentPolicyConfig, SourceType
from sentinel.incidents.service import _column_overlap, _qualification, correlation_fingerprint


def test_correlation_fingerprint_is_stable_and_dataset_scoped() -> None:
    dataset = uuid4()
    assert correlation_fingerprint(dataset, "not_null", ["status"]) == correlation_fingerprint(
        dataset, "NOT_NULL", ["STATUS", "status"]
    )
    assert correlation_fingerprint(dataset, "not_null", ["status"]) != correlation_fingerprint(
        uuid4(), "not_null", ["status"]
    )


def test_incident_policy_excludes_errors_and_nonqualifying_findings() -> None:
    policy = IncidentPolicyConfig(
        minimum_source_severities={"quality": ["ERROR"], "reliability": ["WARNING"]}
    )
    assert _qualification(policy, SourceType.QUALITY, "not_null", "ERROR", "CRITICAL")[0] is False
    assert _qualification(policy, SourceType.QUALITY, "not_null", "FAIL", "WARNING")[0] is False
    assert _qualification(policy, SourceType.QUALITY, "not_null", "FAIL", "CRITICAL")[0] is True
    assert (
        _qualification(policy, SourceType.RELIABILITY, "column_added", "ERROR", "WARNING")[0]
        is False
    )


def test_column_overlap_uses_jaccard_and_rejects_empty_evidence() -> None:
    assert _column_overlap({"status", "amount"}, ["status", "amount"]) == 1.0
    assert _column_overlap({"status", "amount"}, ["status", "region"]) == 1 / 3
    assert _column_overlap(set(), ["status"]) == 0.0
