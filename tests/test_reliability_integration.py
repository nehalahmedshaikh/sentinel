"""PostgreSQL tests for baseline resolution and persisted reliability intelligence."""

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter
from sqlalchemy import Engine, text

from sentinel.domain.contracts import DatasetInput
from sentinel.ingestion.service import DatasetService
from sentinel.persistence.database import create_database_engine, session_factory
from sentinel.quality.contracts import QualityRuleInput, RuleParameters, RuleType
from sentinel.quality.service import QualityService
from sentinel.reliability.contracts import (
    AssessmentStatus,
    BaselineStrategy,
    CategoricalDriftPolicy,
    DetectionPolicyConfig,
    DetectionPolicyInput,
    NumericDriftPolicy,
    ReliabilityAssessmentInput,
)
from sentinel.reliability.service import ReliabilityService
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


def _ingest_versions(
    engine: Engine, root: Path, count: int = 3
) -> tuple[str, list[UUID], FileStore]:
    slug = f"reliability-{uuid4().hex}"
    storage = FileStore(root / "managed")
    versions: list[UUID] = []
    for version_number in range(1, count + 1):
        path = root / f"version-{version_number}.csv"
        has_new_column = version_number > 1
        headers = "id,amount,status" + (",new_field" if has_new_column else "")
        rows: list[str] = []
        for row_number in range(1, 11):
            amount = row_number + (version_number - 1) * 100
            if version_number > 1 and row_number == 2:
                status = ""
            else:
                status = "changed" if version_number > 1 and row_number == 1 else "ok"
            row = f"{row_number},{amount},{status}"
            if has_new_column:
                row += ",present"
            rows.append(row)
        path.write_text(headers + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
        with session_factory(engine)() as session:
            result = DatasetService(session, storage).ingest(
                path, DatasetInput(slug=slug, display_name="Reliability integration")
            )
        versions.append(result.version.id)
    return slug, versions, storage


def _policy_config() -> DetectionPolicyConfig:
    return DetectionPolicyConfig(
        numeric_drift=NumericDriftPolicy(
            minimum_samples=5,
            psi_threshold=0.1,
            wasserstein_threshold=1,
            ks_statistic_threshold=0.1,
        ),
        categorical_drift=CategoricalDriftPolicy(
            minimum_samples=5, js_divergence_threshold=0.01, psi_threshold=0.1
        ),
    )


def test_baseline_strategies_policy_history_onset_and_investigation(
    engine: Engine, tmp_path: Path
) -> None:
    slug, versions, storage = _ingest_versions(engine, tmp_path)
    with session_factory(engine)() as session:
        reliability = ReliabilityService(session, storage)
        policy = reliability.create_policy(
            slug, DetectionPolicyInput(name="baseline policy", config=_policy_config())
        )
        first_request = ReliabilityAssessmentInput(
            dataset_version_id=versions[0], policy_id=policy.id
        )
        initial = reliability.run_assessment(first_request)
        assert initial.assessment.status == AssessmentStatus.NO_BASELINE
        assert initial.assessment.baseline_versions == []
        assert reliability.run_assessment(first_request).assessment.id == initial.assessment.id

    with session_factory(engine)() as session:
        quality = QualityService(session, storage)
        rule = quality.create_rule(
            slug,
            QualityRuleInput(
                name="status cannot be null",
                rule_type=RuleType.NOT_NULL,
                parameters=rule_parameters({"kind": "not_null", "column_name": "status"}),
            ),
        )
        quality.evaluate_version(versions[0])
        quality.evaluate_version(versions[1])

    with session_factory(engine)() as session:
        reliability = ReliabilityService(session, storage)
        previous_request = ReliabilityAssessmentInput(
            dataset_version_id=versions[1], policy_id=policy.id
        )
        previous = reliability.run_assessment(previous_request)
        assert previous.assessment.baseline_strategy == BaselineStrategy.PREVIOUS
        assert [item.id for item in previous.assessment.baseline_versions] == [versions[0]]
        assert any(item.finding_type == "column_added" for item in previous.findings)
        assert any(
            item.finding_type == "categorical_distribution_drift"
            and item.status.value == "DETECTED"
            for item in previous.findings
        )

        explicit = reliability.run_assessment(
            ReliabilityAssessmentInput(
                dataset_version_id=versions[1],
                policy_id=policy.id,
                baseline_strategy=BaselineStrategy.EXPLICIT,
                explicit_baseline_version_id=versions[0],
            )
        )
        assert explicit.assessment.id != previous.assessment.id
        rolling = reliability.run_assessment(
            ReliabilityAssessmentInput(
                dataset_version_id=versions[2],
                policy_id=policy.id,
                baseline_strategy=BaselineStrategy.ROLLING,
                rolling_window=2,
            )
        )
        assert [item.id for item in rolling.assessment.baseline_versions] == versions[:2]
        initial_strategy = reliability.run_assessment(
            ReliabilityAssessmentInput(
                dataset_version_id=versions[2],
                policy_id=policy.id,
                baseline_strategy=BaselineStrategy.INITIAL,
            )
        )
        assert [item.id for item in initial_strategy.assessment.baseline_versions] == [versions[0]]
        assert reliability.change_onset(slug, "schema_drift", "column_added", "new_field")
        investigation = reliability.investigate(versions[1])
        assert "status" in investigation.affected_columns
        assert any(
            item.kind == "quality_deterioration_with_change" for item in investigation.correlations
        )
        assert all(item.causal_claim is False for item in investigation.correlations)
        assert previous.assessment.id in {
            item.assessment.id for item in reliability.assessment_history(slug)
        }
        assert reliability.retrieve_assessment(previous.assessment.id).findings == previous.findings

        second_dataset = f"other-{uuid4().hex}"
        other_path = tmp_path / "other.csv"
        other_path.write_text("x\n1\n", encoding="utf-8")
        other = DatasetService(session, storage).ingest(
            other_path, DatasetInput(slug=second_dataset, display_name="Other")
        )
        with pytest.raises(ValueError, match="same dataset"):
            reliability.run_assessment(
                ReliabilityAssessmentInput(
                    dataset_version_id=versions[1],
                    policy_id=policy.id,
                    baseline_strategy=BaselineStrategy.EXPLICIT,
                    explicit_baseline_version_id=other.version.id,
                )
            )

        changed_config = _policy_config().model_copy(
            update={"numeric_drift": NumericDriftPolicy(psi_threshold=0.4)}
        )
        updated_policy = reliability.update_policy(
            policy.id, DetectionPolicyInput(name=policy.name, config=changed_config)
        )
        assert updated_policy.fingerprint != policy.fingerprint
        changed_assessment = reliability.run_assessment(previous_request)
        assert changed_assessment.assessment.id != previous.assessment.id
        assert (
            reliability.retrieve_assessment(previous.assessment.id).assessment.policy_fingerprint
            == policy.fingerprint
        )
        assert quality.get_rules(slug)[0].id == rule.id


def test_concurrent_reliability_requests_are_idempotent(engine: Engine, tmp_path: Path) -> None:
    slug, versions, storage = _ingest_versions(engine, tmp_path, count=2)
    with session_factory(engine)() as session:
        policy = ReliabilityService(session, storage).create_policy(
            slug, DetectionPolicyInput(name="concurrent", config=_policy_config())
        )
    request = ReliabilityAssessmentInput(dataset_version_id=versions[1], policy_id=policy.id)

    def assess_once() -> UUID:
        with session_factory(engine)() as session:
            return ReliabilityService(session, storage).run_assessment(request).assessment.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        identities = list(executor.map(lambda _: assess_once(), range(2)))
    assert identities[0] == identities[1]
