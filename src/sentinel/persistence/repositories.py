"""Repository operations kept separate from public contracts."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from sentinel.domain.contracts import DatasetInput
from sentinel.domain.exceptions import DatasetNotFoundError
from sentinel.persistence.models import DatasetRow, DatasetVersionRow


def register_dataset(session: Session, data: DatasetInput) -> DatasetRow:
    """Get or create a logical dataset by its explicit slug."""
    row = session.scalar(select(DatasetRow).where(DatasetRow.slug == data.slug).with_for_update())
    if row is None:
        row = DatasetRow(
            slug=data.slug, display_name=data.display_name, description=data.description
        )
        session.add(row)
        session.flush()
    return row


def get_dataset(session: Session, key: str | UUID) -> DatasetRow:
    """Retrieve a dataset by slug or UUID."""
    statement = (
        select(DatasetRow).where(DatasetRow.slug == key)
        if isinstance(key, str)
        else select(DatasetRow).where(DatasetRow.id == key)
    )
    row = session.scalar(statement)
    if row is None:
        raise DatasetNotFoundError(f"Dataset not found: {key}")
    return row


def get_version(session: Session, dataset_id: UUID, version_number: int) -> DatasetVersionRow:
    """Retrieve a version by its dataset-local number."""
    row = session.scalar(
        select(DatasetVersionRow).where(
            DatasetVersionRow.dataset_id == dataset_id,
            DatasetVersionRow.version_number == version_number,
        )
    )
    if row is None:
        raise DatasetNotFoundError(f"Dataset version not found: {dataset_id}/{version_number}")
    return row


def list_versions(session: Session, dataset_id: UUID) -> list[DatasetVersionRow]:
    """List versions in ascending sequence order."""
    return list(
        session.scalars(
            select(DatasetVersionRow)
            .where(DatasetVersionRow.dataset_id == dataset_id)
            .order_by(DatasetVersionRow.version_number)
        )
    )
