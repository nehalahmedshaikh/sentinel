"""Jinja2 dashboard routes sharing the same services and job queue."""

from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import TypeAdapter
from sqlalchemy import func, select

from sentinel.ai.contracts import InvestigationRequest
from sentinel.ai.service import queue_investigation, retrieve_investigation
from sentinel.api.dependencies import SessionDep
from sentinel.api.uploads import upload_to_job
from sentinel.incidents.contracts import (
    IncidentGenerationInput,
    IncidentPolicyInput,
    IncidentSeverity,
    IncidentStatus,
    NoteInput,
    ResolveInput,
    SeverityInput,
    SourceType,
)
from sentinel.incidents.models import IncidentRow
from sentinel.incidents.service import IncidentService
from sentinel.operations.contracts import ScheduleInput
from sentinel.operations.models import JobRow
from sentinel.operations.service import OperationsService
from sentinel.persistence.models import DatasetRow, DatasetVersionRow
from sentinel.quality.contracts import QualityRuleInput, RuleParameters, RuleType, Severity
from sentinel.quality.models import QualityAssessmentRow
from sentinel.quality.service import QualityService
from sentinel.reliability.models import ReliabilityAssessmentRow
from sentinel.reliability.service import ReliabilityService

_TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=_TEMPLATE_DIR)
router = APIRouter(tags=["dashboard"])
FormText = Annotated[str, Form()]
OptionalFormText = Annotated[str | None, Form()]
FormUUID = Annotated[UUID, Form()]
OptionalFormUUID = Annotated[UUID | None, Form()]
FormInteger = Annotated[int, Form()]
FormBool = Annotated[bool, Form()]
FormRule = Annotated[RuleType, Form()]
FormSourceType = Annotated[SourceType, Form()]
FormUpload = Annotated[UploadFile, File()]


@router.get("/", response_class=HTMLResponse)
def overview(request: Request, session: SessionDep) -> HTMLResponse:
    open_incidents = list(
        session.scalars(
            select(IncidentRow).where(IncidentRow.status != IncidentStatus.RESOLVED.value)
        )
    )
    jobs = list(
        session.scalars(
            select(JobRow)
            .where(JobRow.status.in_(["RUNNING", "FAILED"]))
            .order_by(JobRow.created_at.desc())
            .limit(20)
        )
    )
    return templates.TemplateResponse(
        request=request,
        name="overview.html",
        context={
            "datasets": session.scalar(select(func.count()).select_from(DatasetRow)) or 0,
            "incidents": open_incidents,
            "jobs": jobs,
            "schedules": OperationsService(session).list_schedules(),
            "quality_total": session.scalar(select(func.count()).select_from(QualityAssessmentRow))
            or 0,
            "reliability_total": session.scalar(
                select(func.count()).select_from(ReliabilityAssessmentRow)
            )
            or 0,
        },
    )


@router.get("/dashboard/datasets", response_class=HTMLResponse)
def datasets_page(request: Request, session: SessionDep) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="datasets.html",
        context={"datasets": list(session.scalars(select(DatasetRow).order_by(DatasetRow.slug)))},
    )


@router.get("/dashboard/datasets/{dataset_key}", response_class=HTMLResponse)
def dataset_detail(dataset_key: str, request: Request, session: SessionDep) -> HTMLResponse:
    from sentinel.ingestion.service import DatasetService

    service = DatasetService(session, request.app.state.storage)
    try:
        dataset = service.retrieve_dataset(dataset_key)
        versions = service.retrieve_versions(dataset_key)
        session.rollback()
        quality_service = QualityService(session, request.app.state.storage)
        rules = quality_service.get_rules(dataset_key)
        quality_history = quality_service.assessment_history(dataset_key)
        reliability_service = ReliabilityService(session, request.app.state.storage)
        policies = reliability_service.get_policies(dataset_key)
        reliability_history = reliability_service.assessment_history(dataset_key)
        incident_policies = IncidentService(session).get_policies(dataset_key)
    except ValueError as exc:
        raise HTTPException(404, "Dataset not found") from exc
    return templates.TemplateResponse(
        request=request,
        name="dataset_detail.html",
        context={
            "dataset": dataset,
            "versions": versions,
            "rules": rules,
            "policies": policies,
            "quality_history": quality_history,
            "reliability_history": reliability_history,
            "incident_policies": incident_policies,
        },
    )


