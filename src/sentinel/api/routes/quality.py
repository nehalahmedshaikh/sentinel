"""Quality rule and asynchronous assessment endpoints."""

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from sentinel.api.dependencies import SessionDep, require_api_key, storage_for_session
from sentinel.operations.contracts import JobType
from sentinel.operations.service import OperationsService
from sentinel.quality.contracts import QualityRuleInput
from sentinel.quality.service import QualityService

router = APIRouter(
    prefix="/api/v1/quality", tags=["quality"], dependencies=[Depends(require_api_key)]
)


@router.get("/datasets/{dataset_key}/rules")
def list_rules(dataset_key: str, session: SessionDep) -> object:
    return QualityService(session, storage_for_session(session)).get_rules(dataset_key)


@router.post("/datasets/{dataset_key}/rules", status_code=status.HTTP_201_CREATED)
def create_rule(dataset_key: str, data: QualityRuleInput, session: SessionDep) -> object:
    try:
        return QualityService(session, storage_for_session(session)).create_rule(dataset_key, data)
    except Exception as exc:
        raise HTTPException(409, "Unable to create quality rule") from exc


@router.put("/rules/{rule_id}")
def update_rule(rule_id: UUID, data: QualityRuleInput, session: SessionDep) -> object:
    try:
        return QualityService(session, storage_for_session(session)).update_rule(rule_id, data)
    except ValueError as exc:
        raise HTTPException(404, "Quality rule not found") from exc


@router.post("/rules/{rule_id}/enabled")
def set_rule_enabled(rule_id: UUID, enabled: bool, session: SessionDep) -> object:
    try:
        return QualityService(session, storage_for_session(session)).set_rule_enabled(
            rule_id, enabled
        )
    except ValueError as exc:
        raise HTTPException(404, "Quality rule not found") from exc


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: UUID, session: SessionDep) -> None:
    try:
        QualityService(session, storage_for_session(session)).delete_rule(rule_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/versions/{version_id}/assessments", status_code=status.HTTP_202_ACCEPTED)
def submit_assessment(
    version_id: UUID,
    session: SessionDep,
    incident_policy_id: UUID | None = None,
) -> object:
    return OperationsService(session).enqueue(
        JobType.QUALITY,
        {
            "version_id": str(version_id),
            "incident_policy_id": str(incident_policy_id) if incident_policy_id else None,
        },
        idempotency_key=f"quality:{version_id}:{uuid4()}",
        dataset_version_id=version_id,
    )


@router.get("/datasets/{dataset_key}/assessments")
def assessment_history(dataset_key: str, session: SessionDep) -> object:
    return QualityService(session, storage_for_session(session)).assessment_history(dataset_key)


@router.get("/assessments/{assessment_id}")
def retrieve_assessment(assessment_id: UUID, session: SessionDep) -> object:
    try:
        return QualityService(session, storage_for_session(session)).retrieve_assessment(
            assessment_id
        )
    except ValueError as exc:
        raise HTTPException(404, "Quality assessment not found") from exc
