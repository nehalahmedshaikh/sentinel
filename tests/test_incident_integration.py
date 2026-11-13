"""PostgreSQL integration coverage for incident generation and management."""

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter
from sqlalchemy import Engine, text, update
from sqlalchemy.exc import DBAPIError

from sentinel.domain.contracts import DatasetInput
from sentinel.incidents.contracts import (
    IncidentPolicyConfig,
    IncidentPolicyInput,
    IncidentSeverity,
    IncidentStatus,
    LinkInput,
    MergeInput,
    NoteInput,
    ResolveInput,
    SeverityInput,
    SourceType,
)
from sentinel.incidents.models import IncidentEventRow, IncidentEvidenceRow
from sentinel.incidents.service import IncidentService
from sentinel.ingestion.service import DatasetService
from sentinel.persistence.database import create_database_engine, session_factory
from sentinel.quality.contracts import QualityRuleInput, RuleParameters, RuleType, Severity
from sentinel.quality.service import QualityService
from sentinel.reliability.contracts import (
    DetectionPolicyInput,
    ReliabilityAssessmentInput,
)
from sentinel.reliability.service import ReliabilityService
from sentinel.storage.filesystem import FileStore

_RULE_PARAMETERS = TypeAdapter(RuleParameters)


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


def _setup(engine: Engine, root: Path) -> tuple[str, list[UUID], FileStore]:
    slug = f"incidents-{uuid4().hex}"
    store = FileStore(root / "managed")
    versions = []
    for i, contents in enumerate(
        (
            "id,status\n1,ok\n",
            "id,status\n1,\n",
            "id,status,extra\n1,,x\n2,,y\n",
        ),
        start=1,
    ):
        path = root / f"source-{i}.csv"
        path.write_text(contents, encoding="utf-8")
        with session_factory(engine)() as session:
            result = DatasetService(session, store).ingest(
                path, DatasetInput(slug=slug, display_name="Incident test")
            )
        versions.append(result.version.id)
    return slug, versions, store


