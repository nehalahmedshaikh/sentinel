"""Incident generation, correlation, lifecycle, reporting, and history queries."""

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from sentinel.incidents.contracts import (
    GenerationDecision,
    GenerationResult,
    HistoricalSummary,
    IncidentDetail,
    IncidentEventView,
    IncidentEvidenceView,
    IncidentPolicyConfig,
    IncidentPolicyInput,
    IncidentPolicyView,
    IncidentSeverity,
    IncidentStatus,
    IncidentView,
    InvestigationReport,
    LinkInput,
    MergeInput,
    NoteInput,
    ReportFact,
    ResolveInput,
    SeverityInput,
    SourceType,
)
from sentinel.incidents.models import (
    IncidentEventRow,
    IncidentEvidenceRow,
    IncidentGenerationDecisionRow,
    IncidentPolicyRow,
    IncidentRow,
)
from sentinel.persistence.models import DatasetRow, DatasetVersionRow
from sentinel.quality.models import QualityAssessmentRow, QualityFindingRow
from sentinel.reliability.models import ReliabilityAssessmentRow, ReliabilityFindingRow

_TRANSITIONS: dict[IncidentStatus, set[IncidentStatus]] = {
    IncidentStatus.OPEN: {
        IncidentStatus.ACKNOWLEDGED,
        IncidentStatus.INVESTIGATING,
        IncidentStatus.RESOLVED,
    },
    IncidentStatus.ACKNOWLEDGED: {IncidentStatus.INVESTIGATING, IncidentStatus.RESOLVED},
    IncidentStatus.INVESTIGATING: {IncidentStatus.RESOLVED},
    IncidentStatus.RESOLVED: {IncidentStatus.REOPENED},
    IncidentStatus.REOPENED: {
        IncidentStatus.ACKNOWLEDGED,
        IncidentStatus.INVESTIGATING,
        IncidentStatus.RESOLVED,
    },
}
_SEVERITY_RANK = {
    IncidentSeverity.INFORMATIONAL: 0,
    IncidentSeverity.LOW: 1,
    IncidentSeverity.MEDIUM: 2,
    IncidentSeverity.HIGH: 3,
    IncidentSeverity.CRITICAL: 4,
}