@router.post("/dashboard/upload", response_class=HTMLResponse)
async def dashboard_upload(
    request: Request,
    slug: FormText,
    display_name: FormText,
    file: FormUpload,
    session: SessionDep,
) -> RedirectResponse:
    # This route is intentionally thin: use the same persisted upload job workflow as the API.
    await upload_to_job(session, request.app.state.settings, slug, display_name, file, None)
    return RedirectResponse(url=f"/dashboard/datasets/{slug}", status_code=303)


@router.post("/dashboard/datasets/{dataset_key}/quality-rules")
def dashboard_quality_rule(
    request: Request,
    dataset_key: str,
    name: FormText,
    rule_type: FormRule,
    parameters_json: FormText,
    session: SessionDep,
) -> RedirectResponse:
    import json

    try:
        params = TypeAdapter(RuleParameters).validate_python(json.loads(parameters_json))
        QualityService(session, request.app.state.storage).create_rule(
            dataset_key,
            QualityRuleInput(name=name, rule_type=rule_type, parameters=params),
        )
    except Exception as exc:
        raise HTTPException(422, "Invalid quality rule") from exc
    return RedirectResponse(url=f"/dashboard/datasets/{dataset_key}", status_code=303)


@router.post("/dashboard/quality-rules/{rule_id}")
def dashboard_update_quality_rule(
    request: Request,
    rule_id: UUID,
    name: FormText,
    rule_type: FormRule,
    parameters_json: FormText,
    severity: FormText,
    enabled: FormBool,
    session: SessionDep,
) -> RedirectResponse:
    import json

    try:
        params = TypeAdapter(RuleParameters).validate_python(json.loads(parameters_json))
        rule = QualityRuleInput(
            name=name,
            rule_type=rule_type,
            parameters=params,
            severity=Severity(severity),
            enabled=enabled,
        )
        QualityService(session, request.app.state.storage).update_rule(rule_id, rule)
    except Exception as exc:
        raise HTTPException(422, "Invalid quality rule update") from exc
    return RedirectResponse(request.headers.get("referer", "/dashboard/datasets"), status_code=303)


@router.post("/dashboard/quality-rules/{rule_id}/enabled")
def dashboard_set_quality_rule_enabled(
    request: Request, rule_id: UUID, enabled: FormBool, session: SessionDep
) -> RedirectResponse:
    try:
        QualityService(session, request.app.state.storage).set_rule_enabled(rule_id, enabled)
    except ValueError as exc:
        raise HTTPException(404, "Quality rule not found") from exc
    return RedirectResponse("/dashboard/datasets", status_code=303)


@router.post("/dashboard/datasets/{dataset_key}/reliability-policies")
def dashboard_reliability_policy(
    request: Request,
    dataset_key: str,
    name: FormText,
    config_json: FormText,
    session: SessionDep,
) -> RedirectResponse:
    import json

    from sentinel.reliability.contracts import DetectionPolicyInput

    try:
        policy = DetectionPolicyInput.model_validate(
            {"name": name, "config": json.loads(config_json)}
        )
        ReliabilityService(session, request.app.state.storage).create_policy(dataset_key, policy)
    except Exception as exc:
        raise HTTPException(422, "Invalid reliability policy") from exc
    return RedirectResponse(url=f"/dashboard/datasets/{dataset_key}", status_code=303)


