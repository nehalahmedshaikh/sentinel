"""Deterministic unit tests for quality rules and aggregation."""

from uuid import uuid4

import polars as pl
import pytest

from sentinel.quality.aggregation import assessment_summary, rule_snapshot, ruleset_fingerprint
from sentinel.quality.contracts import (
    QualityRuleInput,
    ResultStatus,
    RuleType,
    Severity,
)
from sentinel.quality.engine import evaluate_rule


def rule(
    kind: RuleType,
    parameters: dict[str, object],
    *,
    severity: Severity = Severity.ERROR,
) -> QualityRuleInput:
    return QualityRuleInput(
        name=f"test {kind.value}",
        rule_type=kind,
        parameters={"kind": kind.value, **parameters},
        severity=severity,
    )


def test_rule_parameter_contracts_reject_invalid_bounds_and_shapes() -> None:
    with pytest.raises(ValueError):
        rule(RuleType.NULL_PERCENTAGE, {"column_name": "x", "max_percentage": 101})
    with pytest.raises(ValueError):
        rule(RuleType.RANGE, {"column_name": "x", "minimum": 4, "maximum": 3})
    with pytest.raises(ValueError):
        rule(RuleType.ACCEPTED_VALUES, {"column_name": "x", "values": []})
    with pytest.raises(ValueError):
        rule(RuleType.MIN_VALUE, {"column_name": "x", "minimum": 0, "extra": 1})
    with pytest.raises(ValueError):
        QualityRuleInput(
            name="mismatch",
            rule_type=RuleType.MIN_VALUE,
            parameters={"kind": "max_value", "column_name": "x", "maximum": 3},
        )


def test_required_column_and_data_type_rules() -> None:
    frame = pl.DataFrame({"amount": [1, 2]})
    assert (
        evaluate_rule(rule(RuleType.REQUIRED_COLUMN, {"column_name": "missing"}), frame).status
        == ResultStatus.FAIL
    )
    assert (
        evaluate_rule(rule(RuleType.REQUIRED_COLUMN, {"column_name": "amount"}), frame).status
        == ResultStatus.PASS
    )
    assert (
        evaluate_rule(
            rule(RuleType.DATA_TYPE, {"column_name": "amount", "expected_type": "Int64"}), frame
        ).status
        == ResultStatus.PASS
    )
    assert (
        evaluate_rule(
            rule(RuleType.DATA_TYPE, {"column_name": "amount", "expected_type": "String"}), frame
        ).status
        == ResultStatus.FAIL
    )


def test_null_rules_distinguish_null_from_nan() -> None:
    frame = pl.DataFrame({"x": [None, float("nan"), 2.0]})
    not_null = evaluate_rule(rule(RuleType.NOT_NULL, {"column_name": "x"}), frame)
    assert not_null.status == ResultStatus.FAIL
    assert not_null.violating_records == 1
    assert not_null.evidence["nan_counts_as_null"] is False
    null_percentage = evaluate_rule(
        rule(RuleType.NULL_PERCENTAGE, {"column_name": "x", "max_percentage": 34}), frame
    )
    assert null_percentage.status == ResultStatus.PASS


def test_unique_counts_null_as_one_distinct_value() -> None:
    result = evaluate_rule(
        rule(RuleType.UNIQUE, {"column_name": "x"}), pl.DataFrame({"x": [1, 1, None, None]})
    )
    assert result.status == ResultStatus.FAIL
    assert result.violating_records == 2
    assert result.evidence["nulls_count_as_one_distinct_value"] is True