def correlation_fingerprint(dataset_id: UUID, finding_type: str, columns: list[str]) -> str:
    """Stable identity for findings: dataset UUID + exact category + normalized columns."""
    payload = {
        "dataset_id": str(dataset_id),
        "finding_type": finding_type.casefold(),
        "columns": sorted({column.casefold() for column in columns}),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class IncidentService:
    """Manage incidents derived from immutable Phase 2 and Phase 3 findings."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_policy(
        self, dataset_key: str | UUID, data: IncidentPolicyInput
    ) -> IncidentPolicyView:
        with self.session.begin():
            dataset = self._dataset(dataset_key)
            row = IncidentPolicyRow(
                dataset_id=dataset.id,
                name=data.name,
                config=data.config.model_dump(mode="json"),
                fingerprint=_fingerprint(data.config.model_dump(mode="json")),
                enabled=data.enabled,
            )
            self.session.add(row)
            self.session.flush()
            return _policy_view(row)

    def get_policies(self, dataset_key: str | UUID) -> list[IncidentPolicyView]:
        with self.session.begin():
            dataset = self._dataset(dataset_key)
            rows = self.session.scalars(
                select(IncidentPolicyRow)
                .where(IncidentPolicyRow.dataset_id == dataset.id)
                .order_by(IncidentPolicyRow.name, IncidentPolicyRow.id)
            )
            return [_policy_view(row) for row in rows]

    def update_policy(self, policy_id: UUID, data: IncidentPolicyInput) -> IncidentPolicyView:
        """Replace future incident-generation settings without rewriting old decisions."""
        with self.session.begin():
            row = self.session.scalar(
                select(IncidentPolicyRow).where(IncidentPolicyRow.id == policy_id).with_for_update()
            )
            if row is None:
                raise ValueError(f"Incident policy not found: {policy_id}")
            row.name = data.name
            row.config = data.config.model_dump(mode="json")
            row.fingerprint = _fingerprint(row.config)
            row.enabled = data.enabled
            self.session.flush()
            return _policy_view(row)

    def set_policy_enabled(self, policy_id: UUID, enabled: bool) -> IncidentPolicyView:
        """Enable or disable a policy for subsequent assessment processing."""
        with self.session.begin():
            row = self.session.scalar(
                select(IncidentPolicyRow).where(IncidentPolicyRow.id == policy_id).with_for_update()
            )
            if row is None:
                raise ValueError(f"Incident policy not found: {policy_id}")
            row.enabled = enabled
            return _policy_view(row)

    def process_quality_assessment(self, assessment_id: UUID, policy_id: UUID) -> GenerationResult:
        with self.session.begin():
            assessment = self.session.scalar(
                select(QualityAssessmentRow).where(QualityAssessmentRow.id == assessment_id)
            )
            if assessment is None:
                raise ValueError(f"Quality assessment not found: {assessment_id}")
            return self._process(SourceType.QUALITY, assessment, policy_id)

    def process_reliability_assessment(
        self, assessment_id: UUID, policy_id: UUID
    ) -> GenerationResult:
        with self.session.begin():
            assessment = self.session.scalar(
                select(ReliabilityAssessmentRow).where(ReliabilityAssessmentRow.id == assessment_id)
            )
            if assessment is None:
                raise ValueError(f"Reliability assessment not found: {assessment_id}")
            return self._process(SourceType.RELIABILITY, assessment, policy_id)

    def _process(self, source: SourceType, assessment: Any, policy_id: UUID) -> GenerationResult:
        version_id = (
            assessment.dataset_version_id
            if source == SourceType.QUALITY
            else assessment.target_version_id
        )
        version = self.session.scalar(
            select(DatasetVersionRow).where(DatasetVersionRow.id == version_id)
        )
        if version is None:
            raise ValueError(f"Dataset version not found: {version_id}")
        dataset = self.session.scalar(
            select(DatasetRow).where(DatasetRow.id == version.dataset_id).with_for_update()
        )
        if dataset is None:
            raise ValueError(f"Dataset not found for version: {version_id}")
        self._advisory_lock(dataset.id)
        policy_row = self.session.scalar(
            select(IncidentPolicyRow)
            .where(
                IncidentPolicyRow.id == policy_id,
                IncidentPolicyRow.dataset_id == dataset.id,
            )
            .with_for_update()
        )
        if policy_row is None or not policy_row.enabled:
            raise ValueError(f"Enabled incident policy not found for dataset: {policy_id}")
        policy = IncidentPolicyConfig.model_validate(policy_row.config)
        if source == SourceType.QUALITY:
            findings = list(
                self.session.scalars(
                    select(QualityFindingRow)
                    .where(QualityFindingRow.assessment_id == assessment.id)
                    .order_by(QualityFindingRow.id)
                )
            )
            assessed_at = assessment.evaluated_at
        else:
            findings = list(
                self.session.scalars(
                    select(ReliabilityFindingRow)
                    .where(ReliabilityFindingRow.assessment_id == assessment.id)
                    .order_by(ReliabilityFindingRow.id)
                )
            )
            assessed_at = assessment.evaluated_at
        decisions: list[GenerationDecision] = []
        incident_ids: set[UUID] = set()
        for finding in findings:
            prior = self.session.scalar(
                select(IncidentGenerationDecisionRow).where(
                    IncidentGenerationDecisionRow.source_type == source.value,
                    IncidentGenerationDecisionRow.finding_id == finding.id,
                )
            )
            if prior is not None:
                decisions.append(
                    GenerationDecision(
                        source_type=source,
                        finding_id=finding.id,
                        qualifies=prior.qualifies,
                        reason=prior.reason,
                        incident_id=prior.incident_id,
                    )
                )
                if prior.incident_id:
                    incident_ids.add(prior.incident_id)
                continue
            if isinstance(finding, QualityFindingRow):
                finding_type = finding.rule_snapshot.get("rule_type", "quality_rule")
            else:
                finding_type = finding.finding_type
            status = finding.status
            severity = finding.severity
            columns, metadata = _finding_metadata(source, finding)
            qualifies, reason = _qualification(
                policy, source, str(finding_type), str(status), str(severity)
            )
            incident: IncidentRow | None = None
            if qualifies:
                fingerprint = correlation_fingerprint(dataset.id, str(finding_type), columns)
                incident, match_reason = self._correlation_candidate(
                    dataset.id, str(finding_type), fingerprint, columns, assessed_at, policy
                )
                if incident is None:
                    incident = self._new_incident(
                        dataset,
                        str(finding_type),
                        columns,
                        fingerprint,
                        severity,
                        metadata,
                        assessed_at,
                        policy,
                    )
                    self._event(
                        incident,
                        "created",
                        new={"status": IncidentStatus.OPEN.value},
                        details={"source_type": source.value, "finding_type": finding_type},
                    )
                    reason = f"{reason}; created incident ({match_reason})"
                else:
                    reason = f"{reason}; correlated with existing incident ({match_reason})"
                    if incident.status == IncidentStatus.RESOLVED:
                        self._transition(
                            incident, IncidentStatus.REOPENED, {"reason": "new qualifying evidence"}
                        )
                    incident.last_observed_at = max(incident.last_observed_at, assessed_at)
                    incident.affected_columns = sorted(
                        set(incident.affected_columns) | set(columns)
                    )
                    incident.updated_at = datetime.now(UTC)
                metadata = {
                    **metadata,
                    "correlation_fingerprint": fingerprint,
                    "correlation_decision": match_reason,
                }
                evidence = IncidentEvidenceRow(
                    incident_id=incident.id,
                    source_type=source.value,
                    assessment_id=assessment.id,
                    finding_id=finding.id,
                    quality_assessment_id=assessment.id if source == SourceType.QUALITY else None,
                    quality_finding_id=finding.id if source == SourceType.QUALITY else None,
                    reliability_assessment_id=assessment.id
                    if source == SourceType.RELIABILITY
                    else None,
                    reliability_finding_id=finding.id if source == SourceType.RELIABILITY else None,
                    dataset_version_id=version.id,
                    finding_type=str(finding_type),
                    source_severity=str(severity),
                    affected_columns=columns,
                    evidence_metadata=metadata,
                )
                self.session.add(evidence)
                self._event(
                    incident,
                    "evidence_attached",
                    details={
                        "evidence_source": source.value,
                        "finding_id": str(finding.id),
                        "reason": match_reason if incident.id else "new incident",
                    },
                )
                self._reassess_severity(incident, policy, severity, metadata)
                incident_ids.add(incident.id)
            else:
                reason = f"{reason}; no incident created"
            decision_row = IncidentGenerationDecisionRow(
                source_type=source.value,
                assessment_id=assessment.id,
                finding_id=finding.id,
                quality_assessment_id=assessment.id if source == SourceType.QUALITY else None,
                quality_finding_id=finding.id if source == SourceType.QUALITY else None,
                reliability_assessment_id=assessment.id
                if source == SourceType.RELIABILITY
                else None,
                reliability_finding_id=finding.id if source == SourceType.RELIABILITY else None,
                qualifies=qualifies,
                reason=reason,
                incident_id=incident.id if incident else None,
            )
            self.session.add(decision_row)
            decisions.append(
                GenerationDecision(
                    source_type=source,
                    finding_id=finding.id,
                    qualifies=qualifies,
                    reason=reason,
                    incident_id=incident.id if incident else None,
                )
            )
        self.session.flush()
        rows = [self.session.get(IncidentRow, item) for item in sorted(incident_ids, key=str)]
        return GenerationResult(
            processed_assessment_id=assessment.id,
            source_type=source,
            decisions=decisions,
            incidents=[_incident_view(row, dataset.slug) for row in rows if row is not None],
        )

    def list_incidents(
        self,
        dataset_key: str | UUID,
        *,
        status: IncidentStatus | None = None,
        severity: IncidentSeverity | None = None,
        limit: int = 100,
    ) -> list[IncidentView]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self.session.begin():
            dataset = self._dataset(dataset_key)
            query = select(IncidentRow).where(IncidentRow.dataset_id == dataset.id)
            if status:
                query = query.where(IncidentRow.status == status.value)
            if severity:
                query = query.where(IncidentRow.severity == severity.value)
            rows = self.session.scalars(
                query.order_by(IncidentRow.last_observed_at.desc(), IncidentRow.id).limit(limit)
            )
            return [_incident_view(row, dataset.slug) for row in rows]

    def retrieve(self, incident_id: UUID) -> IncidentDetail:
        with self.session.begin():
            row = self._incident(incident_id)
            dataset = self.session.get(DatasetRow, row.dataset_id)
            evidence_rows = self._evidence_chain(row)
            events = self._event_chain(row)
            return IncidentDetail(
                incident=_incident_view(row, dataset.slug if dataset else "unknown"),
                evidence=[_evidence_view(item) for item in evidence_rows],
                events=[_event_view(item) for item in events],
            )

    def acknowledge(self, incident_id: UUID) -> IncidentView:
        return self._change_status(incident_id, IncidentStatus.ACKNOWLEDGED)

    def start_investigation(self, incident_id: UUID) -> IncidentView:
        return self._change_status(incident_id, IncidentStatus.INVESTIGATING)

    def add_note(self, incident_id: UUID, data: NoteInput) -> IncidentEventView:
        with self.session.begin():
            row = self._incident(incident_id)
            event = self._event(
                row, "investigation_note", details={"note": data.note, "author": data.author}
            )
            self.session.flush()
            return _event_view(event)

    def set_severity(self, incident_id: UUID, data: SeverityInput) -> IncidentView:
        with self.session.begin():
            row = self._incident(incident_id, lock=True)
            old = row.severity
            row.severity = data.severity.value
            row.severity_decision = {
                "kind": "manual",
                "reason": data.reason,
                "changed_at": datetime.now(UTC).isoformat(),
            }
            row.updated_at = datetime.now(UTC)
            self._event(
                row,
                "severity_changed",
                previous=old,
                new=data.severity.value,
                details={"kind": "manual", "reason": data.reason},
            )
            return self._current_view(row)

    def resolve(self, incident_id: UUID, data: ResolveInput) -> IncidentView:
        with self.session.begin():
            row = self._incident(incident_id, lock=True)
            prior_resolutions = (
                self.session.scalar(
                    select(func.count())
                    .select_from(IncidentEventRow)
                    .where(
                        IncidentEventRow.incident_id == row.id,
                        IncidentEventRow.event_type == "resolved",
                    )
                )
                or 0
            )
            self._transition(
                row,
                IncidentStatus.RESOLVED,
                {
                    "summary": data.summary,
                    "reason": data.reason,
                    "resolution_number": int(prior_resolutions) + 1,
                },
            )
            row.resolved_at = datetime.now(UTC)
            return self._current_view(row)

    def reopen(self, incident_id: UUID, reason: str) -> IncidentView:
        if not reason.strip():
            raise ValueError("reopen reason is required")
        return self._change_status(incident_id, IncidentStatus.REOPENED, {"reason": reason})

    def _change_status(
        self, incident_id: UUID, target: IncidentStatus, details: dict[str, Any] | None = None
    ) -> IncidentView:
        with self.session.begin():
            row = self._incident(incident_id, lock=True)
            self._transition(row, target, details or {})
            return self._current_view(row)

    def link(self, incident_id: UUID, data: LinkInput) -> IncidentView:
        with self.session.begin():
            first = self._incident(incident_id, lock=True)
            other = self._incident(data.related_incident_id, lock=True)
            if first.id == other.id or first.dataset_id != other.dataset_id:
                raise ValueError("Incidents must be distinct and belong to the same dataset")
            self._event(
                first,
                "linked",
                details={"related_incident_id": str(other.id), "reason": data.reason},
            )
            self._event(
                other,
                "linked",
                details={"related_incident_id": str(first.id), "reason": data.reason},
            )
            return self._current_view(first)

    def merge(self, incident_id: UUID, data: MergeInput) -> IncidentView:
        with self.session.begin():
            retired = self._incident(incident_id, lock=True)
            survivor = self._incident(data.surviving_incident_id, lock=True)
            if retired.id == survivor.id or retired.dataset_id != survivor.dataset_id:
                raise ValueError("Incidents must be distinct and belong to the same dataset")
            if retired.merged_into_id is not None:
                raise ValueError("Incident has already been merged")
            if survivor.merged_into_id is not None:
                raise ValueError("Surviving incident cannot itself be retired")
            retired.merged_into_id = survivor.id
            if retired.status != IncidentStatus.RESOLVED.value:
                self._transition(
                    retired,
                    IncidentStatus.RESOLVED,
                    {"summary": "Incident merged", "reason": data.reason},
                )
            else:
                retired.resolved_at = retired.resolved_at or datetime.now(UTC)
            survivor.first_observed_at = min(retired.first_observed_at, survivor.first_observed_at)
            survivor.last_observed_at = max(retired.last_observed_at, survivor.last_observed_at)
            survivor.affected_columns = sorted(
                set(retired.affected_columns) | set(survivor.affected_columns)
            )
            retired_severity = IncidentSeverity(retired.severity)
            survivor_severity = IncidentSeverity(survivor.severity)
            if _SEVERITY_RANK[retired_severity] > _SEVERITY_RANK[survivor_severity]:
                old_severity = survivor.severity
                survivor.severity = retired.severity
                survivor.severity_decision = {
                    "kind": "merge",
                    "reason": data.reason,
                    "supporting_incident_id": str(retired.id),
                    "supporting_decision": retired.severity_decision,
                }
                self._event(
                    survivor,
                    "severity_changed",
                    previous=old_severity,
                    new=survivor.severity,
                    details=survivor.severity_decision,
                )
            survivor.updated_at = datetime.now(UTC)
            self._event(
                retired, "merged_into", new=str(survivor.id), details={"reason": data.reason}
            )
            self._event(
                survivor, "merged_from", new=str(retired.id), details={"reason": data.reason}
            )
            return self._current_view(survivor)

    def report(self, incident_id: UUID) -> InvestigationReport:
        detail = self.retrieve(incident_id)
        facts: list[ReportFact] = []
        stats: list[ReportFact] = []
        correlations: list[ReportFact] = []
        for evidence in detail.evidence:
            fact = ReportFact(
                category=evidence.finding_type,
                statement=(
                    f"{evidence.source_type.value} finding {evidence.finding_id} was associated "
                    f"with dataset version {evidence.dataset_version_id}"
                ),
                evidence_ids=[evidence.id],
                details=evidence.metadata,
            )
            facts.append(fact)
            if any(
                key in evidence.metadata
                for key in (
                    "psi",
                    "ks_statistic",
                    "wasserstein",
                    "violating_records",
                    "outlier_count",
                )
            ):
                stats.append(fact)
        for event in detail.events:
            if event.event_type in {"evidence_attached", "linked", "merged_from", "merged_into"}:
                correlations.append(
                    ReportFact(
                        category=event.event_type,
                        statement=(
                            f"Recorded {event.event_type} event at {event.occurred_at.isoformat()}"
                        ),
                        details=event.details,
                    )
                )
        resolution = next(
            (event.details for event in reversed(detail.events) if event.event_type == "resolved"),
            None,
        )
        missing = (
            []
            if detail.evidence
            else ["No source finding evidence is available for this incident."]
        )
        return InvestigationReport(
            incident=detail.incident,
            evidence=detail.evidence,
            timeline=detail.events,
            affected_versions=sorted(
                {item.dataset_version_id for item in detail.evidence}, key=str
            ),
            affected_columns=detail.incident.affected_columns,
            observed_facts=facts,
            statistical_evidence=stats,
            correlations=correlations,
            hypotheses=["Observed associations do not establish causation."],
            missing_evidence=missing,
            resolution=resolution,
        )

    def historical_summary(self, dataset_key: str | UUID) -> HistoricalSummary:
        with self.session.begin():
            dataset = self._dataset(dataset_key)
            rows = list(
                self.session.scalars(
                    select(IncidentRow)
                    .where(IncidentRow.dataset_id == dataset.id)
                    .order_by(IncidentRow.created_at, IncidentRow.id)
                )
            )
            statuses = Counter(row.status for row in rows)
            severities = Counter(row.severity for row in rows)
            columns: Counter[str] = Counter()
            categories: Counter[str] = Counter()
            category_versions: dict[str, set[UUID]] = {}
            reopened_ids: set[UUID] = set()
            escalations = 0
            for row in rows:
                columns.update(row.affected_columns)
                events = list(
                    self.session.scalars(
                        select(IncidentEventRow)
                        .where(IncidentEventRow.incident_id == row.id)
                        .order_by(IncidentEventRow.occurred_at, IncidentEventRow.id)
                    )
                )
                if any(
                    event.event_type == "status_changed" and event.new_value == "REOPENED"
                    for event in events
                ):
                    reopened_ids.add(row.id)
                escalations += sum(
                    event.event_type == "severity_changed"
                    and event.details.get("kind") == "automatic"
                    for event in events
                )
                for ev in self.session.scalars(
                    select(IncidentEvidenceRow).where(IncidentEvidenceRow.incident_id == row.id)
                ):
                    categories[ev.finding_type] += 1
                    category_versions.setdefault(ev.finding_type, set()).add(ev.dataset_version_id)
            return HistoricalSummary(
                dataset_id=dataset.id,
                incident_count=len(rows),
                by_status=dict(sorted(statuses.items())),
                by_severity=dict(sorted(severities.items())),
                affected_columns=dict(sorted(columns.items())),
                finding_categories=dict(sorted(categories.items())),
                reopened_incidents=len(reopened_ids),
                repeated_version_failures={
                    category: len(versions)
                    for category, versions in sorted(category_versions.items())
                    if len(versions) > 1
                },
                severity_escalations=escalations,
                incident_ids=[row.id for row in rows],
            )

    def _correlation_candidate(
        self,
        dataset_id: UUID,
        finding_type: str,
        fingerprint: str,
        columns: list[str],
        observed_at: datetime,
        policy: IncidentPolicyConfig,
    ) -> tuple[IncidentRow | None, str]:
        if not columns:
            return None, "finding has no affected columns; conservative correlation declined"
        cutoff = observed_at - timedelta(hours=policy.correlation_window_hours)
        candidates = list(
            self.session.scalars(
                select(IncidentRow)
                .where(
                    IncidentRow.dataset_id == dataset_id,
                    IncidentRow.finding_category == finding_type,
                    IncidentRow.merged_into_id.is_(None),
                    IncidentRow.status != IncidentStatus.RESOLVED.value,
                    IncidentRow.last_observed_at >= cutoff,
                )
                .with_for_update()
            )
        )
        target_columns = {column.casefold() for column in columns}
        matches = [
            (candidate, _column_overlap(target_columns, candidate.affected_columns))
            for candidate in candidates
            if _column_overlap(target_columns, candidate.affected_columns)
            >= policy.correlation_minimum_column_overlap
        ]
        if len(matches) == 1:
            candidate, overlap = matches[0]
            return candidate, f"unique same-category match; column Jaccard overlap={overlap:.6f}"
        if len(matches) > 1:
            return (
                None,
                f"ambiguous match across {len(matches)} incidents; created separate incident",
            )
        # A previously resolved exact match is a recurrence and is reopened in place.
        resolved = list(
            self.session.scalars(
                select(IncidentRow)
                .where(
                    IncidentRow.dataset_id == dataset_id,
                    IncidentRow.finding_category == finding_type,
                    IncidentRow.merged_into_id.is_(None),
                    IncidentRow.status == IncidentStatus.RESOLVED.value,
                )
                .order_by(IncidentRow.last_observed_at.desc())
                .limit(2)
                .with_for_update()
            )
        )
        resolved = [
            candidate
            for candidate in resolved
            if _column_overlap(target_columns, candidate.affected_columns)
            >= policy.correlation_minimum_column_overlap
        ]
        if len(resolved) == 1 and columns:
            overlap = _column_overlap(target_columns, resolved[0].affected_columns)
            return (
                resolved[0],
                f"unique same-category resolved match; column Jaccard overlap={overlap:.6f}",
            )
        return None, "no unambiguous recent match"

    def _new_incident(
        self,
        dataset: DatasetRow,
        finding_type: str,
        columns: list[str],
        fingerprint: str,
        source_severity: str,
        metadata: dict[str, Any],
        observed_at: datetime,
        policy: IncidentPolicyConfig,
    ) -> IncidentRow:
        incident_id = uuid4()
        decision = _severity_decision(
            source_severity,
            {
                **metadata,
                "affected_column_count": len(columns),
                "recurrence_count": 1,
            },
            policy,
        )
        row = IncidentRow(
            id=incident_id,
            reference=f"INC-{incident_id.hex[:12].upper()}",
            dataset_id=dataset.id,
            title=f"{finding_type.replace('_', ' ').title()} detected",
            description=f"{finding_type} finding detected for dataset {dataset.slug}.",
            finding_category=finding_type,
            status=IncidentStatus.OPEN.value,
            severity=decision["severity"],
            severity_decision=decision,
            affected_columns=columns,
            first_observed_at=observed_at,
            last_observed_at=observed_at,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            correlation_fingerprint=fingerprint,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def _reassess_severity(
        self,
        row: IncidentRow,
        policy: IncidentPolicyConfig,
        source_severity: str,
        metadata: dict[str, Any],
    ) -> None:
        evidence_count = (
            self.session.scalar(
                select(func.count(IncidentEvidenceRow.id)).where(
                    IncidentEvidenceRow.incident_id == row.id
                )
            )
            or 0
        )
        severity_metadata = {
            **metadata,
            "affected_column_count": len(row.affected_columns),
            "recurrence_count": int(evidence_count),
        }
        decision = _severity_decision(source_severity, severity_metadata, policy)
        proposed = IncidentSeverity(decision["severity"])
        existing = IncidentSeverity(row.severity)
        # Evidence may escalate severity; absence or milder findings never auto-downgrade it.
        if _SEVERITY_RANK[proposed] < _SEVERITY_RANK[existing]:
            decision["reason"] += "; retained higher existing severity (no automatic downgrade)"
            return
        if proposed != existing:
            old = row.severity
            row.severity = proposed.value
            row.severity_decision = decision
            row.updated_at = datetime.now(UTC)
            self._event(
                row,
                "severity_changed",
                previous=old,
                new=proposed.value,
                details={**decision, "kind": "automatic"},
            )
        else:
            row.severity_decision = decision

    def _transition(
        self, row: IncidentRow, target: IncidentStatus, details: dict[str, Any]
    ) -> None:
        current = IncidentStatus(row.status)
        if target not in _TRANSITIONS[current]:
            raise ValueError(f"Invalid incident transition: {current.value} -> {target.value}")
        row.status = target.value
        row.updated_at = datetime.now(UTC)
        if target == IncidentStatus.RESOLVED:
            row.resolved_at = row.updated_at
        self._event(
            row, "status_changed", previous=current.value, new=target.value, details=details
        )
        if target == IncidentStatus.RESOLVED:
            self._event(row, "resolved", details=details)
        elif target == IncidentStatus.REOPENED:
            self._event(row, "reopened", details=details)

    def _event(
        self,
        row: IncidentRow,
        event_type: str,
        *,
        previous: Any = None,
        new: Any = None,
        details: dict[str, Any] | None = None,
    ) -> IncidentEventRow:
        event = IncidentEventRow(
            incident_id=row.id,
            event_type=event_type,
            occurred_at=datetime.now(UTC),
            previous_value=previous,
            new_value=new,
            details=details or {},
        )
        self.session.add(event)
        return event

    def _incident(self, incident_id: UUID, *, lock: bool = False) -> IncidentRow:
        query = select(IncidentRow).where(IncidentRow.id == incident_id)
        if lock:
            query = query.with_for_update()
        row = self.session.scalar(query)
        if row is None:
            raise ValueError(f"Incident not found: {incident_id}")
        return row

    def _dataset(self, dataset_key: str | UUID) -> DatasetRow:
        if isinstance(dataset_key, UUID):
            row = self.session.scalar(select(DatasetRow).where(DatasetRow.id == dataset_key))
        else:
            row = self.session.scalar(select(DatasetRow).where(DatasetRow.slug == dataset_key))
        if row is None:
            raise ValueError(f"Dataset not found: {dataset_key}")
        return row

    def _advisory_lock(self, dataset_id: UUID) -> None:
        # The row lock serializes service calls; advisory locking also protects independent writers.
        key = int.from_bytes(dataset_id.bytes[:8], "big", signed=True)
        self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})

    def _current_view(self, row: IncidentRow) -> IncidentView:
        dataset = self.session.get(DatasetRow, row.dataset_id)
        return _incident_view(row, dataset.slug if dataset else "unknown")

    def _evidence_chain(self, row: IncidentRow) -> list[IncidentEvidenceRow]:
        incident_ids = self._merged_descendants(row.id)
        return list(
            self.session.scalars(
                select(IncidentEvidenceRow)
                .where(IncidentEvidenceRow.incident_id.in_(incident_ids))
                .order_by(IncidentEvidenceRow.associated_at, IncidentEvidenceRow.id)
            )
        )

    def _event_chain(self, row: IncidentRow) -> list[IncidentEventRow]:
        incident_ids = self._merged_descendants(row.id)
        return list(
            self.session.scalars(
                select(IncidentEventRow)
                .where(IncidentEventRow.incident_id.in_(incident_ids))
                .order_by(IncidentEventRow.occurred_at, IncidentEventRow.id)
            )
        )

    def _merged_descendants(self, incident_id: UUID) -> list[UUID]:
        result = [incident_id]
        cursor = 0
        while cursor < len(result):
            children = list(
                self.session.scalars(
                    select(IncidentRow.id).where(IncidentRow.merged_into_id == result[cursor])
                )
            )
            result.extend(children)
            cursor += 1
        return result


def _fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _column_overlap(target: set[str], existing: list[str]) -> float:
    current = {column.casefold() for column in existing}
    if not target or not current:
        return 0.0
    return len(target & current) / len(target | current)


def _qualification(
    policy: IncidentPolicyConfig, source: SourceType, finding_type: str, status: str, severity: str
) -> tuple[bool, str]:
    if source == SourceType.QUALITY and status != "FAIL":
        return False, f"quality finding status {status} is not FAIL"
    if source == SourceType.RELIABILITY and status != "DETECTED":
        return False, f"reliability finding status {status} is not DETECTED"
    if policy.finding_types and finding_type not in policy.finding_types:
        return False, f"finding type {finding_type} is not eligible"
    eligible = policy.minimum_source_severities.get(source.value, [])
    rank = {
        "INFO": 0,
        "INFORMATIONAL": 0,
        "WARNING": 1,
        "LOW": 1,
        "MEDIUM": 2,
        "ERROR": 3,
        "HIGH": 3,
        "CRITICAL": 4,
    }
    if rank.get(severity.upper(), -1) < min(
        (rank.get(item.upper(), 5) for item in eligible), default=5
    ):
        return False, f"source severity {severity} is below configured eligibility threshold"
    return True, f"{source.value} finding has qualifying status and severity"


def _finding_metadata(source: SourceType, finding: Any) -> tuple[list[str], dict[str, Any]]:
    if source == SourceType.QUALITY:
        snapshot = finding.rule_snapshot or {}
        params = snapshot.get("parameters", {})
        columns = [str(params["column_name"])] if params.get("column_name") else []
        metadata = dict(finding.evidence or {})
        metadata.update(
            {
                "message": finding.message,
                "status": finding.status,
                "severity": finding.severity,
                "evaluated_records": finding.evaluated_records,
                "violating_records": finding.violating_records,
                "violation_percentage": finding.violation_percentage,
            }
        )
    else:
        columns = [finding.column_name] if finding.column_name else []
        extra_columns = (finding.evidence or {}).get("affected_columns", [])
        columns.extend(str(item) for item in extra_columns if item)
        metadata = dict(finding.evidence or {})
        metadata.update(
            {
                "message": finding.message,
                "detector": finding.detector,
                "baseline_value": finding.baseline_value,
                "target_value": finding.target_value,
            }
        )
    return sorted(set(columns)), metadata


def _severity_decision(
    source_severity: str, metadata: dict[str, Any], policy: IncidentPolicyConfig
) -> dict[str, Any]:
    source_rank = {
        "INFO": "INFORMATIONAL",
        "WARNING": "LOW",
        "ERROR": "MEDIUM",
        "CRITICAL": "CRITICAL",
    }
    severity = source_rank.get(source_severity.upper(), "LOW")
    records = metadata.get(
        "violating_records", metadata.get("affected_records", metadata.get("outlier_count", 0))
    )
    magnitude_candidates = [
        metadata.get(name) for name in ("psi", "js_divergence", "drift_magnitude")
    ]
    magnitude = max(
        (float(value) for value in magnitude_candidates if isinstance(value, (int, float))),
        default=0.0,
    )
    column_count = int(metadata.get("affected_column_count", 0))
    recurrence_count = int(metadata.get("recurrence_count", 1))
    if (
        isinstance(records, (int, float))
        and records >= policy.critical_affected_records
        or magnitude >= policy.critical_drift_magnitude
        or column_count >= policy.critical_affected_columns
    ):
        severity = "CRITICAL"
    elif (
        isinstance(records, (int, float))
        and records >= policy.high_affected_records
        or magnitude >= policy.high_drift_magnitude
        or column_count >= policy.high_affected_columns
        or recurrence_count >= policy.recurrence_high_count
    ):
        severity = "HIGH"
    elif metadata.get("status") == "FAIL" and severity == "LOW":
        severity = "MEDIUM"
    return {
        "kind": "automatic",
        "severity": severity,
        "reason": (
            "classified from source severity, observed counts, affected columns, "
            "drift magnitude, and recurrence"
        ),
        "supporting_evidence": {
            "source_severity": source_severity,
            "affected_records": records,
            "drift_magnitude": magnitude,
            "affected_column_count": column_count,
            "recurrence_count": recurrence_count,
        },
    }


def _policy_view(row: IncidentPolicyRow) -> IncidentPolicyView:
    return IncidentPolicyView(
        id=row.id,
        dataset_id=row.dataset_id,
        name=row.name,
        config=IncidentPolicyConfig.model_validate(row.config),
        fingerprint=row.fingerprint,
        enabled=row.enabled,
    )


def _incident_view(row: IncidentRow, dataset_slug: str) -> IncidentView:
    return IncidentView(
        id=row.id,
        reference=row.reference,
        dataset_id=row.dataset_id,
        dataset_slug=dataset_slug,
        title=row.title,
        description=row.description,
        finding_category=row.finding_category,
        status=IncidentStatus(row.status),
        severity=IncidentSeverity(row.severity),
        affected_columns=row.affected_columns,
        first_observed_at=row.first_observed_at,
        last_observed_at=row.last_observed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        resolved_at=row.resolved_at,
        correlation_fingerprint=row.correlation_fingerprint,
        merged_into_id=row.merged_into_id,
    )


def _evidence_view(row: IncidentEvidenceRow) -> IncidentEvidenceView:
    return IncidentEvidenceView(
        id=row.id,
        incident_id=row.incident_id,
        source_type=SourceType(row.source_type),
        assessment_id=row.assessment_id,
        finding_id=row.finding_id,
        dataset_version_id=row.dataset_version_id,
        finding_type=row.finding_type,
        source_severity=row.source_severity,
        affected_columns=row.affected_columns,
        metadata=row.evidence_metadata,
        associated_at=row.associated_at,
    )


def _event_view(row: IncidentEventRow) -> IncidentEventView:
    return IncidentEventView(
        id=row.id,
        incident_id=row.incident_id,
        event_type=row.event_type,
        occurred_at=row.occurred_at,
        previous_value=row.previous_value,
        new_value=row.new_value,
        details=row.details,
    )
