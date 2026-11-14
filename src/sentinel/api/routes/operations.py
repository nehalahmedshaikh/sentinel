"""Persistent job, monitoring schedule and AI request endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from sentinel.ai.contracts import InvestigationRequest
from sentinel.ai.service import queue_investigation, retrieve_investigation
from sentinel.api.dependencies import SessionDep, require_api_key
from sentinel.operations.contracts import JobStatus, JobType, ScheduleInput
from sentinel.operations.service import OperationsService

router = APIRouter(prefix="/api/v1", tags=["operations"], dependencies=[Depends(require_api_key)])


@router.get("/jobs")
def list_jobs(
    session: SessionDep,
    status: JobStatus | None = None,
    job_type: JobType | None = None,
    dataset_id: UUID | None = None,
    offset: int = 0,
    limit: int = 50,
) -> object:
    try:
        return OperationsService(session).list_jobs(
            status=status, job_type=job_type, dataset_id=dataset_id, offset=offset, limit=limit
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/jobs/{job_id}")
def get_job(job_id: UUID, session: SessionDep) -> object:
    try:
        return OperationsService(session).get_job(job_id)
    except ValueError as exc:
        raise HTTPException(404, "Job not found") from exc


@router.post("/schedules", status_code=status.HTTP_201_CREATED)
def create_schedule(data: ScheduleInput, session: SessionDep) -> object:
    try:
        return OperationsService(session).create_schedule(data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/schedules")
def list_schedules(session: SessionDep, dataset_id: UUID | None = None) -> object:
    return OperationsService(session).list_schedules(dataset_id)


@router.put("/schedules/{schedule_id}")
def update_schedule(schedule_id: UUID, data: ScheduleInput, session: SessionDep) -> object:
    try:
        return OperationsService(session).update_schedule(schedule_id, data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/schedules/{schedule_id}/pause")
def pause_schedule(schedule_id: UUID, session: SessionDep) -> object:
    try:
        return OperationsService(session).set_schedule_enabled(schedule_id, False)
    except ValueError as exc:
        raise HTTPException(404, "Schedule not found") from exc


@router.post("/schedules/{schedule_id}/resume")
def resume_schedule(schedule_id: UUID, session: SessionDep) -> object:
    try:
        return OperationsService(session).set_schedule_enabled(schedule_id, True)
    except ValueError as exc:
        raise HTTPException(404, "Schedule not found") from exc


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(schedule_id: UUID, session: SessionDep) -> None:
    try:
        OperationsService(session).delete_schedule(schedule_id)
    except ValueError as exc:
        raise HTTPException(404, "Schedule not found") from exc


@router.get("/schedules/{schedule_id}/executions")
def schedule_history(schedule_id: UUID, session: SessionDep) -> object:
    return OperationsService(session).schedule_history(schedule_id)


@router.post("/incidents/{incident_id}/ai-investigations", status_code=status.HTTP_202_ACCEPTED)
def request_ai_investigation(
    incident_id: UUID,
    data: InvestigationRequest,
    session: SessionDep,
) -> object:
    try:
        return queue_investigation(session, incident_id, data)
    except ValueError as exc:
        code = 503 if "disabled" in str(exc).lower() else 404
        raise HTTPException(
            code, "AI is disabled" if code == 503 else "Incident not found"
        ) from exc


@router.get("/ai-investigations/{investigation_id}")
def get_ai_investigation(investigation_id: UUID, session: SessionDep) -> object:
    try:
        return retrieve_investigation(session, investigation_id)
    except ValueError as exc:
        raise HTTPException(404, "AI investigation not found") from exc