@pytest.mark.parametrize(
    ("kind", "params", "values", "expected"),
    [
        (RuleType.MIN_VALUE, {"minimum": 0}, [-1, 0, 2], ResultStatus.FAIL),
        (RuleType.MIN_VALUE, {"minimum": 0}, [0, 2], ResultStatus.PASS),
        (RuleType.MAX_VALUE, {"maximum": 2}, [0, 3], ResultStatus.FAIL),
        (RuleType.RANGE, {"minimum": 0, "maximum": 2}, [0, 2], ResultStatus.PASS),
        (RuleType.RANGE, {"minimum": 0, "maximum": 2}, [-1, 2], ResultStatus.FAIL),
    ],
)
def test_numeric_rules(
    kind: RuleType, params: dict[str, object], values: list[int], expected: ResultStatus
) -> None:
    result = evaluate_rule(rule(kind, {"column_name": "x", **params}), pl.DataFrame({"x": values}))
    assert result.status == expected


def test_numeric_incompatible_and_missing_columns_are_errors() -> None:
    numeric = rule(RuleType.MIN_VALUE, {"column_name": "x", "minimum": 0})
    assert evaluate_rule(numeric, pl.DataFrame({"x": ["1"]})).status == ResultStatus.ERROR
    assert evaluate_rule(numeric, pl.DataFrame({"y": [1]})).status == ResultStatus.ERROR


def test_accepted_values_nulls_and_duplicate_full_rows() -> None:
    accepted = rule(RuleType.ACCEPTED_VALUES, {"column_name": "x", "values": ["a", "b"]})
    result = evaluate_rule(accepted, pl.DataFrame({"x": ["a", None, "c"]}))
    assert result.status == ResultStatus.FAIL
    assert result.violating_records == 2
    duplicates = rule(RuleType.DUPLICATE_ROWS, {})
    repeated_value_not_repeated_row = pl.DataFrame({"x": [1, 1], "y": ["a", "b"]})
    exact_duplicate = pl.DataFrame({"x": [1, 1], "y": ["a", "a"]})
    assert evaluate_rule(duplicates, repeated_value_not_repeated_row).status == ResultStatus.PASS
    finding = evaluate_rule(duplicates, exact_duplicate)
    assert finding.status == ResultStatus.FAIL
    assert finding.violating_records == 2


def test_empty_dataset_semantics_and_empty_ruleset() -> None:
    empty = pl.DataFrame(schema={"x": pl.Int64})
    assert (
        evaluate_rule(
            rule(RuleType.DATA_TYPE, {"column_name": "x", "expected_type": "Int64"}), empty
        ).status
        == ResultStatus.PASS
    )
    assert (
        evaluate_rule(rule(RuleType.NOT_NULL, {"column_name": "x"}), empty).status
        == ResultStatus.ERROR
    )
    status, total, passed, failed, errors, message = assessment_summary([], [])
    assert (status, total, passed, failed, errors) == (ResultStatus.ERROR, 0, 0, 0, 0)
    assert message is not None and "No enabled rules" in message


def test_assessment_status_precedence_and_severity_independence() -> None:
    warning_failure = rule(RuleType.NOT_NULL, {"column_name": "x"}, severity=Severity.WARNING)
    failed = evaluate_rule(warning_failure, pl.DataFrame({"x": [None]}))
    assert failed.status == ResultStatus.FAIL
    assert failed.severity == Severity.WARNING
    errored = evaluate_rule(
        rule(RuleType.MIN_VALUE, {"column_name": "x", "minimum": 0}),
        pl.DataFrame({"x": ["bad"]}),
    )
    assert assessment_summary([failed, errored], [{}, {}])[0] == ResultStatus.FAIL


def test_ruleset_fingerprint_is_stable_and_sensitive_to_rule_settings() -> None:
    rule_id = str(uuid4())
    base = rule(RuleType.NOT_NULL, {"column_name": "x"})
    snapshot = rule_snapshot(rule_id, base)
    assert ruleset_fingerprint([snapshot]) == ruleset_fingerprint([snapshot])
    changed = rule_snapshot(
        rule_id, rule(RuleType.NOT_NULL, {"column_name": "x"}, severity=Severity.CRITICAL)
    )
    assert ruleset_fingerprint([snapshot]) != ruleset_fingerprint([changed])
    second = rule_snapshot(str(uuid4()), base)
    assert ruleset_fingerprint([snapshot, second]) == ruleset_fingerprint([second, snapshot])
