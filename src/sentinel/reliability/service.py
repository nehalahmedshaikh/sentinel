"""Policies, baseline resolution, assessments, history, and investigation."""

from datetime import UTC, datetime
from uuid import UUID

import polars as pl
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from sentinel.domain.exceptions import PersistenceError, RuleConflictError, StorageError
from sentinel.ingestion.readers import read_frame
from sentinel.persistence.models import DatasetRow, DatasetVersionRow
from sentinel.persistence.repositories import get_dataset
from sentinel.quality.models import QualityAssessmentRow, QualityFindingRow
from sentinel.reliability.aggregation import assessment_identity, policy_fingerprint
from sentinel.reliability.contracts import (
    AssessmentStatus,
    BaselineStrategy,
    BaselineVersionView,
    ChangeOnsetView,
    DetectionPolicyConfig,
    DetectionPolicyInput,
    DetectionPolicyView,
    FindingStatus,
    InvestigationCorrelation,
    InvestigationView,
    ReliabilityAssessmentInput,
    ReliabilityAssessmentResult,
    ReliabilityAssessmentView,
    ReliabilityFindingView,
    Severity,
)
from sentinel.reliability.detectors import (
    detect_distribution_drift,
    detect_multivariate_anomalies,
    detect_schema_drift,
    detect_univariate_anomalies,
)
from sentinel.reliability.findings import FindingData
from sentinel.reliability.models import (
    DetectionPolicyRow,
    ReliabilityAssessmentRow,
    ReliabilityFindingRow,
)
from sentinel.storage.filesystem import FileStore


