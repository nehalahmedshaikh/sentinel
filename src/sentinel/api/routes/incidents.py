"""Incident retrieval, lifecycle and deterministic report endpoints."""

from collections.abc import Callable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from sentinel.api.dependencies import SessionDep, require_api_key
from sentinel.incidents.contracts import (
    IncidentGenerationInput,
    IncidentSeverity,
    IncidentStatus,
    IncidentView,
    NoteInput,
    ResolveInput,
    SeverityInput,
)
from sentinel.incidents.service import IncidentService
from sentinel.operations.contracts import JobType
from sentinel.operations.service import OperationsService
from sentinel.persistence.models import DatasetRow

router = APIRouter(
    prefix="/api/v1/incidents", tags=["incidents"], dependencies=[Depends(require_api_key)]
)


@router.post("/generate", status_code=status.HTTP_202_ACCEPTED)
def generate_incidents(data: IncidentGenerationInput, session: SessionDep) -> object:
    identity = f"incidents:{data.source_type.value}:{data.assessment_id}:{data.policy_id}"
    return OperationsService(session).enqueue(
        JobType.INCIDENTS,
        {
            "source_type": data.source_type.value,
            "assessment_id": str(data.assessment_id),
            "policy_id": str(data.policy_id),
        },
        idempotency_key=identity,
        policy_id=data.policy_id,
    )


@router.get("")
def list_incidents(
    session: SessionDep,
    dataset_id: UUID | None = None,
    status: IncidentStatus | None = None,
    severity: IncidentSeverity | None = None,
    limit: int = 100,
) -> list[IncidentView]:
    service = IncidentService(session)
    if dataset_id is not None:
        return service.list_incidents(dataset_id, status=status, severity=severity, limit=limit)
    datasets = list(session.scalars(select(DatasetRow).order_by(DatasetRow.slug)))
    result: list[IncidentView] = []
    for dataset in datasets:
        result.extend(
            service.list_incidents(dataset.id, status=status, severity=severity, limit=limit)
        )
    return sorted(result, key=lambda item: item.last_observed_at, reverse=True)[:limit]


@router.get("/{incident_id}")
def retrieve_incident(incident_id: UUID, session: SessionDep) -> object:
    try:
        return IncidentService(session).retrieve(incident_id)
    except ValueError as exc:
        raise HTTPException(404, "Incident not found") from exc


@router.get("/{incident_id}/report")
def incident_report(incident_id: UUID, session: SessionDep) -> object:
    try:
        return IncidentService(session).report(incident_id)
    except ValueError as exc:
        raise HTTPException(404, "Incident not found") from exc


@router.post("/{incident_id}/acknowledge")
def acknowledge(incident_id: UUID, session: SessionDep) -> object:
    return _operation(lambda: IncidentService(session).acknowledge(incident_id))


@router.post("/{incident_id}/investigate")
def start_investigation(incident_id: UUID, session: SessionDep) -> object:
    return _operation(lambda: IncidentService(session).start_investigation(incident_id))


@router.post("/{incident_id}/notes")
def add_note(incident_id: UUID, data: NoteInput, session: SessionDep) -> object:
    return _operation(lambda: IncidentService(session).add_note(incident_id, data))


@router.post("/{incident_id}/resolve")
def resolve(incident_id: UUID, data: ResolveInput, session: SessionDep) -> object:
    return _operation(lambda: IncidentService(session).resolve(incident_id, data))


@router.post("/{incident_id}/reopen")
def reopen(incident_id: UUID, reason: str, session: SessionDep) -> object:
    return _operation(lambda: IncidentService(session).reopen(incident_id, reason))


@router.put("/{incident_id}/severity")
def set_severity(incident_id: UUID, data: SeverityInput, session: SessionDep) -> object:
    return _operation(lambda: IncidentService(session).set_severity(incident_id, data))


def _operation(call: Callable[[], object]) -> object:
    try:
        return call()
    except ValueError as exc:
        message = str(exc)
        code = 404 if "not found" in message.lower() else 409
        raise HTTPException(code, message if code == 409 else "Incident not found") from exc
