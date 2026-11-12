"""PostgreSQL persistence tests for quality rule histories."""

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import polars as pl
import pytest
from pydantic import TypeAdapter
from sqlalchemy import Engine, text

from sentinel.domain.contracts import DatasetInput
from sentinel.domain.exceptions import RuleConflictError
from sentinel.ingestion.service import DatasetService
from sentinel.persistence.database import create_database_engine, session_factory
from sentinel.quality.contracts import (
    FindingResult,
    QualityRuleInput,
    ResultStatus,
    RuleParameters,
    RuleType,
    Severity,
)
from sentinel.quality.engine import evaluate_rule
from sentinel.quality.service import QualityService
from sentinel.storage.filesystem import FileStore

_RULE_PARAMETERS = TypeAdapter(RuleParameters)


def rule_parameters(value: dict[str, object]) -> RuleParameters:
    return _RULE_PARAMETERS.validate_python(value)


@pytest.fixture
def engine() -> Iterator[Engine]:
    if not os.getenv("SENTINEL_TEST_DATABASE_URL"):
        pytest.skip("SENTINEL_TEST_DATABASE_URL is not configured")
    database_engine = create_database_engine(testing=True)
    with database_engine.connect() as connection:
        name = connection.scalar(text("SELECT current_database()"))
    if name != "sentinel_test":
        database_engine.dispose()
        pytest.fail("Refusing integration test setup: database must be sentinel_test")
    yield database_engine
    database_engine.dispose()


def _ingest_version(engine: Engine, tmp_path: Path) -> tuple[str, UUID, FileStore]:
    slug = f"quality-{uuid4().hex}"
    source = tmp_path / "quality.csv"
    source.write_text("id,status,amount\n1,ok,10\n2,,20\n3,invalid,-1\n", encoding="utf-8")
    storage = FileStore(tmp_path / "store")
    with session_factory(engine)() as session:
        registration = DatasetService(session, storage).ingest(
            source, DatasetInput(slug=slug, display_name="Quality integration")
        )
    return slug, registration.version.id, storage


def test_rule_lifecycle_assessments_history_and_idempotency(engine: Engine, tmp_path: Path) -> None:
    slug, version_id, storage = _ingest_version(engine, tmp_path)
    with session_factory(engine)() as session:
        service = QualityService(session, storage)
        not_null = service.create_rule(
            slug,
            QualityRuleInput(
                name="status required",
                rule_type=RuleType.NOT_NULL,
                parameters=rule_parameters({"kind": "not_null", "column_name": "status"}),
                severity=Severity.WARNING,
            ),
        )
        accepted = service.create_rule(
            slug,
            QualityRuleInput(
                name="status set",
                rule_type=RuleType.ACCEPTED_VALUES,
                parameters=rule_parameters(
                    {"kind": "accepted_values", "column_name": "status", "values": ["ok"]}
                ),
            ),
        )
        assert len(service.get_rules(slug)) == 2
        first = service.evaluate_version(version_id)
        assert first.assessment.status == ResultStatus.FAIL
        assert first.assessment.total_rules == 2
        assert first.assessment.failed_rules == 2
        assert len(first.findings) == 2
        assert service.evaluate_version(version_id).assessment.id == first.assessment.id
        old_snapshot = next(
            finding.rule_snapshot for finding in first.findings if finding.rule_id == not_null.id
        )

        changed_rule = service.update_rule(
            not_null.id,
            QualityRuleInput(
                name="status required",
                rule_type=RuleType.NOT_NULL,
                parameters=rule_parameters({"kind": "not_null", "column_name": "status"}),
                severity=Severity.CRITICAL,
            ),
        )
        assert changed_rule.severity == Severity.CRITICAL
        second = service.evaluate_version(version_id)
        assert second.assessment.id != first.assessment.id
        assert old_snapshot["severity"] == Severity.WARNING

        service.set_rule_enabled(accepted.id, False)
        third = service.evaluate_version(version_id)
        assert third.assessment.id not in {first.assessment.id, second.assessment.id}
        assert third.assessment.total_rules == 1
        assert len(service.assessment_history(slug)) == 3
        assert service.retrieve_assessment(first.assessment.id).findings == first.findings
        assert service.retrieve_findings(first.assessment.id) == first.findings
        with pytest.raises(RuleConflictError, match="historical findings"):
            service.delete_rule(not_null.id)
        assert {item.id for item in service.get_rules(slug)} == {not_null.id, accepted.id}


def test_concurrent_assessment_requests_return_same_record(engine: Engine, tmp_path: Path) -> None:
    slug, version_id, storage = _ingest_version(engine, tmp_path)
    with session_factory(engine)() as session:
        QualityService(session, storage).create_rule(
            slug,
            QualityRuleInput(
                name="ids unique",
                rule_type=RuleType.UNIQUE,
                parameters=rule_parameters({"kind": "unique", "column_name": "id"}),
            ),
        )

    def evaluate_once() -> UUID:
        with session_factory(engine)() as session:
            return QualityService(session, storage).evaluate_version(version_id).assessment.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        assessment_ids = list(executor.map(lambda _: evaluate_once(), range(2)))
    assert assessment_ids[0] == assessment_ids[1]


def test_unexpected_rule_failure_is_recorded_without_dropping_other_findings(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, version_id, storage = _ingest_version(engine, tmp_path)
    with session_factory(engine)() as session:
        service = QualityService(session, storage)
        service.create_rule(
            slug,
            QualityRuleInput(
                name="will raise",
                rule_type=RuleType.NOT_NULL,
                parameters=rule_parameters({"kind": "not_null", "column_name": "status"}),
            ),
        )
        service.create_rule(
            slug,
            QualityRuleInput(
                name="id required",
                rule_type=RuleType.REQUIRED_COLUMN,
                parameters=rule_parameters({"kind": "required_column", "column_name": "id"}),
            ),
        )

    def failing_evaluator(rule: QualityRuleInput, frame: pl.DataFrame) -> FindingResult:
        if rule.name == "will raise":
            raise RuntimeError("simulated evaluator failure")
        return evaluate_rule(rule, frame)

    monkeypatch.setattr("sentinel.quality.service.evaluate_rule", failing_evaluator)
    with session_factory(engine)() as session:
        result = QualityService(session, storage).evaluate_version(version_id)
    assert result.assessment.status == ResultStatus.ERROR
    assert result.assessment.error_rules == 1
    assert result.assessment.passed_rules == 1
    assert len(result.findings) == 2
    assert any(finding.message.startswith("Rule evaluation failed") for finding in result.findings)
