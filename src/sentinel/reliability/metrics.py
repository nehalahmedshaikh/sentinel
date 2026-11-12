"""Deterministic numeric and categorical distribution metrics."""

from collections import Counter
from typing import Any

import numpy as np
import polars as pl
from scipy.spatial.distance import jensenshannon  # type: ignore[import-untyped]
from scipy.stats import ks_2samp, wasserstein_distance  # type: ignore[import-untyped]

from sentinel.reliability.contracts import (
    CategoricalDriftPolicy,
    FindingStatus,
    NumericDriftPolicy,
    Severity,
)
from sentinel.reliability.findings import FindingData

_EPSILON = 1e-6


def _finite_numeric(series: pl.Series) -> tuple[np.ndarray[Any, Any], int]:
    """Convert only one numeric column, excluding null/NaN/infinite observations."""
    values = np.asarray(series.drop_nulls().to_list(), dtype=np.float64)
    finite = values[np.isfinite(values)]
    return finite, int(values.size - finite.size)


def _psi_from_counts(base: np.ndarray[Any, Any], target: np.ndarray[Any, Any]) -> float:
    base_share = (base + _EPSILON) / (base.sum() + len(base) * _EPSILON)
    target_share = (target + _EPSILON) / (target.sum() + len(target) * _EPSILON)
    return float(np.sum((target_share - base_share) * np.log(target_share / base_share)))


def _numeric_psi(baseline: np.ndarray[Any, Any], target: np.ndarray[Any, Any], bins: int) -> float:
    if np.all(baseline == baseline[0]):
        base_counts = np.asarray([baseline.size, 0], dtype=np.int64)
        target_match = int(np.count_nonzero(target == baseline[0]))
        target_counts = np.asarray([target_match, target.size - target_match], dtype=np.int64)
        return _psi_from_counts(base_counts, target_counts)
    quantiles = np.quantile(baseline, np.linspace(0, 1, bins + 1)[1:-1])
    edges = np.unique(quantiles)
    base_counts = np.bincount(
        np.searchsorted(edges, baseline, side="right"), minlength=len(edges) + 1
    )
    target_counts = np.bincount(
        np.searchsorted(edges, target, side="right"), minlength=len(edges) + 1
    )
    return _psi_from_counts(base_counts, target_counts)


def numeric_distribution_finding(
    column: str,
    baseline_series: pl.Series,
    target_series: pl.Series,
    policy: NumericDriftPolicy,
) -> FindingData:
    """Compare finite numeric samples using KS, PSI, and Wasserstein distance."""
    baseline, baseline_non_finite = _finite_numeric(baseline_series)
    target, target_non_finite = _finite_numeric(target_series)
    base_n, target_n = int(baseline.size), int(target.size)
    if base_n == 0 or target_n == 0:
        return FindingData(
            detector="numeric_distribution_drift",
            finding_type="distribution_unavailable",
            status=FindingStatus.UNAVAILABLE,
            column_name=column,
            evidence={
                "baseline_sample_size": base_n,
                "target_sample_size": target_n,
                "baseline_non_finite": baseline_non_finite,
                "target_non_finite": target_non_finite,
                "reason": "one or both columns contain no finite numeric values",
            },
            message=f"Numeric distribution comparison unavailable for '{column}'",
        )
    ks_statistic: float | None = None
    ks_pvalue: float | None = None
    if base_n >= policy.minimum_samples and target_n >= policy.minimum_samples:
        ks = ks_2samp(baseline, target, alternative="two-sided", method="auto")
        ks_statistic = float(ks.statistic)
        ks_pvalue = float(ks.pvalue)
    psi = _numeric_psi(baseline, target, policy.bin_count)
    wasserstein = float(wasserstein_distance(baseline, target))
    significant = ks_pvalue is not None and ks_pvalue < policy.ks_alpha
    practical = (
        psi >= policy.psi_threshold
        or wasserstein >= policy.wasserstein_threshold
        or (
            ks_statistic is not None
            and ks_statistic >= policy.ks_statistic_threshold
            and significant
        )
    )
    enough_for_negative = base_n >= policy.minimum_samples and target_n >= policy.minimum_samples
    status = (
        FindingStatus.DETECTED
        if practical
        else FindingStatus.NOT_DETECTED
        if enough_for_negative
        else FindingStatus.UNAVAILABLE
    )
    return FindingData(
        detector="numeric_distribution_drift",
        finding_type="numeric_distribution_drift",
        status=status,
        severity=policy.severity if practical else Severity.INFO,
        column_name=column,
        evidence={
            "baseline_sample_size": base_n,
            "target_sample_size": target_n,
            "baseline_non_finite": baseline_non_finite,
            "target_non_finite": target_non_finite,
            "ks_statistic": ks_statistic,
            "ks_pvalue": ks_pvalue,
            "ks_alpha": policy.ks_alpha,
            "ks_statistic_threshold": policy.ks_statistic_threshold,
            "psi": psi,
            "psi_threshold": policy.psi_threshold,
            "wasserstein_distance": wasserstein,
            "wasserstein_threshold": policy.wasserstein_threshold,
            "practically_significant": practical,
            "statistically_significant": significant,
            "minimum_samples_for_ks": policy.minimum_samples,
            "psi_binning": "baseline quantile bins; constant baseline uses match/non-match bins",
            "psi_bin_count_requested": policy.bin_count,
        },
        message=(
            f"Numeric distribution for '{column}' crosses practical drift thresholds"
            if practical
            else f"Numeric distribution for '{column}' has no established practical drift"
            if status == FindingStatus.NOT_DETECTED
            else (
                f"Numeric distribution for '{column}' has insufficient samples "
                "to establish no drift"
            )
        ),
    )


