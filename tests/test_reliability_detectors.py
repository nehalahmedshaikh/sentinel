"""Unit tests for drift and anomaly detector calculations."""

import numpy as np
import polars as pl

from sentinel.reliability.aggregation import policy_fingerprint
from sentinel.reliability.contracts import (
    CategoricalDriftPolicy,
    DetectionPolicyConfig,
    FindingStatus,
    MultivariateAnomalyPolicy,
    NumericDriftPolicy,
    UnivariateAnomalyPolicy,
)
from sentinel.reliability.detectors import (
    detect_multivariate_anomalies,
    detect_schema_drift,
    detect_univariate_anomalies,
)
from sentinel.reliability.metrics import (
    categorical_distribution_finding,
    numeric_distribution_finding,
)


def test_schema_drift_finds_added_removed_and_type_changes() -> None:
    baseline = [
        {"name": "old", "data_type": "String", "position": 0},
        {"name": "changed", "data_type": "Int64", "position": 1},
    ]
    target = [
        {"name": "changed", "data_type": "Float64", "position": 0},
        {"name": "new", "data_type": "Boolean", "position": 1},
    ]
    findings = detect_schema_drift(baseline, target, DetectionPolicyConfig())
    assert {finding.finding_type for finding in findings} == {
        "column_added",
        "column_removed",
        "column_type_changed",
    }
    changed = next(item for item in findings if item.finding_type == "column_type_changed")
    assert changed.baseline_value == "Int64"
    assert changed.target_value == "Float64"


def test_numeric_drift_distinguishes_practical_change_from_small_difference() -> None:
    policy = NumericDriftPolicy(minimum_samples=20)
    baseline = pl.Series("x", list(range(100)))
    same = numeric_distribution_finding("x", baseline, baseline.clone(), policy)
    assert same.status == FindingStatus.NOT_DETECTED
    assert same.evidence["statistically_significant"] is False
    assert same.evidence["practically_significant"] is False

    shifted = pl.Series("x", [value + 100 for value in range(100)])
    drift = numeric_distribution_finding("x", baseline, shifted, policy)
    assert drift.status == FindingStatus.DETECTED
    assert drift.evidence["statistically_significant"] is True
    assert drift.evidence["practically_significant"] is True
    assert drift.evidence["ks_statistic"] == 1.0
    assert drift.evidence["wasserstein_distance"] == 100.0


def test_numeric_drift_handles_small_samples_constants_and_empty_data() -> None:
    policy = NumericDriftPolicy(minimum_samples=4)
    insufficient = numeric_distribution_finding(
        "x", pl.Series("x", [1]), pl.Series("x", [1]), policy
    )
    assert insufficient.status == FindingStatus.UNAVAILABLE
    assert insufficient.evidence["ks_pvalue"] is None

    constant_same = numeric_distribution_finding(
        "x", pl.Series("x", [2, 2, 2, 2]), pl.Series("x", [2, 2, 2, 2]), policy
    )
    assert constant_same.status == FindingStatus.NOT_DETECTED
    constant_changed = numeric_distribution_finding(
        "x", pl.Series("x", [2, 2, 2, 2]), pl.Series("x", [3, 3, 3, 3]), policy
    )
    assert constant_changed.status == FindingStatus.DETECTED
    empty = numeric_distribution_finding(
        "x", pl.Series("x", [], dtype=pl.Float64), pl.Series("x", [1.0]), policy
    )
    assert empty.status == FindingStatus.UNAVAILABLE


def test_numeric_drift_excludes_null_nan_and_infinity_explicitly() -> None:
    baseline = pl.Series("x", [1.0, 2.0, None, float("nan"), float("inf")])
    target = pl.Series("x", [1.0, 2.0, None, float("nan"), float("inf")])
    result = numeric_distribution_finding(
        "x", baseline, target, NumericDriftPolicy(minimum_samples=2)
    )
    assert result.status == FindingStatus.NOT_DETECTED
    assert result.evidence["baseline_sample_size"] == 2
    assert result.evidence["baseline_non_finite"] == 2


