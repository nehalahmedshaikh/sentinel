"""Polars rule evaluators and deterministic finding construction."""

import math
from collections.abc import Callable
from typing import Protocol

import polars as pl

from sentinel.quality.contracts import (
    AcceptedValuesParams,
    DataTypeParams,
    DuplicateRowsParams,
    FindingResult,
    MaxValueParams,
    MinValueParams,
    NotNullParams,
    NullPercentageParams,
    QualityRuleInput,
    RangeParams,
    RequiredColumnParams,
    ResultStatus,
    RuleType,
    UniqueParams,
)


class Evaluator(Protocol):
    """One independent implementation for a supported rule type."""

    def __call__(self, rule: QualityRuleInput, frame: pl.DataFrame) -> FindingResult: ...


def _make(
    rule: QualityRuleInput,
    status: ResultStatus,
    message: str,
    *,
    evaluated: int | None = None,
    violating: int | None = None,
    evidence: dict[str, object] | None = None,
) -> FindingResult:
    percentage = (
        violating / evaluated * 100
        if evaluated and violating is not None
        else (0.0 if violating == 0 else None)
    )
    return FindingResult(
        rule_id=None,
        rule_snapshot={},
        status=status,
        severity=rule.severity,
        evaluated_records=evaluated,
        violating_records=violating,
        violation_percentage=percentage,
        evidence=evidence or {},
        message=message,
    )


def _missing(rule: QualityRuleInput, column: str, rows: int) -> FindingResult:
    return _make(
        rule,
        ResultStatus.ERROR,
        f"Column '{column}' does not exist",
        evaluated=rows,
        evidence={"column": column, "column_exists": False},
    )


def _empty(rule: QualityRuleInput, rows: int) -> FindingResult:
    return _make(
        rule,
        ResultStatus.ERROR,
        "Rule cannot be evaluated because the dataset has no records",
        evaluated=rows,
        evidence={"row_count": 0},
    )


def _has_column(frame: pl.DataFrame, column: str) -> bool:
    return column in frame.columns


class RequiredColumnEvaluator:
    """Check schema presence independently of row count."""

    def __call__(self, rule: QualityRuleInput, frame: pl.DataFrame) -> FindingResult:
        params = rule.parameters
        assert isinstance(params, RequiredColumnParams)
        exists = _has_column(frame, params.column_name)
        return _make(
            rule,
            ResultStatus.PASS if exists else ResultStatus.FAIL,
            f"Required column '{params.column_name}' is {'present' if exists else 'missing'}",
            evidence={"column": params.column_name, "column_exists": exists},
        )


class DataTypeEvaluator:
    """Compare an inferred Polars dtype without coercion."""

    def __call__(self, rule: QualityRuleInput, frame: pl.DataFrame) -> FindingResult:
        params = rule.parameters
        assert isinstance(params, DataTypeParams)
        if not _has_column(frame, params.column_name):
            return _missing(rule, params.column_name, frame.height)
        observed = str(frame.schema[params.column_name])
        passed = observed == params.expected_type
        return _make(
            rule,
            ResultStatus.PASS if passed else ResultStatus.FAIL,
            f"Column '{params.column_name}' has type {observed}; expected {params.expected_type}",
            evaluated=frame.height,
            violating=0 if passed else frame.height,
            evidence={
                "column": params.column_name,
                "expected_type": params.expected_type,
                "observed_type": observed,
            },
        )


class NotNullEvaluator:
    """Count nulls while leaving floating NaN values distinct."""

    def __call__(self, rule: QualityRuleInput, frame: pl.DataFrame) -> FindingResult:
        params = rule.parameters
        assert isinstance(params, NotNullParams)
        if not _has_column(frame, params.column_name):
            return _missing(rule, params.column_name, frame.height)
        if frame.height == 0:
            return _empty(rule, frame.height)
        nulls = frame.get_column(params.column_name).null_count()
        return _make(
            rule,
            ResultStatus.FAIL if nulls else ResultStatus.PASS,
            f"Column '{params.column_name}' contains {nulls} null values",
            evaluated=frame.height,
            violating=nulls,
            evidence={
                "column": params.column_name,
                "null_count": nulls,
                "nan_counts_as_null": False,
            },
        )


