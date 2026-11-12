"""Application orchestration for rule management and assessments."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from sentinel.domain.exceptions import (
    AssessmentNotFoundError,
    PersistenceError,
    QualityRuleNotFoundError,
    RuleConflictError,
    StorageError,
)
from sentinel.ingestion.readers import read_frame
from sentinel.persistence.models import DatasetRow, DatasetVersionRow
from sentinel.persistence.repositories import get_dataset
from sentinel.quality.aggregation import assessment_summary, rule_snapshot, ruleset_fingerprint
from sentinel.quality.contracts import (
    AssessmentResult,
    FindingResult,
    QualityAssessmentView,
    QualityFindingView,
    QualityRuleInput,
    QualityRuleView,
    ResultStatus,
    Severity,
)
from sentinel.quality.engine import evaluate_rule
from sentinel.quality.models import QualityAssessmentRow, QualityFindingRow, QualityRuleRow
from sentinel.storage.filesystem import FileStore


class QualityService:
    """Manage dataset rules and create immutable version assessments."""

    def __init__(self, session: Session, storage: FileStore) -> None:
        self.session = session
        self.storage = storage

    def create_rule(self, dataset_key: str | UUID, data: QualityRuleInput) -> QualityRuleView:
        """Create a validated rule for an existing dataset."""
        try:
            with self.session.begin():
                dataset = get_dataset(self.session, dataset_key)
                self._lock_dataset(dataset.id)
                row = QualityRuleRow(
                    dataset_id=dataset.id,
                    name=data.name,
                    rule_type=data.rule_type.value,
                    parameters=data.parameters.model_dump(mode="json"),
                    severity=data.severity.value,
                    enabled=data.enabled,
                )
                self.session.add(row)
                self.session.flush()
                result = _rule_view(row)
            return result
        except IntegrityError as exc:
            raise RuleConflictError(
                f"A rule named '{data.name}' already exists for this dataset"
            ) from exc
        except SQLAlchemyError as exc:
            raise PersistenceError(f"Unable to create quality rule: {exc}") from exc

    def get_rules(
        self, dataset_key: str | UUID, *, include_disabled: bool = True
    ) -> list[QualityRuleView]:
        """List rules for a dataset in stable creation/id order."""
        with self.session.begin():
            dataset = get_dataset(self.session, dataset_key)
            statement = select(QualityRuleRow).where(QualityRuleRow.dataset_id == dataset.id)
            if not include_disabled:
                statement = statement.where(QualityRuleRow.enabled.is_(True))
            statement = statement.order_by(QualityRuleRow.created_at, QualityRuleRow.id)
            result = [_rule_view(row) for row in self.session.scalars(statement)]
        return result

    def update_rule(self, rule_id: UUID, data: QualityRuleInput) -> QualityRuleView:
        """Replace a rule definition without changing past findings."""
        try:
            with self.session.begin():
                row = self._locked_rule(rule_id)
                row.name = data.name
                row.rule_type = data.rule_type.value
                row.parameters = data.parameters.model_dump(mode="json")
                row.severity = data.severity.value
                row.enabled = data.enabled
                row.updated_at = datetime.now(UTC)
                self.session.flush()
                result = _rule_view(row)
            return result
        except QualityRuleNotFoundError:
            raise
        except IntegrityError as exc:
            raise RuleConflictError(
                f"A rule named '{data.name}' already exists for this dataset"
            ) from exc
        except SQLAlchemyError as exc:
            raise PersistenceError(f"Unable to update quality rule: {exc}") from exc

    def set_rule_enabled(self, rule_id: UUID, enabled: bool) -> QualityRuleView:
        """Enable or disable a rule while retaining its historical assessments."""
        try:
            with self.session.begin():
                row = self._locked_rule(rule_id)
                row.enabled = enabled
                row.updated_at = datetime.now(UTC)
                self.session.flush()
                result = _rule_view(row)
            return result
        except QualityRuleNotFoundError:
            raise
        except SQLAlchemyError as exc:
            raise PersistenceError(f"Unable to change quality rule state: {exc}") from exc

    def delete_rule(self, rule_id: UUID) -> None:
        """Delete a rule only if no historical finding depends on its identity."""
        try:
            with self.session.begin():
                row = self._locked_rule(rule_id)
                if (
                    self.session.scalar(
                        select(QualityFindingRow.id)
                        .where(QualityFindingRow.rule_id == row.id)
                        .limit(1)
                    )
                    is not None
                ):
                    raise RuleConflictError(
                        "Rule has historical findings; disable it to preserve history"
                    )
                self.session.delete(row)
        except QualityRuleNotFoundError, RuleConflictError:
            raise
        except SQLAlchemyError as exc:
            raise PersistenceError(f"Unable to delete quality rule: {exc}") from exc

    def evaluate_version(self, version_id: UUID) -> AssessmentResult:
        """Evaluate enabled rules for one persisted dataset version."""
        try:
            with self.session.begin():
                result = self._evaluate_version_in_transaction(version_id)
            return result
        except AssessmentNotFoundError, StorageError:
            raise
        except SQLAlchemyError as exc:
            raise PersistenceError(
                f"Unable to evaluate or persist quality assessment: {exc}"
            ) from exc

    def _evaluate_version_in_transaction(self, version_id: UUID) -> AssessmentResult:
        """Evaluate a version while holding the dataset transaction lock."""
        try:
            version = self.session.scalar(
                select(DatasetVersionRow).where(DatasetVersionRow.id == version_id)
            )
            if version is None:
                raise AssessmentNotFoundError(f"Dataset version not found: {version_id}")
            source = self.storage.absolute(version.storage_key)
            if not source.is_file():
                raise StorageError(
                    f"Managed snapshot for dataset version {version_id} is unavailable"
                )
            frame = read_frame(source, version.format)
            dataset = self.session.scalar(
                select(DatasetRow).where(DatasetRow.id == version.dataset_id).with_for_update()
            )
            if dataset is None:
                raise AssessmentNotFoundError(f"Dataset not found for version: {version_id}")
            self._lock_dataset(dataset.id)
            rules = list(
                self.session.scalars(
                    select(QualityRuleRow)
                    .where(
                        QualityRuleRow.dataset_id == dataset.id, QualityRuleRow.enabled.is_(True)
                    )
                    .order_by(QualityRuleRow.id)
                )
            )
            rule_inputs = [_rule_input(row) for row in rules]
            snapshots = [
                rule_snapshot(str(row.id), data)
                for row, data in zip(rules, rule_inputs, strict=True)
            ]
            fingerprint = ruleset_fingerprint(snapshots)
            existing = self.session.scalar(
                select(QualityAssessmentRow)
                .where(
                    QualityAssessmentRow.dataset_version_id == version.id,
                    QualityAssessmentRow.ruleset_fingerprint == fingerprint,
                )
                .options(selectinload(QualityAssessmentRow.findings))
            )
            if existing is not None:
                return _assessment_result(existing)

            results: list[FindingResult] = []
            for row, data, snapshot in zip(rules, rule_inputs, snapshots, strict=True):
                try:
                    outcome = evaluate_rule(data, frame)
                except Exception as exc:
                    outcome = FindingResult(
                        rule_id=None,
                        rule_snapshot={},
                        status=ResultStatus.ERROR,
                        severity=data.severity,
                        evaluated_records=None,
                        violating_records=None,
                        violation_percentage=None,
                        evidence={"exception_type": type(exc).__name__},
                        message=f"Rule evaluation failed: {type(exc).__name__}: {exc}",
                    )
                results.append(
                    outcome.model_copy(update={"rule_id": row.id, "rule_snapshot": snapshot})
                )
            status, total, passed, failed, errors, message = assessment_summary(results, snapshots)
            assessment = QualityAssessmentRow(
                dataset_version_id=version.id,
                ruleset_fingerprint=fingerprint,
                rules_snapshot=snapshots,
                status=status.value,
                total_rules=total,
                passed_rules=passed,
                failed_rules=failed,
                error_rules=errors,
                evaluated_at=datetime.now(UTC),
                message=message,
                findings=[_finding_row(result) for result in results],
            )
            self.session.add(assessment)
            self.session.flush()
            return _assessment_result(assessment)
        except AssessmentNotFoundError, StorageError:
            raise

    def retrieve_assessment(self, assessment_id: UUID) -> AssessmentResult:
        """Retrieve an assessment and its persisted findings."""
        with self.session.begin():
            row = self.session.scalar(
                select(QualityAssessmentRow)
                .where(QualityAssessmentRow.id == assessment_id)
                .options(selectinload(QualityAssessmentRow.findings))
            )
            if row is None:
                raise AssessmentNotFoundError(f"Quality assessment not found: {assessment_id}")
            result = _assessment_result(row)
        return result

    def retrieve_findings(self, assessment_id: UUID) -> list[QualityFindingView]:
        """Retrieve findings in stable creation order."""
        return self.retrieve_assessment(assessment_id).findings

    def assessment_history(self, dataset_key: str | UUID) -> list[AssessmentResult]:
        """Retrieve all assessment results for versions of one dataset."""
        with self.session.begin():
            dataset = get_dataset(self.session, dataset_key)
            rows = self.session.scalars(
                select(QualityAssessmentRow)
                .join(
                    DatasetVersionRow,
                    DatasetVersionRow.id == QualityAssessmentRow.dataset_version_id,
                )
                .where(DatasetVersionRow.dataset_id == dataset.id)
                .options(selectinload(QualityAssessmentRow.findings))
                .order_by(QualityAssessmentRow.evaluated_at, QualityAssessmentRow.id)
            )
            result = [_assessment_result(row) for row in rows]
        return result

    def _lock_dataset(self, dataset_id: UUID) -> None:
        self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:dataset_key))"),
            {"dataset_key": str(dataset_id)},
        )

    def _locked_rule(self, rule_id: UUID) -> QualityRuleRow:
        row = self.session.scalar(select(QualityRuleRow).where(QualityRuleRow.id == rule_id))
        if row is None:
            raise QualityRuleNotFoundError(f"Quality rule not found: {rule_id}")
        self._lock_dataset(row.dataset_id)
        locked = self.session.scalar(
            select(QualityRuleRow).where(QualityRuleRow.id == rule_id).with_for_update()
        )
        if locked is None:
            raise QualityRuleNotFoundError(f"Quality rule not found: {rule_id}")
        return locked


def _rule_input(row: QualityRuleRow) -> QualityRuleInput:
    return QualityRuleInput.model_validate(
        {
            "name": row.name,
            "rule_type": row.rule_type,
            "parameters": row.parameters,
            "severity": row.severity,
            "enabled": row.enabled,
        }
    )


def _rule_view(row: QualityRuleRow) -> QualityRuleView:
    data = _rule_input(row)
    return QualityRuleView(
        id=row.id,
        dataset_id=row.dataset_id,
        name=row.name,
        rule_type=data.rule_type,
        parameters=data.parameters,
        severity=data.severity,
        enabled=row.enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _finding_row(result: FindingResult) -> QualityFindingRow:
    return QualityFindingRow(
        rule_id=result.rule_id,
        rule_snapshot=result.rule_snapshot,
        status=result.status.value,
        severity=result.severity.value,
        evaluated_records=result.evaluated_records,
        violating_records=result.violating_records,
        violation_percentage=result.violation_percentage,
        evidence=result.evidence,
        message=result.message,
    )


def _assessment_result(row: QualityAssessmentRow) -> AssessmentResult:
    assessment = QualityAssessmentView(
        id=row.id,
        dataset_version_id=row.dataset_version_id,
        ruleset_fingerprint=row.ruleset_fingerprint,
        rules_snapshot=row.rules_snapshot,
        status=ResultStatus(row.status),
        total_rules=row.total_rules,
        passed_rules=row.passed_rules,
        failed_rules=row.failed_rules,
        error_rules=row.error_rules,
        evaluated_at=row.evaluated_at,
        message=row.message,
    )
    findings = [
        QualityFindingView(
            id=finding.id,
            assessment_id=finding.assessment_id,
            rule_id=finding.rule_id,
            rule_snapshot=finding.rule_snapshot,
            status=ResultStatus(finding.status),
            severity=Severity(finding.severity),
            evaluated_records=finding.evaluated_records,
            violating_records=finding.violating_records,
            violation_percentage=finding.violation_percentage,
            evidence=finding.evidence,
            message=finding.message,
        )
        for finding in row.findings
    ]
    return AssessmentResult(assessment=assessment, findings=findings)
