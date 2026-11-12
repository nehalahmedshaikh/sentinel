"""Independent schema, drift, and anomaly detector implementations."""

import hashlib
from collections import Counter
from typing import Any

import numpy as np
import polars as pl
from sklearn.ensemble import IsolationForest  # type: ignore[import-untyped]
from sklearn.neighbors import LocalOutlierFactor  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

from sentinel.reliability.contracts import (
    DetectionPolicyConfig,
    FindingStatus,
    Severity,
)
from sentinel.reliability.findings import FindingData
from sentinel.reliability.metrics import (
    categorical_distribution_finding,
    numeric_distribution_finding,
)

_MAX_ROW_REFERENCES = 20
_CATEGORICAL_DTYPES = {pl.String, pl.Boolean, pl.Categorical}


def detect_schema_drift(
    baseline_schema: list[dict[str, Any]],
    target_schema: list[dict[str, Any]],
    policy: DetectionPolicyConfig,
) -> list[FindingData]:
    """Report added, removed, and type-changed columns."""
    if not policy.schema_drift.enabled:
        return []
    baseline = {column["name"]: column for column in baseline_schema}
    target = {column["name"]: column for column in target_schema}
    findings: list[FindingData] = []
    for name in sorted(set(baseline) | set(target)):
        before, after = baseline.get(name), target.get(name)
        if before is None:
            if after is None:
                continue
            findings.append(
                FindingData(
                    detector="schema_drift",
                    finding_type="column_added",
                    status=FindingStatus.DETECTED,
                    severity=policy.schema_drift.added_severity,
                    column_name=name,
                    baseline_value=None,
                    target_value=after,
                    evidence={"classification": "added", "column_position": after["position"]},
                    message=f"Column '{name}' was added",
                )
            )
        elif after is None:
            findings.append(
                FindingData(
                    detector="schema_drift",
                    finding_type="column_removed",
                    status=FindingStatus.DETECTED,
                    severity=policy.schema_drift.removed_severity,
                    column_name=name,
                    baseline_value=before,
                    target_value=None,
                    evidence={"classification": "removed", "column_position": before["position"]},
                    message=f"Column '{name}' was removed",
                )
            )
        elif before["data_type"] != after["data_type"]:
            findings.append(
                FindingData(
                    detector="schema_drift",
                    finding_type="column_type_changed",
                    status=FindingStatus.DETECTED,
                    severity=policy.schema_drift.type_change_severity,
                    column_name=name,
                    baseline_value=before["data_type"],
                    target_value=after["data_type"],
                    evidence={
                        "classification": "type_changed",
                        "column_position_changed": before["position"] != after["position"],
                    },
                    message=(
                        f"Column '{name}' changed type from {before['data_type']} "
                        f"to {after['data_type']}"
                    ),
                )
            )
    return findings


def detect_distribution_drift(
    baseline: pl.DataFrame, target: pl.DataFrame, policy: DetectionPolicyConfig
) -> list[FindingData]:
    """Compare same-named, type-compatible columns using suitable metrics."""
    findings: list[FindingData] = []
    for column in sorted(set(baseline.columns) & set(target.columns)):
        base_series, target_series = baseline.get_column(column), target.get_column(column)
        if base_series.dtype != target_series.dtype:
            findings.append(
                FindingData(
                    detector="distribution_drift",
                    finding_type="distribution_unavailable",
                    status=FindingStatus.UNAVAILABLE,
                    column_name=column,
                    evidence={
                        "baseline_type": str(base_series.dtype),
                        "target_type": str(target_series.dtype),
                        "reason": "column data types differ",
                    },
                    message=(
                        f"Distribution comparison unavailable for '{column}' "
                        "because its type changed"
                    ),
                )
            )
        elif base_series.dtype.is_numeric() and policy.numeric_drift.enabled:
            findings.append(
                numeric_distribution_finding(
                    column, base_series, target_series, policy.numeric_drift
                )
            )
        elif _is_categorical(base_series.dtype) and policy.categorical_drift.enabled:
            findings.append(
                categorical_distribution_finding(
                    column, base_series, target_series, policy.categorical_drift
                )
            )
    if not findings and (policy.numeric_drift.enabled or policy.categorical_drift.enabled):
        findings.append(
            FindingData(
                detector="distribution_drift",
                finding_type="distribution_unavailable",
                status=FindingStatus.UNAVAILABLE,
                evidence={"reason": "no type-compatible columns are available for comparison"},
                message="Distribution drift comparison unavailable: no comparable columns",
            )
        )
    return findings