class NullPercentageEvaluator:
    """Enforce an inclusive maximum null percentage."""

    def __call__(self, rule: QualityRuleInput, frame: pl.DataFrame) -> FindingResult:
        params = rule.parameters
        assert isinstance(params, NullPercentageParams)
        if not _has_column(frame, params.column_name):
            return _missing(rule, params.column_name, frame.height)
        if frame.height == 0:
            return _empty(rule, frame.height)
        nulls = frame.get_column(params.column_name).null_count()
        percentage = nulls / frame.height * 100
        passed = percentage <= params.max_percentage
        return _make(
            rule,
            ResultStatus.PASS if passed else ResultStatus.FAIL,
            f"Column '{params.column_name}' has {percentage:.6g}% nulls; "
            f"maximum is {params.max_percentage}%",
            evaluated=frame.height,
            violating=nulls,
            evidence={
                "column": params.column_name,
                "null_count": nulls,
                "null_percentage": percentage,
                "max_percentage": params.max_percentage,
            },
        )


class UniqueEvaluator:
    """Count extra occurrences, with null as one distinct value."""

    def __call__(self, rule: QualityRuleInput, frame: pl.DataFrame) -> FindingResult:
        params = rule.parameters
        assert isinstance(params, UniqueParams)
        if not _has_column(frame, params.column_name):
            return _missing(rule, params.column_name, frame.height)
        if frame.height == 0:
            return _empty(rule, frame.height)
        series = frame.get_column(params.column_name)
        distinct = series.n_unique()
        violations = frame.height - distinct
        return _make(
            rule,
            ResultStatus.FAIL if violations else ResultStatus.PASS,
            f"Column '{params.column_name}' has {violations} duplicate occurrences",
            evaluated=frame.height,
            violating=violations,
            evidence={
                "column": params.column_name,
                "distinct_count": distinct,
                "nulls_count_as_one_distinct_value": True,
            },
        )


class NumericEvaluator:
    """Shared numeric validation and Polars predicate helpers."""

    def evaluate(
        self,
        rule: QualityRuleInput,
        frame: pl.DataFrame,
        column: str,
        predicate: Callable[[pl.Expr], pl.Expr],
        expectation: dict[str, object],
    ) -> FindingResult:
        if not _has_column(frame, column):
            return _missing(rule, column, frame.height)
        series = frame.get_column(column)
        if not series.dtype.is_numeric():
            return _make(
                rule,
                ResultStatus.ERROR,
                f"Column '{column}' has non-numeric type {series.dtype}",
                evaluated=frame.height,
                evidence={"column": column, "observed_type": str(series.dtype), **expectation},
            )
        if frame.height == 0:
            return _empty(rule, frame.height)
        tested = frame.filter(pl.col(column).is_not_null())
        if tested.height == 0:
            return _make(
                rule,
                ResultStatus.ERROR,
                f"Column '{column}' has no non-null numeric values",
                evaluated=frame.height,
                evidence={"column": column, **expectation},
            )
        condition = predicate(pl.col(column))
        if series.dtype in (pl.Float32, pl.Float64):
            condition = condition | pl.col(column).is_nan()
        violations = tested.filter(condition).height
        clean = tested
        non_finite = 0
        if series.dtype in (pl.Float32, pl.Float64):
            clean = tested.filter(pl.col(column).is_finite())
            non_finite = tested.height - clean.height
        observed_min = _json_number(clean.get_column(column).min()) if clean.height else None
        observed_max = _json_number(clean.get_column(column).max()) if clean.height else None
        passed = violations == 0
        return _make(
            rule,
            ResultStatus.PASS if passed else ResultStatus.FAIL,
            f"Column '{column}' {'satisfies' if passed else 'violates'} numeric bounds",
            evaluated=tested.height,
            violating=violations,
            evidence={
                "column": column,
                "observed_minimum": observed_min,
                "observed_maximum": observed_max,
                "non_finite_values": non_finite,
                **expectation,
            },
        )


class MinValueEvaluator(NumericEvaluator):
    """Require all non-null values to meet a lower bound."""

    def __call__(self, rule: QualityRuleInput, frame: pl.DataFrame) -> FindingResult:
        params = rule.parameters
        assert isinstance(params, MinValueParams)
        return self.evaluate(
            rule,
            frame,
            params.column_name,
            lambda value: value < params.minimum,
            {"minimum": params.minimum},
        )


