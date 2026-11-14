"""Focused Phase 5 API, provider, and configuration coverage."""

import asyncio
import os
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient
from pydantic import ValidationError

from sentinel.ai.contracts import AIInvestigationResponse, ChatRequest
from sentinel.ai.provider import OpenAICompatibleProvider, ProviderError
from sentinel.api.app import create_app
from sentinel.api.uploads import upload_to_job
from sentinel.operations.contracts import JobStatus, transition_job
from sentinel.settings import Settings


def _settings(tmp_path: Path, **changes: object) -> Settings:
    values: dict[str, object] = {
        "database_url": os.getenv(
            "SENTINEL_TEST_DATABASE_URL", "postgresql+psycopg://localhost/sentinel_test"
        ),
        "data_dir": str(tmp_path),
        "api_key": "test-api-key-that-is-long-enough",
    }
    values.update(changes)
    return Settings.model_validate(values)


def test_job_state_machine_rejects_terminal_transition() -> None:
    assert transition_job(JobStatus.QUEUED, JobStatus.RUNNING) == JobStatus.RUNNING
    with pytest.raises(ValueError, match="Invalid job transition"):
        transition_job(JobStatus.SUCCEEDED, JobStatus.RUNNING)


def test_settings_validate_ai_configuration_and_hide_secrets() -> None:
    with pytest.raises(ValidationError, match="AI requires"):
        Settings(
            database_url="postgresql+psycopg://localhost/sentinel_test",
            data_dir="/tmp/sentinel",
            api_key="test-api-key-that-is-long-enough",
            ai_enabled=True,
        )
    settings = Settings(
        database_url="postgresql+psycopg://localhost/sentinel_test",
        data_dir="/tmp/sentinel",
        api_key="test-api-key-that-is-long-enough",
        ai_api_key="private-provider-key",
    )
    assert "private-provider-key" not in repr(settings)


def test_provider_uses_compatible_endpoint_and_validates_response() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://model.local/v1/chat/completions"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "{}"}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(respond))
    provider = OpenAICompatibleProvider("http://model.local/v1", None, client=client)
    response = provider.complete(ChatRequest(model="local", messages=[]))
    assert response.choices[0].message.content == "{}"


def test_provider_wraps_transport_failures() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(503, request=request))
    )
    provider = OpenAICompatibleProvider("http://model.local", None, client=client)
    with pytest.raises(ProviderError, match="HTTPStatusError"):
        provider.complete(ChatRequest(model="local", messages=[]))


def test_provider_wraps_timeout() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("inference timed out", request=request)

    client = httpx.Client(transport=httpx.MockTransport(timeout))
    provider = OpenAICompatibleProvider("http://model.local", None, client=client)
    with pytest.raises(ProviderError, match="ReadTimeout"):
        provider.complete(ChatRequest(model="local", messages=[]))


def test_provider_rejects_oversized_response() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 1_000_001))
    )
    provider = OpenAICompatibleProvider("http://model.local", None, client=client)
    with pytest.raises(ProviderError, match="ResponseTooLarge"):
        provider.complete(ChatRequest(model="local", messages=[]))


def test_ai_claim_requires_evidence_reference() -> None:
    with pytest.raises(ValidationError):
        AIInvestigationResponse.model_validate(
            {
                "executive_summary": "Observed a failure.",
                "observations": [{"statement": "The check failed", "evidence_ids": []}],
                "hypotheses": [],
                "uncertainty": [],
                "next_steps": [],
            }
        )


def test_health_readiness_metrics_and_api_key_authentication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import redis

    monkeypatch.setattr(redis.Redis, "ping", lambda self: True)
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/ready").status_code == 200
        metrics = client.get("/metrics")
        assert metrics.status_code == 200
        assert "sentinel_jobs" in metrics.text
        assert client.get("/api/v1/datasets").status_code == 401
        assert (
            client.get(
                "/api/v1/datasets",
                headers={"Authorization": "Bearer test-api-key-that-is-long-enough"},
            ).status_code
            == 200
        )


def test_upload_rejects_unsupported_extension_before_staging(tmp_path: Path) -> None:
    upload = UploadFile(file=BytesIO(b"payload"), filename="data.exe")
    with pytest.raises(Exception) as exc:
        asyncio.run(
            upload_to_job(
                None,  # type: ignore[arg-type]
                _settings(tmp_path),
                "dataset",
                "Dataset",
                upload,
            )
        )
    assert getattr(exc.value, "status_code", None) == 415
    assert list(tmp_path.iterdir()) == []