@router.post("/dashboard/reliability-policies/{policy_id}")
def dashboard_update_reliability_policy(
    request: Request,
    policy_id: UUID,
    name: FormText,
    config_json: FormText,
    session: SessionDep,
) -> RedirectResponse:
    import json

    from sentinel.reliability.contracts import DetectionPolicyInput

    try:
        policy = DetectionPolicyInput.model_validate(
            {"name": name, "config": json.loads(config_json)}
        )
        ReliabilityService(session, request.app.state.storage).update_policy(policy_id, policy)
    except Exception as exc:
        raise HTTPException(422, "Invalid reliability policy update") from exc
    return RedirectResponse(request.headers.get("referer", "/dashboard/datasets"), status_code=303)


@router.post("/dashboard/reliability-policies/{policy_id}/enabled")
def dashboard_set_reliability_policy_enabled(
    request: Request, policy_id: UUID, enabled: FormBool, session: SessionDep
) -> RedirectResponse:
    try:
        ReliabilityService(session, request.app.state.storage).set_policy_enabled(
            policy_id, enabled
        )
    except ValueError as exc:
        raise HTTPException(404, "Reliability policy not found") from exc
    return RedirectResponse(request.headers.get("referer", "/dashboard/datasets"), status_code=303)


@router.post("/dashboard/datasets/{dataset_key}/incident-policies")
def dashboard_create_incident_policy(
    dataset_key: str,
    name: FormText,
    session: SessionDep,
    config_json: OptionalFormText = None,
) -> RedirectResponse:
    import json

    try:
        data = IncidentPolicyInput(
            name=name,
            config=(json.loads(config_json) if config_json and config_json.strip() else {}),
        )
        IncidentService(session).create_policy(dataset_key, data)
    except Exception as exc:
        raise HTTPException(422, "Invalid incident policy") from exc
    return RedirectResponse(f"/dashboard/datasets/{dataset_key}", status_code=303)


@router.post("/dashboard/incident-policies/{policy_id}")
def dashboard_update_incident_policy(
    request: Request,
    policy_id: UUID,
    name: FormText,
    config_json: FormText,
    enabled: FormBool,
    session: SessionDep,
) -> RedirectResponse:
    import json

    try:
        data = IncidentPolicyInput(name=name, config=json.loads(config_json), enabled=enabled)
        IncidentService(session).update_policy(policy_id, data)
    except ValueError as exc:
        raise HTTPException(422, "Invalid incident policy") from exc
    return RedirectResponse(request.headers.get("referer", "/dashboard/incidents"), status_code=303)


@router.post("/dashboard/incident-policies/{policy_id}/enabled")
def dashboard_set_incident_policy_enabled(
    request: Request, policy_id: UUID, enabled: FormBool, session: SessionDep
) -> RedirectResponse:
    try:
        IncidentService(session).set_policy_enabled(policy_id, enabled)
    except ValueError as exc:
        raise HTTPException(404, "Incident policy not found") from exc
    return RedirectResponse(request.headers.get("referer", "/dashboard/incidents"), status_code=303)


