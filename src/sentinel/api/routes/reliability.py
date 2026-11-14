"""Reliability policy and asynchronous assessment endpoints."""

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from sentinel.api.dependencies import SessionDep, require_api_key, storage_for_session
from sentinel.operations.contracts import JobType
from sentinel.operations.service import OperationsService
from sentinel.reliability.contracts import DetectionPolicyInput, ReliabilityAssessmentInput
from sentinel.reliability.service import ReliabilityService

router = APIRouter(
    prefix="/api/v1/reliability", tags=["reliability"], dependencies=[Depends(require_api_key)]
)


@router.get("/datasets/{dataset_key}/policies")
def list_policies(dataset_key: str, session: SessionDep) -> object:
    return ReliabilityService(session, storage_for_session(session)).get_policies(dataset_key)


@router.post("/datasets/{dataset_key}/policies", status_code=status.HTTP_201_CREATED)
def create_policy(dataset_key: str, data: DetectionPolicyInput, session: SessionDep) -> object:
    return ReliabilityService(session, storage_for_session(session)).create_policy(
        dataset_key, data
    )


@router.put("/policies/{policy_id}")
def update_policy(policy_id: UUID, data: DetectionPolicyInput, session: SessionDep) -> object:
    return ReliabilityService(session, storage_for_session(session)).update_policy(policy_id, data)


@router.post("/policies/{policy_id}/enabled")
def set_policy_enabled(policy_id: UUID, enabled: bool, session: SessionDep) -> object:
    return ReliabilityService(session, storage_for_session(session)).set_policy_enabled(
        policy_id, enabled
    )


@router.post("/assessments", status_code=status.HTTP_202_ACCEPTED)
def submit_assessment(
    data: ReliabilityAssessmentInput,
    session: SessionDep,
    incident_policy_id: UUID | None = None,
) -> object:
    return OperationsService(session).enqueue(
        JobType.RELIABILITY,
        {
            "request": data.model_dump(mode="json"),
            "incident_policy_id": str(incident_policy_id) if incident_policy_id else None,
        },
        idempotency_key=f"reliability:{data.dataset_version_id}:{data.policy_id}:{uuid4()}",
        dataset_version_id=data.dataset_version_id,
        policy_id=data.policy_id,
    )


@router.get("/datasets/{dataset_key}/assessments")
def assessment_history(dataset_key: str, session: SessionDep) -> object:
    return ReliabilityService(session, storage_for_session(session)).assessment_history(dataset_key)


@router.get("/assessments/{assessment_id}")
def retrieve_assessment(assessment_id: UUID, session: SessionDep) -> object:
    try:
        return ReliabilityService(session, storage_for_session(session)).retrieve_assessment(
            assessment_id
        )
    except ValueError as exc:
        raise HTTPException(404, "Reliability assessment not found") from exc
