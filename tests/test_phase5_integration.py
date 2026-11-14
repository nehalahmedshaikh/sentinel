"""PostgreSQL coverage for persistent jobs, outbox dispatch, and schedule identity."""

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text, update

from sentinel.api.app import create_app
from sentinel.domain.contracts import DatasetInput, DatasetView
from sentinel.ingestion.service import DatasetService
from sentinel.operations.contracts import JobType, JobView, ScheduleInput
from sentinel.operations.models import JobRow, OutboxRow, ScheduleExecutionRow
from sentinel.operations.service import OperationsService, dispatch_due_schedules, dispatch_outbox
from sentinel.persistence.database import create_database_engine, session_factory
from sentinel.settings import Settings
from sentinel.storage.filesystem import FileStore


@pytest.fixture
def engine() -> Iterator[Engine]:
    if not os.getenv("SENTINEL_TEST_DATABASE_URL"):
        pytest.skip("SENTINEL_TEST_DATABASE_URL is not configured")
    database_engine = create_database_engine(testing=True)
    with database_engine.connect() as connection:
        name = connection.scalar(text("SELECT current_database()"))
    if name != "sentinel_test":
        database_engine.dispose()
        pytest.fail("Refusing Phase 5 integration tests outside sentinel_test")
    yield database_engine
    database_engine.dispose()


def _dataset(engine: Engine, root: Path) -> DatasetView:
    path = root / "dataset.csv"
    path.write_text("value\n1\n", encoding="utf-8")
    with session_factory(engine)() as session:
        result = DatasetService(session, FileStore(root / "managed")).ingest(
            path,
            DatasetInput(slug=f"operations-{uuid4().hex}", display_name="Operations"),
        )
        return result.dataset


def test_job_idempotency_and_outbox_retry(engine: Engine) -> None:
    key = f"phase5-job-{uuid4()}"
    with session_factory(engine)() as session:
        first = OperationsService(session).enqueue(
            JobType.QUALITY, {"version_id": str(uuid4())}, idempotency_key=key
        )
    with session_factory(engine)() as session:
        second = OperationsService(session).enqueue(
            JobType.QUALITY, {"version_id": str(uuid4())}, idempotency_key=key
        )
        assert first.id == second.id

    class FailingSender:
        def send_task(self, task_name: str, *, args: list[str]) -> None:
            raise ConnectionError("broker unavailable")

    class Sender:
        published: list[tuple[str, list[str]]]

        def __init__(self) -> None:
            self.published = []

        def send_task(self, task_name: str, *, args: list[str]) -> None:
            self.published.append((task_name, args))

    with session_factory(engine)() as session:
        assert dispatch_outbox(session, FailingSender()) == 0
    sender = Sender()
    with session_factory(engine)() as session:
        assert dispatch_outbox(session, sender) >= 1
    with session_factory(engine)() as session:
        assert dispatch_outbox(session, sender) == 0
    assert any(args == [str(first.id)] for _, args in sender.published)


def test_concurrent_job_enqueue_returns_single_idempotent_job(engine: Engine) -> None:
    key = f"concurrent-job-{uuid4()}"

    def enqueue() -> JobView:
        with session_factory(engine)() as session:
            return OperationsService(session).enqueue(
                JobType.QUALITY, {"version_id": str(uuid4())}, idempotency_key=key
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: enqueue(), range(2)))
    assert results[0].id == results[1].id