class MaxValueEvaluator(NumericEvaluator):
    """Require all non-null values to meet an upper bound."""

    def __call__(self, rule: QualityRuleInput, frame: pl.DataFrame) -> FindingResult:
        params = rule.parameters
        assert isinstance(params, MaxValueParams)
        return self.evaluate(
            rule,
            frame,
            params.column_name,
            lambda value: value > params.maximum,
            {"maximum": params.maximum},
        )


class RangeEvaluator(NumericEvaluator):
    """Require all non-null values to lie in an inclusive range."""

    def __call__(self, rule: QualityRuleInput, frame: pl.DataFrame) -> FindingResult:
        params = rule.parameters
        assert isinstance(params, RangeParams)
        return self.evaluate(
            rule,
            frame,
            params.column_name,
            lambda value: (value < params.minimum) | (value > params.maximum),
            {"minimum": params.minimum, "maximum": params.maximum, "bounds_inclusive": True},
        )


class AcceptedValuesEvaluator:
    """Require every value, including null, to belong to the configured set."""

    def __call__(self, rule: QualityRuleInput, frame: pl.DataFrame) -> FindingResult:
        params = rule.parameters
        assert isinstance(params, AcceptedValuesParams)
        if not _has_column(frame, params.column_name):
            return _missing(rule, params.column_name, frame.height)
        if frame.height == 0:
            return _empty(rule, frame.height)
        series = frame.get_column(params.column_name)
        try:
            accepted = series.is_in(params.values) & series.is_not_null()
            violations = int((~accepted).sum())
        except (TypeError, pl.exceptions.PolarsError) as exc:
            return _make(
                rule,
                ResultStatus.ERROR,
                f"Accepted values are incompatible with column '{params.column_name}': {exc}",
                evaluated=frame.height,
                evidence={"column": params.column_name, "accepted_values": params.values},
            )
        return _make(
            rule,
            ResultStatus.FAIL if violations else ResultStatus.PASS,
            f"Column '{params.column_name}' has {violations} values outside the accepted set",
            evaluated=frame.height,
            violating=violations,
            evidence={
                "column": params.column_name,
                "accepted_values": params.values,
                "nulls_are_accepted": False,
            },
        )


class DuplicateRowsEvaluator:
    """Reject duplicate complete records, not repeated individual values."""

    def __call__(self, rule: QualityRuleInput, frame: pl.DataFrame) -> FindingResult:
        params = rule.parameters
        assert isinstance(params, DuplicateRowsParams)
        if frame.height == 0:
            return _empty(rule, frame.height)
        duplicates = int(frame.is_duplicated().sum())
        return _make(
            rule,
            ResultStatus.FAIL if duplicates else ResultStatus.PASS,
            f"Dataset contains {duplicates} rows participating in duplicate records",
            evaluated=frame.height,
            violating=duplicates,
            evidence={"duplicate_rows": duplicates, "columns_compared": frame.columns},
        )


EVALUATORS: dict[RuleType, Evaluator] = {
    RuleType.REQUIRED_COLUMN: RequiredColumnEvaluator(),
    RuleType.DATA_TYPE: DataTypeEvaluator(),
    RuleType.NOT_NULL: NotNullEvaluator(),
    RuleType.NULL_PERCENTAGE: NullPercentageEvaluator(),
    RuleType.UNIQUE: UniqueEvaluator(),
    RuleType.MIN_VALUE: MinValueEvaluator(),
    RuleType.MAX_VALUE: MaxValueEvaluator(),
    RuleType.RANGE: RangeEvaluator(),
    RuleType.ACCEPTED_VALUES: AcceptedValuesEvaluator(),
    RuleType.DUPLICATE_ROWS: DuplicateRowsEvaluator(),
}


def _json_number(value: object) -> int | float | None:
    """Keep numeric evidence safe for JSONB, marking non-finite values separately."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return None


def evaluate_rule(rule: QualityRuleInput, frame: pl.DataFrame) -> FindingResult:
    """Dispatch one rule to its dedicated evaluator."""
    return EVALUATORS[rule.rule_type](rule, frame)