class ReliabilityService:
    """Manage detector policies and reproducible reliability assessments."""

    def __init__(self, session: Session, storage: FileStore) -> None:
        self.session = session
        self.storage = storage

    def create_policy(
        self, dataset_key: str | UUID, data: DetectionPolicyInput
    ) -> DetectionPolicyView:
        """Persist a validated dataset-specific detection policy."""
        try:
            with self.session.begin():
                dataset = get_dataset(self.session, dataset_key)
                self._lock_dataset(dataset.id)
                fingerprint = policy_fingerprint(data.config)
                row = DetectionPolicyRow(
                    dataset_id=dataset.id,
                    name=data.name,
                    config=data.config.model_dump(mode="json"),
                    fingerprint=fingerprint,
                    enabled=data.enabled,
                )
                self.session.add(row)
                self.session.flush()
                result = _policy_view(row)
            return result
        except IntegrityError as exc:
            raise RuleConflictError(
                f"Detection policy '{data.name}' already exists for this dataset"
            ) from exc
        except SQLAlchemyError as exc:
            raise PersistenceError(f"Unable to create detection policy: {exc}") from exc

    def get_policies(self, dataset_key: str | UUID) -> list[DetectionPolicyView]:
        """List all policies, including disabled policies, in stable order."""
        with self.session.begin():
            dataset = get_dataset(self.session, dataset_key)
            rows = self.session.scalars(
                select(DetectionPolicyRow)
                .where(DetectionPolicyRow.dataset_id == dataset.id)
                .order_by(DetectionPolicyRow.created_at, DetectionPolicyRow.id)
            )
            result = [_policy_view(row) for row in rows]
        return result

    def update_policy(self, policy_id: UUID, data: DetectionPolicyInput) -> DetectionPolicyView:
        """Replace a policy while leaving completed assessments unchanged."""
        try:
            with self.session.begin():
                row = self._locked_policy(policy_id)
                row.name = data.name
                row.config = data.config.model_dump(mode="json")
                row.fingerprint = policy_fingerprint(data.config)
                row.enabled = data.enabled
                row.updated_at = datetime.now(UTC)
                self.session.flush()
                result = _policy_view(row)
            return result
        except IntegrityError as exc:
            raise RuleConflictError(
                f"Detection policy '{data.name}' already exists for this dataset"
            ) from exc
        except SQLAlchemyError as exc:
            raise PersistenceError(f"Unable to update detection policy: {exc}") from exc

    def set_policy_enabled(self, policy_id: UUID, enabled: bool) -> DetectionPolicyView:
        """Enable or disable future use of a policy."""
        try:
            with self.session.begin():
                row = self._locked_policy(policy_id)
                row.enabled = enabled
                row.updated_at = datetime.now(UTC)
                self.session.flush()
                result = _policy_view(row)
            return result
        except SQLAlchemyError as exc:
            raise PersistenceError(f"Unable to change detection policy state: {exc}") from exc

    def run_assessment(self, request: ReliabilityAssessmentInput) -> ReliabilityAssessmentResult:
        """Resolve the baseline, evaluate detectors, and persist immutable evidence."""
        try:
            with self.session.begin():
                result = self._run_assessment(request)
            return result
        except StorageError, ValueError:
            raise
        except SQLAlchemyError as exc:
            raise PersistenceError(f"Unable to run reliability assessment: {exc}") from exc

    def _run_assessment(self, request: ReliabilityAssessmentInput) -> ReliabilityAssessmentResult:
        target = self.session.scalar(
            select(DatasetVersionRow).where(DatasetVersionRow.id == request.dataset_version_id)
        )
        if target is None:
            raise ValueError(f"Dataset version not found: {request.dataset_version_id}")
        dataset = self.session.scalar(
            select(DatasetRow).where(DatasetRow.id == target.dataset_id).with_for_update()
        )
        if dataset is None:
            raise ValueError(f"Dataset not found for version: {request.dataset_version_id}")
        self._lock_dataset(dataset.id)
        policy_row = self.session.scalar(
            select(DetectionPolicyRow)
            .where(
                DetectionPolicyRow.id == request.policy_id,
                DetectionPolicyRow.dataset_id == dataset.id,
            )
            .with_for_update()
        )
        if policy_row is None:
            raise ValueError(f"Detection policy not found for dataset: {request.policy_id}")
        if not policy_row.enabled:
            raise ValueError(f"Detection policy is disabled: {request.policy_id}")
        policy = DetectionPolicyConfig.model_validate(policy_row.config)
        baselines = self._resolve_baselines(target, request)
        baseline_refs = [
            {"id": str(row.id), "version_number": row.version_number} for row in baselines
        ]
        identity = assessment_identity(
            target.id,
            request.baseline_strategy,
            baseline_refs,
            policy_row.id,
            policy_row.fingerprint,
        )
        existing = self.session.scalar(
            select(ReliabilityAssessmentRow)
            .where(
                ReliabilityAssessmentRow.target_version_id == target.id,
                ReliabilityAssessmentRow.identity_fingerprint == identity,
            )
            .options(selectinload(ReliabilityAssessmentRow.findings))
        )
        if existing is not None:
            return _assessment_result(existing, target.version_number)

        target_path = self.storage.absolute(target.storage_key)
        if not target_path.is_file():
            raise StorageError(f"Managed snapshot for dataset version {target.id} is unavailable")
        target_frame = read_frame(target_path, target.format)
        findings: list[FindingData] = []
        baseline_frames: list[pl.DataFrame] = []
        for baseline in baselines:
            path = self.storage.absolute(baseline.storage_key)
            if not path.is_file():
                findings.append(
                    _detector_error(
                        "baseline_load", None, "Managed baseline snapshot is unavailable"
                    )
                )
                continue
            try:
                baseline_frames.append(read_frame(path, baseline.format))
            except Exception as exc:
                findings.append(
                    _detector_error("baseline_load", None, f"{type(exc).__name__}: {exc}")
                )
        baseline_frame: pl.DataFrame | None = None
        if baseline_frames:
            try:
                baseline_frame = (
                    baseline_frames[0]
                    if len(baseline_frames) == 1
                    else pl.concat(baseline_frames, how="diagonal_relaxed")
                )
            except Exception as exc:
                findings.append(
                    _detector_error("rolling_baseline", None, f"{type(exc).__name__}: {exc}")
                )

        if baseline_frame is None:
            if baselines:
                findings.extend(
                    _unavailable_comparisons(policy, "baseline data could not be loaded")
                )
            else:
                findings.extend(
                    _unavailable_comparisons(policy, "no baseline exists for this strategy")
                )
        else:
            nearest_baseline = baselines[-1]
            try:
                findings.extend(
                    detect_schema_drift(
                        nearest_baseline.schema_data,
                        target.schema_data,
                        policy,
                    )
                )
            except Exception as exc:
                findings.append(
                    _detector_error("schema_drift", None, f"{type(exc).__name__}: {exc}")
                )
            try:
                findings.extend(detect_distribution_drift(baseline_frame, target_frame, policy))
            except Exception as exc:
                findings.append(
                    _detector_error("distribution_drift", None, f"{type(exc).__name__}: {exc}")
                )
        try:
            findings.extend(detect_univariate_anomalies(target_frame, policy))
        except Exception as exc:
            findings.append(
                _detector_error("univariate_anomaly", None, f"{type(exc).__name__}: {exc}")
            )
        try:
            findings.extend(detect_multivariate_anomalies(target_frame, policy))
        except Exception as exc:
            findings.append(
                _detector_error("multivariate_anomaly", None, f"{type(exc).__name__}: {exc}")
            )

        if not any(
            [
                policy.schema_drift.enabled and baseline_frame is not None,
                policy.numeric_drift.enabled and baseline_frame is not None,
                policy.categorical_drift.enabled and baseline_frame is not None,
                policy.univariate_anomaly.enabled,
                policy.multivariate_anomaly.enabled,
            ]
        ):
            status, message = AssessmentStatus.ERROR, "No enabled detectors were configured."
        elif not baselines:
            status, message = (
                AssessmentStatus.NO_BASELINE,
                "No baseline version is available for comparison.",
            )
        elif findings and all(finding.status == FindingStatus.ERROR for finding in findings):
            status, message = AssessmentStatus.ERROR, "All enabled reliability detectors failed."
        elif any(
            finding.status in (FindingStatus.ERROR, FindingStatus.UNAVAILABLE)
            for finding in findings
        ):
            status, message = AssessmentStatus.PARTIAL, None
        else:
            status, message = AssessmentStatus.COMPLETE, None

        policy_snapshot = {
            "id": str(policy_row.id),
            "name": policy_row.name,
            "fingerprint": policy_row.fingerprint,
            "config": policy.model_dump(mode="json", by_alias=True),
        }
        assessment = ReliabilityAssessmentRow(
            dataset_id=dataset.id,
            target_version_id=target.id,
            baseline_strategy=request.baseline_strategy.value,
            baseline_versions=baseline_refs,
            policy_id=policy_row.id,
            policy_fingerprint=policy_row.fingerprint,
            policy_snapshot=policy_snapshot,
            identity_fingerprint=identity,
            status=status.value,
            finding_count=len(findings),
            evaluated_at=datetime.now(UTC),
            message=message,
            findings=[_finding_row(finding) for finding in findings],
        )
        self.session.add(assessment)
        self.session.flush()
        return _assessment_result(assessment, target.version_number)

    def retrieve_assessment(self, assessment_id: UUID) -> ReliabilityAssessmentResult:
        """Fetch one immutable assessment and all of its findings."""
        with self.session.begin():
            row = self.session.scalar(
                select(ReliabilityAssessmentRow)
                .where(ReliabilityAssessmentRow.id == assessment_id)
                .options(
                    selectinload(ReliabilityAssessmentRow.findings),
                    selectinload(ReliabilityAssessmentRow.target_version),
                )
            )
            if row is None:
                raise ValueError(f"Reliability assessment not found: {assessment_id}")
            result = _assessment_result(row, row.target_version.version_number)
        return result

    def assessment_history(self, dataset_key: str | UUID) -> list[ReliabilityAssessmentResult]:
        """Retrieve assessments in target version order for trend inspection."""
        with self.session.begin():
            dataset = get_dataset(self.session, dataset_key)
            rows = self.session.scalars(
                select(ReliabilityAssessmentRow)
                .join(
                    DatasetVersionRow,
                    DatasetVersionRow.id == ReliabilityAssessmentRow.target_version_id,
                )
                .where(ReliabilityAssessmentRow.dataset_id == dataset.id)
                .options(
                    selectinload(ReliabilityAssessmentRow.findings),
                    selectinload(ReliabilityAssessmentRow.target_version),
                )
                .order_by(DatasetVersionRow.version_number, ReliabilityAssessmentRow.evaluated_at)
            )
            result = [_assessment_result(row, row.target_version.version_number) for row in rows]
        return result

    def change_onset(
        self,
        dataset_key: str | UUID,
        detector: str,
        finding_type: str,
        column_name: str | None = None,
    ) -> ChangeOnsetView | None:
        """Return the earliest version with a matching detected finding."""
        history = self.assessment_history(dataset_key)
        matches = [
            (assessment, finding)
            for assessment in history
            for finding in assessment.findings
            if finding.status == FindingStatus.DETECTED
            and finding.detector == detector
            and finding.finding_type == finding_type
            and finding.column_name == column_name
        ]
        if not matches:
            return None
        assessment, finding = min(
            matches, key=lambda pair: pair[0].assessment.target_version_number
        )
        return ChangeOnsetView(
            dataset_id=assessment.assessment.dataset_id,
            detector=detector,
            finding_type=finding_type,
            column_name=column_name,
            first_version_id=assessment.assessment.target_version_id,
            first_version_number=assessment.assessment.target_version_number,
            assessment_id=assessment.assessment.id,
            evidence=finding.evidence,
        )

    def investigate(self, version_id: UUID) -> InvestigationView:
        """Combine persisted reliability and Phase 2 quality evidence deterministically."""
        with self.session.begin():
            target = self.session.scalar(
                select(DatasetVersionRow).where(DatasetVersionRow.id == version_id)
            )
            if target is None:
                raise ValueError(f"Dataset version not found: {version_id}")
            reliability_rows = list(
                self.session.scalars(
                    select(ReliabilityAssessmentRow)
                    .where(ReliabilityAssessmentRow.target_version_id == version_id)
                    .options(selectinload(ReliabilityAssessmentRow.findings))
                )
            )
            quality_rows = list(
                self.session.scalars(
                    select(QualityAssessmentRow)
                    .where(QualityAssessmentRow.dataset_version_id == version_id)
                    .options(selectinload(QualityAssessmentRow.findings))
                )
            )
            previous = (
                self.session.scalar(
                    select(DatasetVersionRow).where(
                        DatasetVersionRow.dataset_id == target.dataset_id,
                        DatasetVersionRow.version_number == target.version_number - 1,
                    )
                )
                if target.version_number > 1
                else None
            )
            previous_quality = []
            if previous is not None:
                previous_quality = list(
                    self.session.scalars(
                        select(QualityAssessmentRow)
                        .where(QualityAssessmentRow.dataset_version_id == previous.id)
                        .options(selectinload(QualityAssessmentRow.findings))
                    )
                )
            result = _investigation(target, reliability_rows, quality_rows, previous_quality)
        return result

    def _resolve_baselines(
        self, target: DatasetVersionRow, request: ReliabilityAssessmentInput
    ) -> list[DatasetVersionRow]:
        strategy = request.baseline_strategy
        if strategy == BaselineStrategy.PREVIOUS:
            if target.version_number <= 1:
                return []
            row = self.session.scalar(
                select(DatasetVersionRow).where(
                    DatasetVersionRow.dataset_id == target.dataset_id,
                    DatasetVersionRow.version_number == target.version_number - 1,
                )
            )
            return [row] if row is not None else []
        if strategy == BaselineStrategy.INITIAL:
            if target.version_number == 1:
                return []
            row = self.session.scalar(
                select(DatasetVersionRow)
                .where(DatasetVersionRow.dataset_id == target.dataset_id)
                .order_by(DatasetVersionRow.version_number)
                .limit(1)
            )
            return [row] if row is not None else []
        if strategy == BaselineStrategy.EXPLICIT:
            row = self.session.scalar(
                select(DatasetVersionRow).where(
                    DatasetVersionRow.id == request.explicit_baseline_version_id
                )
            )
            if row is None or row.dataset_id != target.dataset_id:
                raise ValueError(
                    "Explicit baseline version must belong to the same dataset as the target"
                )
            if row.id == target.id:
                raise ValueError("Target version cannot be its own explicit baseline")
            return [row]
        rows = list(
            self.session.scalars(
                select(DatasetVersionRow)
                .where(
                    DatasetVersionRow.dataset_id == target.dataset_id,
                    DatasetVersionRow.version_number < target.version_number,
                )
                .order_by(DatasetVersionRow.version_number.desc())
                .limit(request.rolling_window)
            )
        )
        return sorted(rows, key=lambda row: row.version_number)

    def _lock_dataset(self, dataset_id: UUID) -> None:
        self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:dataset_key))"),
            {"dataset_key": str(dataset_id)},
        )

    def _locked_policy(self, policy_id: UUID) -> DetectionPolicyRow:
        row = self.session.scalar(
            select(DetectionPolicyRow).where(DetectionPolicyRow.id == policy_id)
        )
        if row is None:
            raise ValueError(f"Detection policy not found: {policy_id}")
        self._lock_dataset(row.dataset_id)
        locked = self.session.scalar(
            select(DetectionPolicyRow).where(DetectionPolicyRow.id == policy_id).with_for_update()
        )
        if locked is None:
            raise ValueError(f"Detection policy not found: {policy_id}")
        return locked