def detect_univariate_anomalies(
    target: pl.DataFrame, policy: DetectionPolicyConfig
) -> list[FindingData]:
    """Find numeric outliers and low-frequency categorical values."""
    config = policy.univariate_anomaly
    if not config.enabled:
        return []
    findings: list[FindingData] = []
    for column, dtype in target.schema.items():
        try:
            if dtype.is_numeric():
                findings.append(_numeric_anomalies(target, column, config))
            elif _is_categorical(dtype):
                findings.extend(_categorical_anomalies(target, column, config))
        except Exception as exc:
            findings.append(
                FindingData(
                    detector="univariate_anomaly",
                    finding_type="univariate_evaluation_error",
                    status=FindingStatus.ERROR,
                    severity=Severity.WARNING,
                    column_name=column,
                    evidence={"exception_type": type(exc).__name__},
                    message=(
                        f"Univariate anomaly detection failed for '{column}': "
                        f"{type(exc).__name__}: {exc}"
                    ),
                )
            )
    return findings


def _numeric_anomalies(frame: pl.DataFrame, column: str, config: Any) -> FindingData:
    """Apply IQR fences or modified Z-score to one numeric column."""
    series = frame.get_column(column)
    values = np.asarray(series.to_list(), dtype=np.float64)
    valid = np.isfinite(values)
    valid_values = values[valid]
    if valid_values.size < 3:
        return FindingData(
            detector="univariate_numeric",
            finding_type="numeric_outlier",
            status=FindingStatus.UNAVAILABLE,
            column_name=column,
            evidence={
                "sample_size": int(valid_values.size),
                "reason": "fewer than three finite values",
            },
            message=f"Numeric outlier detection unavailable for '{column}'",
        )
    if config.numeric_method == "iqr":
        q1, q3 = np.quantile(valid_values, [0.25, 0.75])
        spread = q3 - q1
        lower = float(q1 - config.iqr_multiplier * spread)
        upper = float(q3 + config.iqr_multiplier * spread)
        outlier_mask = valid & ((values < lower) | (values > upper))
        evidence = {
            "method": "iqr",
            "q1": float(q1),
            "q3": float(q3),
            "iqr": float(spread),
            "multiplier": config.iqr_multiplier,
            "lower_bound": lower,
            "upper_bound": upper,
        }
    else:
        median = float(np.median(valid_values))
        deviations = np.abs(valid_values - median)
        mad = float(np.median(deviations))
        if mad == 0:
            valid_outlier = valid_values != median
        else:
            valid_outlier = 0.67448975 * deviations / mad > config.modified_z_threshold
        outlier_mask = np.zeros(values.shape, dtype=bool)
        outlier_mask[np.flatnonzero(valid)] = valid_outlier
        evidence = {
            "method": "modified_z_score",
            "median": median,
            "median_absolute_deviation": mad,
            "threshold": config.modified_z_threshold,
        }
    count = int(outlier_mask.sum())
    indices = np.flatnonzero(outlier_mask)[:_MAX_ROW_REFERENCES].tolist()
    rate = count / int(valid.sum())
    return FindingData(
        detector="univariate_numeric",
        finding_type="numeric_outlier",
        status=FindingStatus.DETECTED if count else FindingStatus.NOT_DETECTED,
        severity=config.severity if count else Severity.INFO,
        column_name=column,
        evidence={
            **evidence,
            "sample_size": int(valid.sum()),
            "anomaly_count": count,
            "anomaly_rate": rate,
            "row_indices_sample": indices,
            "row_indices_truncated": count > len(indices),
            "non_finite_or_null_count": frame.height - int(valid.sum()),
        },
        message=(
            f"Found {count} numeric outliers in '{column}'"
            if count
            else f"No numeric outliers found in '{column}'"
        ),
    )


def _categorical_anomalies(frame: pl.DataFrame, column: str, config: Any) -> list[FindingData]:
    """Report low-frequency categories without persisting category values."""
    values = frame.get_column(column).drop_nulls().cast(pl.String).to_list()
    total = len(values)
    if total < config.categorical_minimum_count:
        return [
            FindingData(
                detector="univariate_categorical",
                finding_type="rare_category",
                status=FindingStatus.UNAVAILABLE,
                column_name=column,
                evidence={"sample_size": total, "reason": "below configured minimum count"},
                message=f"Categorical anomaly detection unavailable for '{column}'",
            )
        ]
    counts = Counter(str(value) for value in values)
    if len(counts) / total > 0.5:
        return [
            FindingData(
                detector="univariate_categorical",
                finding_type="rare_category",
                status=FindingStatus.UNAVAILABLE,
                column_name=column,
                evidence={
                    "sample_size": total,
                    "distinct_count": len(counts),
                    "reason": "high-cardinality column",
                },
                message=f"Categorical anomaly detection skipped high-cardinality column '{column}'",
            )
        ]
    rare = [
        (value, count)
        for value, count in counts.items()
        if count < config.categorical_minimum_count
        and count / total <= config.categorical_rare_rate
    ]
    return [
        FindingData(
            detector="univariate_categorical",
            finding_type="rare_category",
            status=FindingStatus.DETECTED if rare else FindingStatus.NOT_DETECTED,
            severity=config.severity if rare else Severity.INFO,
            column_name=column,
            evidence={
                "sample_size": total,
                "rare_category_count": len(rare),
                "rare_categories": [
                    {
                        "token": hashlib.sha256(value.encode("utf-8")).hexdigest()[:16],
                        "count": count,
                    }
                    for value, count in rare[:_MAX_ROW_REFERENCES]
                ],
                "rare_category_rate_threshold": config.categorical_rare_rate,
                "minimum_category_count": config.categorical_minimum_count,
                "null_count": frame.get_column(column).null_count(),
            },
            message=(
                f"Found {len(rare)} rare categories in '{column}'"
                if rare
                else f"No rare categories found in '{column}'"
            ),
        )
    ]