def test_schedule_dispatch_uses_one_job_per_occurrence(engine: Engine, tmp_path: Path) -> None:
    dataset = _dataset(engine, tmp_path)
    with session_factory(engine)() as session:
        schedule = OperationsService(session).create_schedule(
            ScheduleInput(dataset_id=dataset.id, interval_seconds=60)
        )
    with session_factory(engine)() as session:
        from sentinel.operations.models import MonitoringScheduleRow, utcnow

        session.execute(
            update(MonitoringScheduleRow)
            .where(MonitoringScheduleRow.id == schedule.id)
            .values(next_run_at=utcnow())
        )
        session.commit()
        assert dispatch_due_schedules(session) >= 1
        dispatch_due_schedules(session)
        jobs = list(
            session.scalars(
                select(JobRow).where(JobRow.idempotency_key.like(f"schedule:{schedule.id}:%"))
            )
        )
        executions = list(
            session.scalars(
                select(ScheduleExecutionRow).where(ScheduleExecutionRow.schedule_id == schedule.id)
            )
        )
        outbox = session.scalar(select(OutboxRow).where(OutboxRow.job_id == jobs[0].id))
        assert len(jobs) == len(executions) == 1
        assert outbox is not None and outbox.published_at is None


def test_schedule_soft_delete_preserves_execution_history(engine: Engine, tmp_path: Path) -> None:
    dataset = _dataset(engine, tmp_path)
    with session_factory(engine)() as session:
        schedule = OperationsService(session).create_schedule(
            ScheduleInput(dataset_id=dataset.id, interval_seconds=60)
        )
        OperationsService(session).delete_schedule(schedule.id)
        assert OperationsService(session).list_schedules(dataset.id) == []


def test_concurrent_schedule_dispatch_claims_occurrence_once(
    engine: Engine, tmp_path: Path
) -> None:
    dataset = _dataset(engine, tmp_path)
    with session_factory(engine)() as session:
        schedule = OperationsService(session).create_schedule(
            ScheduleInput(dataset_id=dataset.id, interval_seconds=60)
        )
        from sentinel.operations.models import MonitoringScheduleRow, utcnow

        session.execute(
            update(MonitoringScheduleRow)
            .where(MonitoringScheduleRow.id == schedule.id)
            .values(next_run_at=utcnow())
        )
        session.commit()

    def dispatch() -> int:
        with session_factory(engine)() as session:
            return dispatch_due_schedules(session)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: dispatch(), range(2)))
    with session_factory(engine)() as session:
        executions = list(
            session.scalars(
                select(ScheduleExecutionRow).where(ScheduleExecutionRow.schedule_id == schedule.id)
            )
        )
        assert len(executions) == 1