@router.post("/dashboard/datasets/{dataset_key}/incidents/generate")
def dashboard_generate_incidents(
    dataset_key: str,
    source_type: FormSourceType,
    assessment_id: FormUUID,
    policy_id: FormUUID,
    session: SessionDep,
) -> RedirectResponse:
    from sentinel.incidents.models import IncidentPolicyRow
    from sentinel.operations.contracts import JobType
    from sentinel.quality.models import QualityAssessmentRow
    from sentinel.reliability.models import ReliabilityAssessmentRow

    dataset = session.scalar(select(DatasetRow).where(DatasetRow.slug == dataset_key))
    policy = session.get(IncidentPolicyRow, policy_id)
    assessment_model = (
        QualityAssessmentRow if source_type == SourceType.QUALITY else ReliabilityAssessmentRow
    )
    assessment = session.get(assessment_model, assessment_id)
    assessed_dataset_id = None
    if isinstance(assessment, QualityAssessmentRow):
        version = session.get(DatasetVersionRow, assessment.dataset_version_id)
        assessed_dataset_id = version.dataset_id if version is not None else None
    elif isinstance(assessment, ReliabilityAssessmentRow):
        assessed_dataset_id = assessment.dataset_id
    if (
        dataset is None
        or policy is None
        or assessment is None
        or not policy.enabled
        or policy.dataset_id != dataset.id
        or assessed_dataset_id != dataset.id
    ):
        session.rollback()
        raise HTTPException(422, "Select an assessment and enabled policy for this dataset")
    dataset_uuid = dataset.id
    request_data = IncidentGenerationInput(
        source_type=source_type, assessment_id=assessment_id, policy_id=policy_id
    )
    session.rollback()
    OperationsService(session).enqueue(
        JobType.INCIDENTS,
        {
            "source_type": request_data.source_type.value,
            "assessment_id": str(request_data.assessment_id),
            "policy_id": str(request_data.policy_id),
        },
        idempotency_key=(
            f"incidents:{request_data.source_type.value}:"
            f"{request_data.assessment_id}:{request_data.policy_id}"
        ),
        dataset_id=dataset_uuid,
        policy_id=policy_id,
    )
    return RedirectResponse(f"/dashboard/datasets/{dataset_key}", status_code=303)


@router.post("/dashboard/datasets/{dataset_key}/assessments")
def dashboard_assessment(
    dataset_key: str,
    kind: FormText,
    version_id: FormUUID,
    session: SessionDep,
    policy_id: OptionalFormUUID = None,
) -> RedirectResponse:
    from sentinel.operations.contracts import JobType

    if kind == "quality":
        OperationsService(session).enqueue(
            JobType.QUALITY,
            {"version_id": str(version_id), "incident_policy_id": None},
            idempotency_key=f"dashboard-quality:{version_id}:{uuid4()}",
            dataset_version_id=version_id,
        )
    elif kind == "reliability" and policy_id is not None:
        from sentinel.reliability.contracts import (
            BaselineStrategy,
            ReliabilityAssessmentInput,
        )

        assessment = ReliabilityAssessmentInput(
            dataset_version_id=version_id,
            policy_id=policy_id,
            baseline_strategy=BaselineStrategy.PREVIOUS,
        )
        OperationsService(session).enqueue(
            JobType.RELIABILITY,
            {"request": assessment.model_dump(mode="json"), "incident_policy_id": None},
            idempotency_key=f"dashboard-reliability:{version_id}:{policy_id}:{uuid4()}",
            dataset_version_id=version_id,
            policy_id=policy_id,
        )
    else:
        raise HTTPException(422, "Select a valid assessment type and policy")
    return RedirectResponse(url=f"/dashboard/datasets/{dataset_key}", status_code=303)


@router.get("/dashboard/quality/assessments/{assessment_id}", response_class=HTMLResponse)
def quality_assessment_detail(
    assessment_id: UUID, request: Request, session: SessionDep
) -> HTMLResponse:
    try:
        result = QualityService(session, request.app.state.storage).retrieve_assessment(
            assessment_id
        )
    except ValueError as exc:
        raise HTTPException(404, "Quality assessment not found") from exc
    return templates.TemplateResponse(
        request=request,
        name="assessment_detail.html",
        context={"kind": "Quality", "result": result},
    )


@router.get("/dashboard/reliability/assessments/{assessment_id}", response_class=HTMLResponse)
def reliability_assessment_detail(
    assessment_id: UUID, request: Request, session: SessionDep
) -> HTMLResponse:
    try:
        result = ReliabilityService(session, request.app.state.storage).retrieve_assessment(
            assessment_id
        )
    except ValueError as exc:
        raise HTTPException(404, "Reliability assessment not found") from exc
    return templates.TemplateResponse(
        request=request,
        name="assessment_detail.html",
        context={"kind": "Reliability", "result": result},
    )