def detect_multivariate_anomalies(
    target: pl.DataFrame, policy: DetectionPolicyConfig
) -> list[FindingData]:
    """Run enabled sklearn detectors on a deterministic numeric feature matrix."""
    config = policy.multivariate_anomaly
    if not config.enabled:
        return []
    eligible = [name for name, dtype in target.schema.items() if dtype.is_numeric()]
    selected = eligible[: config.max_features]
    dropped = len(eligible) - len(selected)
    columns: list[np.ndarray[Any, Any]] = []
    used: list[str] = []
    for name in selected:
        values = np.asarray(target.get_column(name).to_list(), dtype=np.float64)
        values[~np.isfinite(values)] = np.nan
        finite = values[np.isfinite(values)]
        if finite.size == 0 or np.all(finite == finite[0]):
            continue
        median = float(np.median(finite))
        values[np.isnan(values)] = median
        columns.append(values)
        used.append(name)
    reason: str | None = None
    if target.height < config.minimum_samples:
        reason = "row count below configured minimum"
    elif len(columns) < 2:
        reason = "fewer than two non-constant numeric features are available"
    if reason:
        return [
            FindingData(
                detector="multivariate_anomaly",
                finding_type="multivariate_anomaly",
                status=FindingStatus.UNAVAILABLE,
                evidence={
                    "row_count": target.height,
                    "feature_count": len(columns),
                    "selected_features": used,
                    "features_truncated": dropped,
                    "reason": reason,
                },
                message=f"Multivariate anomaly detection unavailable: {reason}",
            )
        ]
    matrix = np.column_stack(columns)
    matrix = StandardScaler().fit_transform(matrix)
    findings: list[FindingData] = []
    for detector in config.detectors:
        try:
            if detector == "isolation_forest":
                model = IsolationForest(
                    n_estimators=100,
                    contamination=config.contamination,
                    random_state=config.random_state,
                    n_jobs=1,
                )
                labels = model.fit_predict(matrix)
            else:
                neighbors = min(config.neighbors, target.height - 1)
                if neighbors < 2:
                    raise ValueError("LOF requires at least three rows")
                labels = LocalOutlierFactor(
                    n_neighbors=neighbors, contamination=config.contamination
                ).fit_predict(matrix)
            indices = np.flatnonzero(labels == -1)
            count = int(indices.size)
            findings.append(
                FindingData(
                    detector=detector,
                    finding_type="multivariate_anomaly",
                    status=FindingStatus.DETECTED if count else FindingStatus.NOT_DETECTED,
                    severity=config.severity if count else Severity.INFO,
                    evidence={
                        "algorithm": detector,
                        "contamination": config.contamination,
                        "sample_size": target.height,
                        "feature_count": len(used),
                        "features": used,
                        "features_truncated": dropped,
                        "anomaly_count": count,
                        "anomaly_rate": count / target.height,
                        "row_indices_sample": indices[:_MAX_ROW_REFERENCES].tolist(),
                        "row_indices_truncated": count > _MAX_ROW_REFERENCES,
                        "preprocessing": (
                            "median imputation, drop constant/all-null columns, StandardScaler"
                        ),
                        "random_state": config.random_state,
                        "n_neighbors": min(config.neighbors, target.height - 1)
                        if detector == "local_outlier_factor"
                        else None,
                    },
                    message=f"{detector} identified {count} anomalous observations",
                )
            )
        except Exception as exc:
            findings.append(
                FindingData(
                    detector=detector,
                    finding_type="multivariate_detector_error",
                    status=FindingStatus.ERROR,
                    severity=Severity.WARNING,
                    evidence={"exception_type": type(exc).__name__, "features": used},
                    message=f"{detector} failed: {type(exc).__name__}: {exc}",
                )
            )
    return findings


def _is_categorical(dtype: pl.DataType) -> bool:
    return dtype in _CATEGORICAL_DTYPES or str(dtype).startswith("Enum")
