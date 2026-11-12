"""PostgreSQL integration coverage; uses only sentinel_test."""

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from sentinel.domain.contracts import DatasetInput
from sentinel.ingestion.service import DatasetService
from sentinel.persistence.database import create_database_engine, session_factory
from sentinel.storage.filesystem import FileStore


@pytest.fixture
def service(tmp_path: Path) -> Iterator[DatasetService]:
    url = os.getenv("SENTINEL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SENTINEL_TEST_DATABASE_URL is not configured")
    engine = create_database_engine(testing=True)
    with engine.connect() as connection:
        database_name = connection.scalar(text("SELECT current_database()"))
    if database_name != "sentinel_test":
        engine.dispose()
        pytest.fail("Refusing integration test setup: test database must be named sentinel_test")
    yield DatasetService(session_factory(engine)(), FileStore(tmp_path / "managed"))
    engine.dispose()


def test_registration_versioning_duplicate_and_retrieval(
    service: DatasetService, tmp_path: Path
) -> None:
    slug = f"integration-{uuid4().hex}"
    data = DatasetInput(slug=slug, display_name="Integration")
    source = tmp_path / "source.csv"
    source.write_text("amount,label\n2,a\n4,b\n", encoding="utf-8")
    first = service.ingest(source, data)
    same = service.ingest(source, data)
    assert first.version.version_number == same.version.version_number == 1
    source.write_text("amount,label\n8,c\n", encoding="utf-8")
    second = service.ingest(source, data)
    assert second.version.version_number == 2
    snapshot = service.storage.absolute(first.version.storage_key)
    assert snapshot.read_text(encoding="utf-8") == "amount,label\n2,a\n4,b\n"
    assert len(service.retrieve_versions(slug)) == 2
    assert service.retrieve_version(slug, 1).profile.columns[0].mean == 3
