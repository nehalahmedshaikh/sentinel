"""Application service coordinating ingestion and retrieval."""

from pathlib import Path
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from sentinel.domain.contracts import (
    ColumnSchema,
    DataProfile,
    DatasetInput,
    DatasetVersionView,
    DatasetView,
    Registration,
    schema_as_dicts,
)
from sentinel.domain.exceptions import PersistenceError, ValidationError
from sentinel.ingestion.readers import detect_format, read_frame
from sentinel.persistence.models import DatasetRow, DatasetVersionRow
from sentinel.persistence.repositories import (
    get_dataset,
    get_version,
    list_versions,
    register_dataset,
)
from sentinel.profiling.profile import build_profile, fingerprint_schema, infer_schema
from sentinel.storage.filesystem import FileStore


class DatasetService:
    """Register datasets, ingest immutable versions, and retrieve results."""

    def __init__(self, session: Session, storage: FileStore) -> None:
        self.session = session
        self.storage = storage

    def register(self, data: DatasetInput) -> DatasetView:
        """Register or retrieve a dataset by explicit slug."""
        try:
            with self.session.begin():
                self.session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:slug))"), {"slug": data.slug}
                )
                row = register_dataset(self.session, data)
                self.session.flush()
                result = _dataset_view(row)
            return result
        except SQLAlchemyError as exc:
            raise PersistenceError(f"Unable to register dataset: {exc}") from exc

    def ingest(self, source_path: Path, dataset: DatasetInput) -> Registration:
        """Preserve source bytes and atomically register an immutable version."""
        source = source_path.expanduser()
        if not source.exists() or not source.is_file():
            raise ValidationError("Source path must refer to an existing regular file")
        if source.stat().st_size == 0:
            raise ValidationError("Source file is empty")
        file_format = detect_format(source)
        staged, content_hash, size = self.storage.stage(source)
        promoted: Path | None = None
        try:
            frame = read_frame(staged, file_format)
            schema = infer_schema(frame)
            profile = build_profile(frame)
            schema_hash = fingerprint_schema(schema)
            with self.session.begin():
                self.session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:slug))"), {"slug": dataset.slug}
                )
                dataset_row = register_dataset(self.session, dataset)
                locked_row = self.session.scalar(
                    select(DatasetRow).where(DatasetRow.id == dataset_row.id).with_for_update()
                )
                if locked_row is None:
                    raise PersistenceError("Dataset disappeared while acquiring its ingestion lock")
                dataset_row = locked_row
                existing = self.session.scalar(
                    select(DatasetVersionRow).where(
                        DatasetVersionRow.dataset_id == dataset_row.id,
                        DatasetVersionRow.content_sha256 == content_hash,
                    )
                )
                if existing is not None:
                    return Registration(
                        dataset=_dataset_view(dataset_row), version=_version_view(existing)
                    )
                next_version = (
                    max((v.version_number for v in dataset_row.versions), default=0)
                ) + 1
                promoted, storage_key = self.storage.promote(
                    staged, dataset_row.id, next_version, source.name
                )
                version_row = DatasetVersionRow(
                    dataset_id=dataset_row.id,
                    version_number=next_version,
                    source_filename=source.name,
                    format=file_format,
                    content_sha256=content_hash,
                    file_size_bytes=size,
                    storage_key=storage_key,
                    schema_data=schema_as_dicts(schema),
                    schema_fingerprint=schema_hash,
                    row_count=frame.height,
                    column_count=frame.width,
                    profile=profile.model_dump(mode="json"),
                )
                self.session.add(version_row)
                self.session.flush()
                result = Registration(
                    dataset=_dataset_view(dataset_row), version=_version_view(version_row)
                )
            return result
        except (SQLAlchemyError, OSError) as exc:
            if promoted is not None:
                self.storage.discard(promoted)
            raise PersistenceError(f"Unable to persist ingested dataset: {exc}") from exc
        except Exception:
            if promoted is not None:
                self.storage.discard(promoted)
            raise
        finally:
            self.storage.discard(staged)

    def retrieve_dataset(self, key: str | UUID) -> DatasetView:
        """Retrieve a dataset by slug or UUID."""
        try:
            return _dataset_view(get_dataset(self.session, key))
        except SQLAlchemyError as exc:
            raise PersistenceError(f"Unable to retrieve dataset: {exc}") from exc

    def retrieve_versions(self, key: str | UUID) -> list[DatasetVersionView]:
        """List versions for a dataset."""
        row = get_dataset(self.session, key)
        return [_version_view(item) for item in list_versions(self.session, row.id)]

    def retrieve_version(self, key: str | UUID, number: int) -> DatasetVersionView:
        """Retrieve one version and its persisted profile."""
        row = get_dataset(self.session, key)
        return _version_view(get_version(self.session, row.id, number))


def _dataset_view(row: DatasetRow) -> DatasetView:
    return DatasetView(
        id=row.id,
        slug=row.slug,
        display_name=row.display_name,
        description=row.description,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _version_view(row: DatasetVersionRow) -> DatasetVersionView:
    return DatasetVersionView(
        id=row.id,
        dataset_id=row.dataset_id,
        version_number=row.version_number,
        source_filename=row.source_filename,
        format=row.format,
        content_sha256=row.content_sha256,
        file_size_bytes=row.file_size_bytes,
        storage_key=row.storage_key,
        schema_=[ColumnSchema.model_validate(item) for item in row.schema_data],
        schema_fingerprint=row.schema_fingerprint,
        row_count=row.row_count,
        column_count=row.column_count,
        profile=DataProfile.model_validate(row.profile),
        ingested_at=row.ingested_at,
    )