def _detector_error(detector: str, column: str | None, message: str) -> FindingData:
    return FindingData(
        detector=detector,
        finding_type="detector_error",
        status=FindingStatus.ERROR,
        severity=Severity.WARNING,
        column_name=column,
        evidence={"error": message},
        message=f"{detector} failed: {message}",
    )


def _unavailable_comparisons(policy: DetectionPolicyConfig, reason: str) -> list[FindingData]:
    findings: list[FindingData] = []
    if policy.schema_drift.enabled:
        findings.append(_unavailable("schema_drift", reason))
    if policy.numeric_drift.enabled or policy.categorical_drift.enabled:
        findings.append(_unavailable("distribution_drift", reason))
    return findings


def _unavailable(detector: str, reason: str) -> FindingData:
    return FindingData(
        detector=detector,
        finding_type="comparison_unavailable",
        status=FindingStatus.UNAVAILABLE,
        evidence={"reason": reason},
        message=f"{detector} comparison unavailable: {reason}",
    )


def _finding_row(finding: FindingData) -> ReliabilityFindingRow:
    return ReliabilityFindingRow(
        detector=finding.detector,
        finding_type=finding.finding_type,
        status=finding.status.value,
        severity=finding.severity.value,
        column_name=finding.column_name,
        baseline_value=finding.baseline_value,
        target_value=finding.target_value,
        evidence=finding.evidence,
        message=finding.message,
    )