@router.get("/dashboard/incidents", response_class=HTMLResponse)
def incidents_page(
    request: Request,
    session: SessionDep,
    status: str | None = None,
    severity: str | None = None,
    dataset_id: UUID | None = None,
) -> HTMLResponse:
    query = select(IncidentRow).order_by(IncidentRow.last_observed_at.desc()).limit(100)
    if status:
        query = query.where(IncidentRow.status == status)
    if severity:
        query = query.where(IncidentRow.severity == severity)
    if dataset_id:
        query = query.where(IncidentRow.dataset_id == dataset_id)
    incidents = list(session.scalars(query))
    datasets = {row.id: row.slug for row in session.scalars(select(DatasetRow))}
    return templates.TemplateResponse(
        request=request,
        name="incidents.html",
        context={"incidents": incidents, "datasets": datasets},
    )


@router.get("/dashboard/incidents/{incident_id}", response_class=HTMLResponse)
def incident_detail(incident_id: UUID, request: Request, session: SessionDep) -> HTMLResponse:
    try:
        detail = IncidentService(session).retrieve(incident_id)
        report = IncidentService(session).report(incident_id)
    except ValueError as exc:
        raise HTTPException(404, "Incident not found") from exc
    investigation_id = request.query_params.get("ai")
    ai_result = None
    if investigation_id:
        try:
            ai_result = retrieve_investigation(session, UUID(investigation_id))
            if ai_result.incident_id != incident_id:
                ai_result = None
        except ValueError:
            ai_result = None
    return templates.TemplateResponse(
        request=request,
        name="incident_detail.html",
        context={"detail": detail, "report": report, "ai_result": ai_result},
    )


@router.get(
    "/dashboard/fragments/ai-investigations/{investigation_id}", response_class=HTMLResponse
)
def ai_result_fragment(
    investigation_id: UUID, request: Request, session: SessionDep
) -> HTMLResponse:
    try:
        result = retrieve_investigation(session, investigation_id)
    except ValueError as exc:
        raise HTTPException(404, "AI investigation not found") from exc
    return templates.TemplateResponse(
        request=request, name="ai_result.html", context={"result": result}
    )


@router.post("/dashboard/incidents/{incident_id}/lifecycle")
def lifecycle_action(
    request: Request,
    incident_id: UUID,
    action: FormText,
    session: SessionDep,
    reason: str = Form(default="Operator action"),
) -> RedirectResponse:
    service = IncidentService(session)
    try:
        if action == "acknowledge":
            service.acknowledge(incident_id)
        elif action == "investigate":
            service.start_investigation(incident_id)
        elif action == "note":
            service.add_note(incident_id, NoteInput(note=reason))
        elif action == "resolve":
            service.resolve(incident_id, ResolveInput(summary=reason, reason=reason))
        elif action == "reopen":
            service.reopen(incident_id, reason)
        else:
            raise ValueError("Unknown incident operation")
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse(url=f"/dashboard/incidents/{incident_id}", status_code=303)


@router.post("/dashboard/incidents/{incident_id}/severity")
def severity_action(
    incident_id: UUID,
    severity: FormText,
    reason: FormText,
    session: SessionDep,
) -> RedirectResponse:
    try:
        IncidentService(session).set_severity(
            incident_id, SeverityInput(severity=IncidentSeverity(severity), reason=reason)
        )
    except ValueError as exc:
        raise HTTPException(409, "Unable to change incident severity") from exc
    return RedirectResponse(url=f"/dashboard/incidents/{incident_id}", status_code=303)


@router.get("/dashboard/jobs", response_class=HTMLResponse)
def jobs_page(request: Request, session: SessionDep) -> HTMLResponse:
    jobs = OperationsService(session).list_jobs(limit=100)
    return templates.TemplateResponse(
        request=request, name="jobs.html", context={"jobs": jobs.items}
    )