def test_duplicate_worker_delivery_executes_persisted_job_once(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SENTINEL_DATABASE_URL", str(engine.url))
    monkeypatch.setenv("SENTINEL_API_KEY", "phase-five-worker-test-secret")
    monkeypatch.setenv("SENTINEL_DATA_DIR", str(tmp_path))
    from sentinel.operations import tasks

    monkeypatch.setattr(tasks, "make_session", session_factory(engine))
    calls: list[object] = []

    def fake_run_service(
        kind: JobType, job_id: object, payload: dict[str, object]
    ) -> dict[str, object]:
        calls.append(job_id)
        return {}

    monkeypatch.setattr(tasks, "_run_service", fake_run_service)
    with session_factory(engine)() as session:
        job = OperationsService(session).enqueue(
            JobType.QUALITY, {"test": True}, idempotency_key=f"worker:{uuid4()}"
        )

    tasks._execute(job.id)
    tasks._execute(job.id)
    with session_factory(engine)() as session:
        persisted = session.get(JobRow, job.id)
        assert persisted is not None
        assert persisted.status == "SUCCEEDED"
        assert persisted.attempt_count == 1
    assert calls == [job.id]


def test_transient_failure_retry_and_stale_worker_recovery(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta

    monkeypatch.setenv("SENTINEL_DATABASE_URL", str(engine.url))
    monkeypatch.setenv("SENTINEL_API_KEY", "phase-five-worker-test-secret")
    monkeypatch.setenv("SENTINEL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SENTINEL_TASK_MAX_RETRIES", "2")
    monkeypatch.setenv("SENTINEL_STALE_JOB_SECONDS", "60")
    from sentinel.operations import tasks
    from sentinel.operations.models import utcnow
    from sentinel.settings import get_settings

    get_settings.cache_clear()

    monkeypatch.setattr(tasks, "make_session", session_factory(engine))

    def fail_transiently(
        kind: JobType, job_id: object, payload: dict[str, object]
    ) -> dict[str, object]:
        raise ConnectionError("temporary broker-side service failure")

    monkeypatch.setattr(tasks, "_run_service", fail_transiently)
    with session_factory(engine)() as session:
        job = OperationsService(session).enqueue(
            JobType.QUALITY, {"test": True}, idempotency_key=f"retry:{uuid4()}"
        )
    tasks._execute(job.id)
    with session_factory(engine)() as session:
        persisted = session.get(JobRow, job.id)
        outbox = session.scalar(select(OutboxRow).where(OutboxRow.job_id == job.id))
        assert persisted is not None and persisted.status == "QUEUED"
        assert persisted.next_retry_at is not None
        assert outbox is not None and outbox.published_at is None
        session.execute(
            update(JobRow)
            .where(JobRow.id == job.id)
            .values(status="RUNNING", started_at=utcnow() - timedelta(hours=1))
        )
        session.execute(
            update(OutboxRow).where(OutboxRow.job_id == job.id).values(published_at=utcnow())
        )
        session.commit()
    assert tasks.recover_stale_jobs() >= 1
    with session_factory(engine)() as session:
        persisted = session.get(JobRow, job.id)
        outbox = session.scalar(select(OutboxRow).where(OutboxRow.job_id == job.id))
        assert persisted is not None and persisted.status == "QUEUED"
        assert persisted.progress["stage"] == "recovered_after_worker_interruption"
        assert outbox is not None and outbox.published_at is None
    get_settings.cache_clear()


def test_dashboard_core_pages_render_against_persisted_services(
    engine: Engine, tmp_path: Path
) -> None:
    dataset = _dataset(engine, tmp_path)
    settings = Settings(
        database_url=str(engine.url),
        data_dir=str(tmp_path / "dashboard-storage"),
        api_key="phase-five-dashboard-secret",
    )
    with TestClient(create_app(settings)) as client:
        for path in (
            "/",
            "/dashboard/datasets",
            f"/dashboard/datasets/{dataset.slug}",
            "/dashboard/incidents",
            "/dashboard/jobs",
            "/dashboard/schedules",
        ):
            response = client.get(path)
            assert response.status_code == 200, (path, response.text)
        incident_page = client.get("/dashboard/incidents?status=OPEN")
        assert incident_page.status_code == 200
        assert 'value="" disabled selected>Any dataset</option>' in incident_page.text


def test_incident_policy_api_and_real_assessment_generation(engine: Engine, tmp_path: Path) -> None:
    from pydantic import TypeAdapter

    from sentinel.incidents.contracts import IncidentPolicyConfig
    from sentinel.incidents.service import IncidentService
    from sentinel.quality.contracts import QualityRuleInput, RuleParameters, RuleType, Severity
    from sentinel.quality.service import QualityService

    source = tmp_path / "policy-dataset.csv"
    source.write_text("value\n\n", encoding="utf-8")
    storage = FileStore(tmp_path / "policy-managed")
    with session_factory(engine)() as session:
        registration = DatasetService(session, storage).ingest(
            source,
            DatasetInput(slug=f"policy-{uuid4().hex}", display_name="Policy test"),
        )
        QualityService(session, storage).create_rule(
            registration.dataset.slug,
            QualityRuleInput(
                name="required value",
                rule_type=RuleType.NOT_NULL,
                parameters=TypeAdapter(RuleParameters).validate_python(
                    {"kind": "not_null", "column_name": "value"}
                ),
                severity=Severity.CRITICAL,
            ),
        )
        assessment = (
            QualityService(session, storage).evaluate_version(registration.version.id).assessment
        )

    settings = Settings(
        database_url=str(engine.url),
        data_dir=str(tmp_path),
        api_key="phase-five-policy-api-secret",
    )
    headers = {"Authorization": f"Bearer {settings.api_key}"}
    with TestClient(create_app(settings)) as client:
        collection = f"/api/v1/incidents/datasets/{registration.dataset.slug}/policies"
        assert client.get(collection, headers=headers).json() == []
        created = client.post(collection, headers=headers, json={"name": "default"})
        assert created.status_code == 201
        policy = created.json()
        assert policy["config"]["minimum_source_severities"] == {
            "quality": ["ERROR", "CRITICAL"],
            "reliability": ["ERROR", "CRITICAL"],
        }
        assert [item["id"] for item in client.get(collection, headers=headers).json()] == [
            policy["id"]
        ]

        updated = client.put(
            f"/api/v1/incidents/policies/{policy['id']}",
            headers=headers,
            json={
                "name": "default",
                "enabled": True,
                "config": IncidentPolicyConfig().model_dump(mode="json"),
            },
        )
        assert updated.status_code == 200
        assert (
            client.post(
                f"/api/v1/incidents/policies/{policy['id']}/enabled?enabled=false",
                headers=headers,
            ).json()["enabled"]
            is False
        )
        assert (
            client.post(
                f"/api/v1/incidents/policies/{policy['id']}/enabled?enabled=true",
                headers=headers,
            ).json()["enabled"]
            is True
        )

        with session_factory(engine)() as session:
            generation = IncidentService(session).process_quality_assessment(
                assessment.id, UUID(policy["id"])
            )
            assert generation.decisions
            assert generation.incidents

        payload = {
            "source_type": "quality",
            "assessment_id": str(assessment.id),
            "policy_id": policy["id"],
        }
        queued = client.post("/api/v1/incidents/generate", headers=headers, json=payload)
        assert queued.status_code == 202
        repeated = client.post("/api/v1/incidents/generate", headers=headers, json=payload)
        assert repeated.json()["id"] == queued.json()["id"]

        page = client.get(f"/dashboard/datasets/{registration.dataset.slug}")
        assert page.status_code == 200
        assert "Incident-generation policies" in page.text
        assert f'value="{policy["id"]}"' in page.text
        dashboard_created = client.post(
            f"/dashboard/datasets/{registration.dataset.slug}/incident-policies",
            data={"name": "dashboard-default", "config_json": ""},
            follow_redirects=False,
        )
        assert dashboard_created.status_code == 303
        dashboard_policies = client.get(collection, headers=headers).json()
        dashboard_policy = next(
            item for item in dashboard_policies if item["name"] == "dashboard-default"
        )
        assert dashboard_policy["config"]["correlation_window_hours"] == 72
        disabled = client.post(
            f"/dashboard/incident-policies/{dashboard_policy['id']}/enabled",
            data={"enabled": "false"},
            follow_redirects=False,
        )
        assert disabled.status_code == 303
        form_job = client.post(
            f"/dashboard/datasets/{registration.dataset.slug}/incidents/generate",
            data={
                "source_type": "quality",
                "assessment_id": str(assessment.id),
                "policy_id": policy["id"],
            },
            follow_redirects=False,
        )
        assert form_job.status_code == 303
        assert form_job.headers["location"] == f"/dashboard/datasets/{registration.dataset.slug}"


def test_ai_investigation_persists_validated_evidence_references(
    engine: Engine, tmp_path: Path
) -> None:
    import json

    from pydantic import TypeAdapter

    from sentinel.ai.contracts import (
        ChatChoice,
        ChatCompletion,
        ChatMessage,
        ChatRequest,
        InvestigationRequest,
    )
    from sentinel.ai.provider import ProviderError
    from sentinel.ai.service import (
        queue_investigation,
        retrieve_investigation,
        run_investigation,
    )
    from sentinel.incidents.contracts import IncidentPolicyInput
    from sentinel.incidents.service import IncidentService
    from sentinel.quality.contracts import QualityRuleInput, RuleParameters, RuleType, Severity
    from sentinel.quality.service import QualityService

    data_path = tmp_path / "ai-data.csv"
    data_path.write_text("value\n\n", encoding="utf-8")
    storage = FileStore(tmp_path / "managed")
    with session_factory(engine)() as session:
        registration = DatasetService(session, storage).ingest(
            data_path,
            DatasetInput(slug=f"ai-{uuid4().hex}", display_name="AI test"),
        )
        QualityService(session, storage).create_rule(
            registration.dataset.slug,
            QualityRuleInput(
                name="value required",
                rule_type=RuleType.NOT_NULL,
                parameters=TypeAdapter(RuleParameters).validate_python(
                    {"kind": "not_null", "column_name": "value"}
                ),
                severity=Severity.CRITICAL,
            ),
        )
        quality_result = QualityService(session, storage).evaluate_version(registration.version.id)
        policy = IncidentService(session).create_policy(
            registration.dataset.slug, IncidentPolicyInput(name="AI test policy")
        )
        generation = IncidentService(session).process_quality_assessment(
            quality_result.assessment.id, policy.id
        )
        assert generation.incidents
        incident_id = generation.incidents[0].id
        settings = Settings(
            database_url=str(engine.url),
            data_dir=str(tmp_path),
            api_key="phase-five-ai-test-secret",
            ai_enabled=True,
            ai_base_url="http://model.invalid/v1",
            ai_model="test-model",
        )
        request = InvestigationRequest(question="Summarize the observed rule result.")
        queued = queue_investigation(session, incident_id, request, settings)
        repeated = queue_investigation(session, incident_id, request, settings)
        assert queued.id == repeated.id

        class EvidenceProvider:
            def complete(self, chat_request: ChatRequest) -> ChatCompletion:
                message = chat_request.messages[-1]
                payload = json.loads(message.content)
                evidence_id = payload["evidence"][0]["evidence_id"]
                response = {
                    "executive_summary": "The required value check failed.",
                    "observations": [
                        {
                            "statement": "The value required rule failed.",
                            "evidence_ids": [evidence_id],
                        }
                    ],
                    "hypotheses": [],
                    "uncertainty": ["The source of the missing value is unknown."],
                    "next_steps": ["Inspect the upstream input."],
                }
                return ChatCompletion(
                    choices=[
                        ChatChoice(
                            message=ChatMessage(role="assistant", content=json.dumps(response))
                        )
                    ]
                )

        result = run_investigation(
            session,
            queued.job_id,
            incident_id,
            {"question": request.question},
            settings=settings,
            provider=EvidenceProvider(),
        )
        assert result["investigation_id"] == str(queued.id)

        invalid_request = InvestigationRequest(question="Check reference validation.")
        invalid_queued = queue_investigation(session, incident_id, invalid_request, settings)

        class InvalidReferenceProvider:
            def complete(self, chat_request: ChatRequest) -> ChatCompletion:
                response = {
                    "executive_summary": "A fabricated reference is included.",
                    "observations": [
                        {
                            "statement": "This evidence was not supplied.",
                            "evidence_ids": [str(uuid4())],
                        }
                    ],
                    "hypotheses": [],
                    "uncertainty": [],
                    "next_steps": [],
                }
                return ChatCompletion(
                    choices=[
                        ChatChoice(
                            message=ChatMessage(role="assistant", content=json.dumps(response))
                        )
                    ]
                )

        with pytest.raises(ProviderError, match="ValueError"):
            run_investigation(
                session,
                invalid_queued.job_id,
                incident_id,
                {"question": invalid_request.question},
                settings=settings,
                provider=InvalidReferenceProvider(),
            )
        failed = retrieve_investigation(session, invalid_queued.id)
        assert failed.status == "FAILED"
        assert failed.failure_class == "ValueError"