def _categorical_counts(series: pl.Series) -> Counter[str]:
    values = series.drop_nulls().cast(pl.String).to_list()
    return Counter(str(value) for value in values)


def categorical_distribution_finding(
    column: str,
    baseline_series: pl.Series,
    target_series: pl.Series,
    policy: CategoricalDriftPolicy,
) -> FindingData:
    """Compare categorical frequency distributions using JS divergence and PSI."""
    baseline_counts = _categorical_counts(baseline_series)
    target_counts = _categorical_counts(target_series)
    base_n, target_n = sum(baseline_counts.values()), sum(target_counts.values())
    if base_n == 0 or target_n == 0:
        return FindingData(
            detector="categorical_distribution_drift",
            finding_type="distribution_unavailable",
            status=FindingStatus.UNAVAILABLE,
            column_name=column,
            evidence={
                "baseline_sample_size": base_n,
                "target_sample_size": target_n,
                "baseline_null_count": baseline_series.null_count(),
                "target_null_count": target_series.null_count(),
                "reason": "one or both columns have no non-null categorical values",
            },
            message=f"Categorical distribution comparison unavailable for '{column}'",
        )
    values = sorted(set(baseline_counts) | set(target_counts))
    baseline_vector = np.asarray([baseline_counts[value] for value in values], dtype=np.float64)
    target_vector = np.asarray([target_counts[value] for value in values], dtype=np.float64)
    baseline_probability = baseline_vector / baseline_vector.sum()
    target_probability = target_vector / target_vector.sum()
    js_divergence = float(jensenshannon(baseline_probability, target_probability, base=2) ** 2)
    psi = _psi_from_counts(baseline_vector, target_vector)
    practical = js_divergence >= policy.js_divergence_threshold or psi >= policy.psi_threshold
    enough = base_n >= policy.minimum_samples and target_n >= policy.minimum_samples
    status = (
        FindingStatus.DETECTED
        if practical
        else FindingStatus.NOT_DETECTED
        if enough
        else FindingStatus.UNAVAILABLE
    )
    return FindingData(
        detector="categorical_distribution_drift",
        finding_type="categorical_distribution_drift",
        status=status,
        severity=policy.severity if practical else Severity.INFO,
        column_name=column,
        evidence={
            "baseline_sample_size": base_n,
            "target_sample_size": target_n,
            "baseline_null_count": baseline_series.null_count(),
            "target_null_count": target_series.null_count(),
            "category_count": len(values),
            "jensen_shannon_divergence": js_divergence,
            "js_divergence_threshold": policy.js_divergence_threshold,
            "psi": psi,
            "psi_threshold": policy.psi_threshold,
            "practically_significant": practical,
            "minimum_samples": policy.minimum_samples,
            "psi_binning": "one bin per category in the sorted union of observed categories",
        },
        message=(
            f"Categorical distribution for '{column}' crosses practical drift thresholds"
            if practical
            else f"Categorical distribution for '{column}' has no established practical drift"
            if status == FindingStatus.NOT_DETECTED
            else (
                f"Categorical distribution for '{column}' has insufficient samples "
                "to establish no drift"
            )
        ),
    )
