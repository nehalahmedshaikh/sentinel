"""Dataset registration, upload, and version retrieval endpoints."""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from sentinel.api.dependencies import SessionDep, SettingsDep, require_api_key
from sentinel.api.uploads import upload_to_job
from sentinel.domain.contracts import DatasetInput
from sentinel.domain.exceptions import DatasetNotFoundError
from sentinel.ingestion.service import DatasetService
from sentinel.persistence.models import DatasetRow
from sentinel.storage.filesystem import FileStore

router = APIRouter(
    prefix="/api/v1/datasets", tags=["datasets"], dependencies=[Depends(require_api_key)]
)
UploadFileDep = Annotated[UploadFile, File()]
FormString = Annotated[str, Form()]
OptionalFormString = Annotated[str | None, Form()]


@router.post("", status_code=status.HTTP_201_CREATED)
def register_dataset(data: DatasetInput, session: SessionDep) -> object:
    try:
        return DatasetService(session, storage_for_session(session)).register(data)
    except Exception as exc:
        raise HTTPException(409, "Dataset registration failed") from exc


def storage_for_session(session: Session) -> FileStore:
    """Use the configured file store bound by the request session info."""
    return cast(FileStore, session.info["sentinel_storage"])


@router.get("")
def list_datasets(session: SessionDep) -> list[dict[str, object]]:
    return [
        {
            "id": row.id,
            "slug": row.slug,
            "display_name": row.display_name,
            "description": row.description,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
        for row in session.scalars(select(DatasetRow).order_by(DatasetRow.slug))
    ]


@router.get("/{dataset_key}")
def retrieve_dataset(dataset_key: str, session: SessionDep) -> object:
    try:
        service = DatasetService(session, storage_for_session(session))
        return service.retrieve_dataset(dataset_key)
    except (DatasetNotFoundError, ValueError) as exc:
        raise HTTPException(404, "Dataset not found") from exc


@router.get("/{dataset_key}/versions")
def retrieve_versions(dataset_key: str, session: SessionDep) -> object:
    try:
        return DatasetService(session, storage_for_session(session)).retrieve_versions(dataset_key)
    except (DatasetNotFoundError, ValueError) as exc:
        raise HTTPException(404, "Dataset not found") from exc


@router.get("/{dataset_key}/versions/{version_number}")
def retrieve_version(dataset_key: str, version_number: int, session: SessionDep) -> object:
    try:
        return DatasetService(session, storage_for_session(session)).retrieve_version(
            dataset_key, version_number
        )
    except (DatasetNotFoundError, ValueError) as exc:
        raise HTTPException(404, "Dataset version not found") from exc


@router.post("/{dataset_key}/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload_dataset(
    dataset_key: str,
    file: UploadFileDep,
    display_name: FormString,
    session: SessionDep,
    settings: SettingsDep,
    description: OptionalFormString = None,
) -> object:
    try:
        return await upload_to_job(session, settings, dataset_key, display_name, file, description)
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(422, "Unable to queue dataset ingestion") from exc