def test_categorical_js_and_psi_for_same_and_changed_distribution() -> None:
    policy = CategoricalDriftPolicy(minimum_samples=10)
    baseline = pl.Series("state", ["a"] * 50 + ["b"] * 50)
    same = categorical_distribution_finding("state", baseline, baseline.clone(), policy)
    assert same.status == FindingStatus.NOT_DETECTED
    assert same.evidence["jensen_shannon_divergence"] == 0
    shifted = pl.Series("state", ["a"] * 10 + ["b"] * 90)
    changed = categorical_distribution_finding("state", baseline, shifted, policy)
    assert changed.status == FindingStatus.DETECTED
    assert 0 < changed.evidence["jensen_shannon_divergence"] <= 1
    assert changed.evidence["psi"] > policy.psi_threshold


def test_categorical_drift_handles_null_and_empty_columns() -> None:
    policy = CategoricalDriftPolicy(minimum_samples=2)
    empty = categorical_distribution_finding(
        "x", pl.Series("x", [None, None], dtype=pl.String), pl.Series("x", ["a"]), policy
    )
    assert empty.status == FindingStatus.UNAVAILABLE
    nulls = categorical_distribution_finding(
        "x", pl.Series("x", ["a", None, "b"]), pl.Series("x", ["a", None, "b"]), policy
    )
    assert nulls.status == FindingStatus.NOT_DETECTED
    assert nulls.evidence["baseline_null_count"] == 1


def test_univariate_numeric_outliers_and_rare_categories_are_bounded() -> None:
    values = [0.0] * 29 + [50.0]
    frame = pl.DataFrame({"measurement": values, "status": ["ok"] * 29 + ["rare"]})
    config = DetectionPolicyConfig(
        univariate_anomaly=UnivariateAnomalyPolicy(categorical_rare_rate=0.1)
    )
    findings = detect_univariate_anomalies(frame, config)
    numeric = next(item for item in findings if item.detector == "univariate_numeric")
    categorical = next(item for item in findings if item.detector == "univariate_categorical")
    assert numeric.status == FindingStatus.DETECTED
    assert numeric.evidence["anomaly_count"] == 1
    assert numeric.evidence["row_indices_sample"] == [29]
    assert categorical.status == FindingStatus.DETECTED
    assert "rare" not in str(categorical.evidence["rare_categories"])


def test_multivariate_algorithms_are_opt_in_and_report_unsuitable_data() -> None:
    assert detect_multivariate_anomalies(pl.DataFrame({"x": [1]}), DetectionPolicyConfig()) == []
    unsuitable = DetectionPolicyConfig(
        multivariate_anomaly=MultivariateAnomalyPolicy(enabled=True, minimum_samples=5)
    )
    result = detect_multivariate_anomalies(pl.DataFrame({"x": [1, 2, 3]}), unsuitable)
    assert len(result) == 1
    assert result[0].status == FindingStatus.UNAVAILABLE


def test_isolation_forest_and_lof_detect_synthetic_multivariate_outlier() -> None:
    generator = np.random.default_rng(19)
    ordinary = generator.normal(0, 0.2, size=(100, 2))
    rows = np.vstack((ordinary, np.asarray([[8.0, 8.0]])))
    frame = pl.DataFrame({"x": rows[:, 0], "y": rows[:, 1]})
    policy = DetectionPolicyConfig(
        multivariate_anomaly=MultivariateAnomalyPolicy(
            enabled=True,
            contamination=0.03,
            minimum_samples=20,
            neighbors=15,
            random_state=11,
        )
    )
    results = detect_multivariate_anomalies(frame, policy)
    assert {item.detector for item in results} == {"isolation_forest", "local_outlier_factor"}
    assert all(item.status == FindingStatus.DETECTED for item in results)
    assert all(item.evidence["row_indices_sample"] for item in results)


def test_policy_fingerprint_includes_detector_settings() -> None:
    first = DetectionPolicyConfig()
    second = DetectionPolicyConfig(numeric_drift=NumericDriftPolicy(psi_threshold=0.3))
    assert policy_fingerprint(first) == policy_fingerprint(first)
    assert policy_fingerprint(first) != policy_fingerprint(second)