def test_incident_generation_correlation_lifecycle_report_and_history(
    engine: Engine, tmp_path: Path
) -> None:
    slug, versions, storage = _setup(engine, tmp_path)
    with session_factory(engine)() as session:
        quality = QualityService(session, storage)
        quality.create_rule(
            slug,
            QualityRuleInput(
                name="status is required",
                rule_type=RuleType.NOT_NULL,
                parameters=_RULE_PARAMETERS.validate_python(
                    {"kind": "not_null", "column_name": "status"}
                ),
                severity=Severity.CRITICAL,
            ),
        )
        assessment1 = quality.evaluate_version(versions[1]).assessment
        assessment2 = quality.evaluate_version(versions[2]).assessment

    policy_config = IncidentPolicyConfig(
        minimum_source_severities={"quality": ["ERROR"], "reliability": ["WARNING"]}
    )
    with session_factory(engine)() as session:
        service = IncidentService(session)
        policy = service.create_policy(
            slug, IncidentPolicyInput(name="default", config=policy_config)
        )
        generated = service.process_quality_assessment(assessment1.id, policy.id)
        assert generated.decisions[0].qualifies
        incident = generated.incidents[0]
        second = service.process_quality_assessment(assessment2.id, policy.id)
        assert second.incidents[0].id == incident.id
        repeated = service.process_quality_assessment(assessment2.id, policy.id)
        assert repeated.incidents[0].id == incident.id
        detail = service.retrieve(incident.id)
        assert len(detail.evidence) == 2
        assert len({item.source_type for item in detail.evidence}) == 1
        assert detail.incident.severity == IncidentSeverity.CRITICAL
        append_only_event_id = detail.events[0].id
        append_only_evidence_id = detail.evidence[0].id

    with session_factory(engine)() as session:
        reliability = ReliabilityService(session, storage)
        detection_policy = reliability.create_policy(
            slug, DetectionPolicyInput(name="schema changes")
        )
        reliability_assessment = reliability.run_assessment(
            ReliabilityAssessmentInput(
                dataset_version_id=versions[2], policy_id=detection_policy.id
            )
        )
        assert any(item.status.value == "DETECTED" for item in reliability_assessment.findings)
        result = IncidentService(session).process_reliability_assessment(
            reliability_assessment.assessment.id, policy.id
        )
        assert any(item.source_type == SourceType.RELIABILITY for item in result.decisions)

    # Separate sessions concurrently process the same assessment; evidence uniqueness and
    # dataset serialization ensure a single incident and one association.
    def process() -> object:
        with session_factory(engine)() as session:
            return IncidentService(session).process_quality_assessment(assessment2.id, policy.id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: process(), range(2)))

    with session_factory(engine)() as session:
        service = IncidentService(session)
        initial_incidents = service.list_incidents(slug)
        assert len(initial_incidents) >= 2
        non_quality = [row for row in initial_incidents if row.id != incident.id]
        if len(non_quality) >= 2:
            survivor_id, retired_id = non_quality[0].id, non_quality[1].id
            evidence_before = len(service.retrieve(survivor_id).evidence) + len(
                service.retrieve(retired_id).evidence
            )
            merged = service.merge(
                retired_id,
                MergeInput(surviving_incident_id=survivor_id, reason="same source event"),
            )
            assert merged.id == survivor_id
            assert len(service.retrieve(survivor_id).evidence) == evidence_before
            service.link(
                incident.id,
                LinkInput(related_incident_id=survivor_id, reason="related dataset symptoms"),
            )
        acknowledged = service.acknowledge(incident.id)
        assert acknowledged.status == IncidentStatus.ACKNOWLEDGED
        investigating = service.start_investigation(incident.id)
        assert investigating.status == IncidentStatus.INVESTIGATING
        service.set_severity(
            incident.id, SeverityInput(severity=IncidentSeverity.HIGH, reason="manual review")
        )
        service.add_note(incident.id, NoteInput(note="checked source pipeline", author="test"))
        resolved = service.resolve(
            incident.id, ResolveInput(summary="fixed", reason="source record corrected")
        )
        assert resolved.status == IncidentStatus.RESOLVED
        report = service.report(incident.id)
        assert report.affected_columns == ["status"]
        assert len(report.evidence) == 2
        assert report.hypotheses
        assert service.historical_summary(slug).incident_count == len(initial_incidents)
        event_count_before_invalid_transition = len(service.retrieve(incident.id).events)
        with pytest.raises(ValueError, match="Invalid incident transition"):
            service.acknowledge(incident.id)
        assert len(service.retrieve(incident.id).events) == event_count_before_invalid_transition
        reopened = service.reopen(incident.id, "same failure observed on next version")
        assert reopened.status == IncidentStatus.REOPENED
        assert reopened.resolved_at is not None
        assert service.report(incident.id).resolution is not None

    with session_factory(engine)() as session:
        with pytest.raises(DBAPIError, match="incident history rows are append-only"):
            with session.begin():
                session.execute(
                    update(IncidentEventRow)
                    .where(IncidentEventRow.id == append_only_event_id)
                    .values(details={"tampered": True})
                )
    with session_factory(engine)() as session:
        with pytest.raises(DBAPIError, match="incident history rows are append-only"):
            with session.begin():
                session.execute(
                    update(IncidentEvidenceRow)
                    .where(IncidentEvidenceRow.id == append_only_evidence_id)
                    .values(finding_type="tampered")
                )


def test_nonqualifying_quality_pass_does_not_create_incident(
    engine: Engine, tmp_path: Path
) -> None:
    slug, versions, storage = _setup(engine, tmp_path)
    with session_factory(engine)() as session:
        quality = QualityService(session, storage)
        quality.create_rule(
            slug,
            QualityRuleInput(
                name="id is required",
                rule_type=RuleType.NOT_NULL,
                parameters=_RULE_PARAMETERS.validate_python(
                    {"kind": "not_null", "column_name": "id"}
                ),
                severity=Severity.CRITICAL,
            ),
        )
        assessment = quality.evaluate_version(versions[0]).assessment
    with session_factory(engine)() as session:
        service = IncidentService(session)
        policy = service.create_policy(slug, IncidentPolicyInput(name="strict"))
        result = service.process_quality_assessment(assessment.id, policy.id)
        assert result.decisions[0].qualifies is False
        assert result.incidents == []