def _policy_view(row: DetectionPolicyRow) -> DetectionPolicyView:
    config = DetectionPolicyConfig.model_validate(row.config)
    return DetectionPolicyView(
        id=row.id,
        dataset_id=row.dataset_id,
        name=row.name,
        config=config,
        fingerprint=row.fingerprint,
        enabled=row.enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _assessment_result(
    row: ReliabilityAssessmentRow, target_version_number: int
) -> ReliabilityAssessmentResult:
    assessment = ReliabilityAssessmentView(
        id=row.id,
        dataset_id=row.dataset_id,
        target_version_id=row.target_version_id,
        target_version_number=target_version_number,
        baseline_strategy=BaselineStrategy(row.baseline_strategy),
        baseline_versions=[
            BaselineVersionView.model_validate(item) for item in row.baseline_versions
        ],
        policy_id=row.policy_id,
        policy_fingerprint=row.policy_fingerprint,
        policy_snapshot=row.policy_snapshot,
        identity_fingerprint=row.identity_fingerprint,
        status=AssessmentStatus(row.status),
        finding_count=row.finding_count,
        evaluated_at=row.evaluated_at,
        message=row.message,
    )
    findings = [
        ReliabilityFindingView(
            id=finding.id,
            assessment_id=finding.assessment_id,
            detector=finding.detector,
            finding_type=finding.finding_type,
            status=FindingStatus(finding.status),
            severity=Severity(finding.severity),
            column_name=finding.column_name,
            baseline_value=finding.baseline_value,
            target_value=finding.target_value,
            evidence=finding.evidence,
            message=finding.message,
        )
        for finding in row.findings
    ]
    return ReliabilityAssessmentResult(assessment=assessment, findings=findings)


def _investigation(
    target: DatasetVersionRow,
    reliability_rows: list[ReliabilityAssessmentRow],
    quality_rows: list[QualityAssessmentRow],
    previous_quality: list[QualityAssessmentRow],
) -> InvestigationView:
    reliability_findings = [finding for row in reliability_rows for finding in row.findings]
    detected = [
        finding
        for finding in reliability_findings
        if finding.status == FindingStatus.DETECTED.value
    ]
    affected = {finding.column_name for finding in detected if finding.column_name is not None}
    for assessment in quality_rows:
        for finding in assessment.findings:
            if finding.status in {"FAIL", "ERROR"}:
                column = _quality_column(finding)
                if column is not None:
                    affected.add(column)
    quality_findings_with_assessment = [
        (assessment, finding)
        for assessment in quality_rows
        for finding in assessment.findings
        if finding.rule_id is not None and finding.status in {"FAIL", "ERROR"}
    ]
    correlations: list[InvestigationCorrelation] = []
    for reliability in detected:
        same_column = [
            (assessment, finding)
            for assessment, finding in quality_findings_with_assessment
            if _quality_column(finding) == reliability.column_name
        ]
        if same_column:
            affected.update(filter(None, (_quality_column(item) for _, item in same_column)))
            correlations.append(
                InvestigationCorrelation(
                    kind="same_version_co_occurrence",
                    column_name=reliability.column_name,
                    reliability_finding_ids=[reliability.id],
                    quality_assessment_id=same_column[0][0].id,
                    quality_finding_ids=[item.id for _, item in same_column],
                    description=(
                        "A reliability change and a failing quality rule affect "
                        "the same version and column."
                    ),
                )
            )
    previous_by_ruleset_and_rule: dict[tuple[str, UUID], list[QualityFindingRow]] = {}
    for assessment in previous_quality:
        for finding in assessment.findings:
            if finding.rule_id is not None:
                key = (assessment.ruleset_fingerprint, finding.rule_id)
                previous_by_ruleset_and_rule.setdefault(key, []).append(finding)
    for assessment, finding in quality_findings_with_assessment:
        if finding.status != "FAIL" or finding.rule_id is None:
            continue
        previous_results = previous_by_ruleset_and_rule.get(
            (assessment.ruleset_fingerprint, finding.rule_id), []
        )
        if any(old.status == "PASS" for old in previous_results):
            column = _quality_column(finding)
            affected.update(filter(None, (column,)))
            related = [item for item in detected if item.column_name == column]
            correlations.append(
                InvestigationCorrelation(
                    kind="quality_deterioration_with_change",
                    column_name=column,
                    reliability_finding_ids=[item.id for item in related],
                    quality_assessment_id=assessment.id,
                    quality_finding_ids=[finding.id],
                    description=(
                        "A previously passing quality rule failed on this version; "
                        "concurrent changes are correlations only."
                    ),
                )
            )
    observed = [
        f"Version {target.version_number} has {len(reliability_rows)} reliability assessment(s).",
        f"Version {target.version_number} has {len(quality_rows)} quality assessment(s).",
    ]
    observed.extend(
        f"{finding.detector} reported {finding.finding_type} on "
        f"{finding.column_name or 'the dataset'} ({finding.status})."
        for finding in detected
    )
    limitations = ["Co-occurrence and timing do not establish causation."]
    if not quality_rows:
        limitations.append("No Phase 2 quality assessment exists for the target version.")
    if not reliability_rows:
        limitations.append("No reliability assessment exists for the target version.")
    if not previous_quality:
        limitations.append(
            "No previous-version quality assessment is available for deterioration comparison."
        )
    return InvestigationView(
        dataset_id=target.dataset_id,
        dataset_version_id=target.id,
        version_number=target.version_number,
        reliability_assessment_ids=[row.id for row in reliability_rows],
        quality_assessment_ids=[row.id for row in quality_rows],
        affected_columns=sorted(affected),
        correlations=correlations,
        observed_facts=observed,
        limitations=limitations,
    )


def _quality_column(finding: QualityFindingRow) -> str | None:
    parameters = finding.rule_snapshot.get("parameters")
    if isinstance(parameters, dict):
        value = parameters.get("column_name")
        return value if isinstance(value, str) else None
    return None
