"""Shared safe upload staging for API and dashboard forms."""

from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from sentinel.domain.contracts import DatasetInput
from sentinel.domain.exceptions import ValidationError
from sentinel.ingestion.readers import detect_format
from sentinel.operations.contracts import JobType
from sentinel.operations.service import OperationsService
from sentinel.settings import Settings


async def upload_to_job(
    session: Session,
    settings: Settings,
    slug: str,
    display_name: str,
    file: UploadFile,
    description: str | None = None,
) -> object:
    filename = Path((file.filename or "upload").replace("\\", "/")).name
    extension = Path(filename).suffix.lower().lstrip(".")
    if extension not in settings.allowed_file_types:
        raise HTTPException(415, "Unsupported upload extension")
    incoming = Path(settings.data_dir) / "incoming" / uuid4().hex
    incoming.mkdir(parents=True, exist_ok=False)
    target = incoming / filename
    size = 0
    try:
        with target.open("xb") as destination:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.upload_max_bytes:
                    raise HTTPException(413, "Upload exceeds configured size limit")
                destination.write(chunk)
        try:
            detect_format(target)
        except ValidationError as exc:
            raise HTTPException(415, "File content does not match a supported format") from exc
        dataset = DatasetInput(slug=slug, display_name=display_name, description=description)
        return OperationsService(session).enqueue(
            JobType.INGEST,
            {"source_path": str(target), "dataset": dataset.model_dump(mode="json")},
            idempotency_key=f"upload:{uuid4()}",
        )
    except HTTPException:
        target.unlink(missing_ok=True)
        incoming.rmdir()
        raise
    except (OSError, ValueError) as exc:
        target.unlink(missing_ok=True)
        incoming.rmdir()
        raise HTTPException(422, "Unable to queue dataset ingestion") from exc
    finally:
        await file.close()