@router.get("/dashboard/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(job_id: UUID, request: Request, session: SessionDep) -> HTMLResponse:
    try:
        job = OperationsService(session).get_job(job_id)
    except ValueError as exc:
        raise HTTPException(404, "Job not found") from exc
    return templates.TemplateResponse(request=request, name="job_detail.html", context={"job": job})


@router.get("/dashboard/fragments/jobs", response_class=HTMLResponse)
def jobs_fragment(request: Request, session: SessionDep) -> HTMLResponse:
    jobs = OperationsService(session).list_jobs(limit=30)
    return templates.TemplateResponse(
        request=request, name="job_rows.html", context={"jobs": jobs.items}
    )


@router.get("/dashboard/schedules", response_class=HTMLResponse)
def schedules_page(request: Request, session: SessionDep) -> HTMLResponse:
    schedules = OperationsService(session).list_schedules()
    datasets = list(session.scalars(select(DatasetRow).order_by(DatasetRow.slug)))
    return templates.TemplateResponse(
        request=request,
        name="schedules.html",
        context={"schedules": schedules, "datasets": datasets},
    )


@router.get("/dashboard/schedules/{schedule_id}/history", response_class=HTMLResponse)
def schedule_history_page(schedule_id: UUID, request: Request, session: SessionDep) -> HTMLResponse:
    try:
        schedule = next(
            item for item in OperationsService(session).list_schedules() if item.id == schedule_id
        )
    except StopIteration as exc:
        raise HTTPException(404, "Schedule not found") from exc
    history = OperationsService(session).schedule_history(schedule_id)
    return templates.TemplateResponse(
        request=request,
        name="schedule_history.html",
        context={"schedule": schedule, "history": history},
    )


@router.post("/dashboard/schedules")
def create_schedule_form(
    dataset_id: FormUUID,
    interval_seconds: FormInteger,
    session: SessionDep,
) -> RedirectResponse:
    OperationsService(session).create_schedule(
        ScheduleInput(dataset_id=dataset_id, interval_seconds=interval_seconds)
    )
    return RedirectResponse(url="/dashboard/schedules", status_code=303)


@router.post("/dashboard/schedules/{schedule_id}/{action}")
def schedule_action(schedule_id: UUID, action: str, session: SessionDep) -> RedirectResponse:
    service = OperationsService(session)
    if action == "pause":
        service.set_schedule_enabled(schedule_id, False)
    elif action == "resume":
        service.set_schedule_enabled(schedule_id, True)
    elif action == "delete":
        service.delete_schedule(schedule_id)
    else:
        raise HTTPException(404, "Unknown schedule action")
    return RedirectResponse(url="/dashboard/schedules", status_code=303)


@router.post("/dashboard/schedules/{schedule_id}")
def update_schedule_form(
    schedule_id: UUID,
    dataset_id: FormUUID,
    interval_seconds: FormInteger,
    session: SessionDep,
) -> RedirectResponse:
    try:
        service = OperationsService(session)
        current = next(item for item in service.list_schedules() if item.id == schedule_id)
        session.rollback()
        same_dataset = current.dataset_id == dataset_id
        service.update_schedule(
            schedule_id,
            ScheduleInput(
                dataset_id=dataset_id,
                interval_seconds=interval_seconds,
                reliability_policy_id=current.reliability_policy_id if same_dataset else None,
                incident_policy_id=current.incident_policy_id if same_dataset else None,
            ),
        )
    except (ValueError, StopIteration) as exc:
        raise HTTPException(422, "Unable to update monitoring schedule") from exc
    return RedirectResponse(url="/dashboard/schedules", status_code=303)


@router.post("/dashboard/incidents/{incident_id}/ai")
def request_ai_form(
    incident_id: UUID,
    session: SessionDep,
    question: str = Form(default="Summarize evidence, hypotheses, and uncertainty."),
) -> RedirectResponse:
    try:
        investigation = queue_investigation(
            session, incident_id, InvestigationRequest(question=question)
        )
    except ValueError as exc:
        raise HTTPException(
            503 if "disabled" in str(exc).lower() else 404, "AI request unavailable"
        ) from exc
    return RedirectResponse(
        url=f"/dashboard/incidents/{incident_id}?ai={investigation.id}", status_code=303
    )
