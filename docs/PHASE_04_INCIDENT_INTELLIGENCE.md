# Phase 4: Incident Intelligence

## Objective

Transform Sentinel's existing data-quality and reliability findings into persistent, manageable incidents.

An incident represents an operational problem that may span multiple assessments, findings, and dataset versions. It must have a lifecycle, severity, evidence history, and reproducible investigation report.

This phase builds the incident-management core of Sentinel. It must reuse the existing Phase 1 dataset foundation, Phase 2 quality assessments, and Phase 3 reliability assessments.

## 1. Core domain concepts

### 1.1 Incident

An incident represents a distinct reliability problem requiring attention.

An incident should contain:

- Stable incident identifier.
- Human-readable incident reference.
- Dataset identity.
- Title and description.
- Current status.
- Current severity.
- Primary affected columns, where applicable.
- First observed timestamp.
- Last observed timestamp.
- Created and updated timestamps.
- Resolution timestamp, when resolved.
- Current correlation identity.
- Evidence and event history.

An incident may be associated with multiple findings across multiple assessments and dataset versions.

An incident is not an assessment. Assessments remain immutable records of individual evaluations.

### 1.2 Incident evidence

Evidence links an incident to its originating analytical results.

Supported evidence sources:

- Phase 2 quality assessments and findings.
- Phase 3 reliability assessments and findings.
- Schema drift findings.
- Distribution drift findings.
- Anomaly findings.
- Deterministic investigation results.

Preserve source identifiers and enough metadata to interpret the evidence.

Do not copy entire datasets or unnecessarily duplicate sensitive records.

Evidence should be append-only. Existing evidence must not silently disappear when an incident is updated, correlated, or resolved.

### 1.3 Incident events

Every meaningful incident transition must produce a persistent event.

Examples:

- Incident created.
- Evidence attached.
- Severity changed.
- Status changed.
- Incident acknowledged.
- Investigation note added.
- Incident resolved.
- Incident reopened.
- Incident merged or linked to another incident, if supported.

Events must record:

- Event identifier.
- Incident identifier.
- Event type.
- Timestamp.
- Previous value, where applicable.
- New value, where applicable.
- Structured event details.

The event history must be append-only and provide an auditable incident timeline.

## 2. Incident generation

Implement a service that processes completed quality and reliability assessments and determines whether their findings warrant incidents.

Requirements:

- Support explicit incident-generation policies.
- Allow qualifying finding types and severity thresholds to be configured.
- Avoid creating incidents for every informational or insignificant finding.
- Preserve the originating assessment and finding references.
- Make processing idempotent.
- Prevent duplicate incident creation under concurrent processing.
- Support processing assessments that contain partial detector errors without treating errors as successful detections.
- Record why a finding did or did not qualify for incident creation where appropriate.

Incident generation must not modify the original assessments.

## 3. Correlation engine

Related findings should be grouped into a coherent incident where sufficient evidence supports the relationship.

Implement deterministic correlation using signals such as:

- Same dataset identity.
- Same or overlapping affected columns.
- Compatible finding categories.
- Related quality and reliability symptoms.
- Temporal proximity.
- Existing incident identity and history.

Correlation must be configurable and deterministic.

Requirements:

- Repeated findings belonging to an existing unresolved incident should update that incident with new evidence.
- New findings that match an existing incident should not create duplicate incidents.
- Findings from different datasets must not be merged merely because their column names or metric values match.
- Correlation decisions must be recorded and explainable.
- Weak or ambiguous matches must not be merged automatically.
- Preserve the distinction between a confirmed match and a possible relationship.

Implement a stable correlation identity or fingerprint. Its construction must be documented and tested.

Do not attempt unrestricted causal inference.

### Incident merging and linking

If two incidents are later found to be related, support an explicit, auditable way to link them or merge them where appropriate.

A merge must preserve:

- Original incident identifiers.
- All evidence.
- Both incident histories.
- The reason for the merge.
- The surviving incident identity.
- A reference from the retired incident to the surviving incident.

Do not silently delete or overwrite an incident.

## 4. Severity management

Implement a configurable severity policy.

Provide a sensible default severity model:

- CRITICAL
- HIGH
- MEDIUM
- LOW
- INFORMATIONAL

Severity should consider relevant evidence, including:

- Detector severity.
- Number and proportion of affected records.
- Number of affected columns.
- Quality-assessment outcome.
- Magnitude of drift.
- Recurrence across successive versions.
- Whether the finding affects an important dataset or column, where such importance is explicitly configured.

Do not invent business criticality. Where no importance metadata exists, rely on observable evidence and configured defaults.

Requirements:

- Store the reason and evidence behind severity assignment.
- Preserve severity history.
- Support explicit manual severity changes.
- Record both automatic and manual changes in the event history.
- Re-evaluate severity when new evidence arrives, subject to the configured policy.
- Do not silently downgrade an incident simply because a later assessment does not reproduce the finding.

Severity and incident status must remain separate concepts.

## 5. Incident lifecycle

Implement these statuses:

- OPEN
- ACKNOWLEDGED
- INVESTIGATING
- RESOLVED
- REOPENED

Define and validate permitted transitions.

Expected behaviour:

- New incidents begin as OPEN.
- OPEN incidents can be acknowledged or investigated.
- ACKNOWLEDGED incidents can enter investigation or be resolved.
- INVESTIGATING incidents can be resolved.
- RESOLVED incidents can be reopened when new qualifying evidence indicates recurrence or persistence.
- REOPENED incidents can be acknowledged, investigated, or resolved again.

A transition must:

- Validate the current status.
- Record the previous and new statuses.
- Record the transition timestamp.
- Append an incident event.
- Preserve historical resolution timestamps and reopening information.

Do not allow arbitrary status assignment that bypasses lifecycle validation.

Support an optional resolution summary and resolution reason.

## 6. Incident management service

Implement a coherent service interface for:

- Generating incidents from quality assessments.
- Generating incidents from reliability assessments.
- Processing a completed assessment without duplicating previously processed findings.
- Listing incidents with filters.
- Retrieving an incident and its evidence.
- Retrieving incident timelines.
- Acknowledging incidents.
- Starting or updating investigations.
- Adding investigation notes.
- Changing severity.
- Resolving incidents.
- Reopening incidents.
- Linking or merging incidents.
- Generating investigation reports.
- Retrieving recurring-incident history.

Use Pydantic input/output contracts.

Keep domain logic separate from database persistence. Follow existing Sentinel service conventions.

## 7. Investigation reports

Generate deterministic reports from persisted incident evidence and event history.

A report should include:

- Incident reference.
- Dataset identity.
- Current status and severity.
- First and most recent observation.
- Affected dataset versions.
- Affected columns.
- Evidence summary.
- Quality findings.
- Reliability findings.
- Relevant drift and anomaly measurements.
- Incident timeline.
- Correlation decisions.
- Resolution information, if resolved.
- Observed facts.
- Relationships and possible explanations, clearly labelled as such.

Reports must distinguish:

- Directly observed facts.
- Statistical evidence.
- Correlations.
- Hypotheses or possible explanations.
- Unavailable or incomplete evidence.

Never present correlation as established causation.

Reports must be reproducible from stored records. Do not rely on transient in-memory state or external LLM output.

Support a structured report representation suitable for later API exposure and export.

## 8. Recurrence and historical intelligence

Provide ways to identify:

- Repeated incidents involving the same dataset.
- Repeatedly affected columns.
- Recurring finding categories.
- Incidents that reappear after resolution.
- Dataset versions associated with repeated failures.
- Patterns of repeated severity escalation.

Distinguish a reopened incident from a newly created incident.

Historical retrieval must preserve original incident and event identities.

## 9. Persistence

Add SQLAlchemy models and an Alembic migration.

Expected persistent entities include:

- Incident.
- IncidentEvidence.
- IncidentEvent.
- Incident policy/configuration, if persisted separately.
- Any necessary association or merge/link records.

The precise model decomposition may follow existing project conventions, but evidence and event history must remain independently queryable.

Requirements:

- Database-level uniqueness constraints where appropriate.
- Transaction-safe incident generation and correlation.
- Protection against concurrent duplicate processing.
- Immutable event history.
- Append-only evidence history.
- Foreign-key integrity.
- Efficient retrieval by dataset, incident status, severity, and time.

Never rewrite existing Phase 1, Phase 2, or Phase 3 migrations.

## 10. Testing requirements

Add unit and PostgreSQL integration tests.

At minimum, test:

### Generation
- Qualifying findings create incidents.
- Non-qualifying findings do not create incidents.
- Repeated assessment processing is idempotent.
- Concurrent processing does not create duplicate incidents.
- Partial assessment errors are handled correctly.

### Correlation
- Repeated findings update the appropriate unresolved incident.
- Related findings from multiple assessments are correlated.
- Different datasets are not incorrectly merged.
- Ambiguous matches are not automatically merged.
- Correlation fingerprints are deterministic.
- Explicit incident merging preserves evidence and history.

### Severity
- Default severity classification.
- Configurable thresholds.
- Severity escalation.
- Manual severity changes.
- No automatic downgrade without supporting evidence.
- Severity changes are recorded.

### Lifecycle
- Valid transitions.
- Invalid transitions.
- Acknowledgement.
- Investigation.
- Resolution.
- Reopening.
- Resolution history preservation.

### Evidence and reports
- Evidence is append-only.
- Evidence references remain valid.
- Timeline ordering.
- Deterministic report generation.
- Incomplete evidence is represented accurately.
- Reports distinguish observations from hypotheses.

### Historical intelligence
- Recurring incidents.
- Repeated finding categories.
- Reopened versus newly created incidents.
- Historical retrieval and filtering.

### Persistence
- Migration behaviour.
- PostgreSQL foreign-key integrity.
- Transaction rollback.
- Concurrency safeguards.
- Existing Phase 1–3 regression tests.

Use deterministic synthetic findings and assessments wherever possible.

## 11. Non-goals

Do not implement:

- FastAPI or REST endpoints.
- Background workers or scheduled execution.
- Redis, Celery, or message brokers.
- Notifications, email or messaging integrations.
- Dashboard or frontend.
- LLM-generated incident analysis.
- Autonomous remediation.
- External ticketing integrations.
- Multi-tenant access control.

These belong to Phase 5 or later.

## 12. Acceptance criteria

Phase 4 is complete when:

1. Qualifying quality and reliability findings generate persistent incidents.
2. Repeated findings are correlated with existing incidents where justified.
3. Correlation is deterministic, explainable and protected against duplicates.
4. Severity is configurable and evidence-backed.
5. Incident lifecycle transitions are validated and auditable.
6. Evidence and events are preserved as append-only history.
7. Incidents can be linked or merged without losing their histories.
8. Deterministic investigation reports can be generated from persisted evidence.
9. Recurrence and historical incident patterns can be queried.
10. PostgreSQL migrations and integration tests pass against the dedicated test database.
11. All existing Phase 1–3 functionality remains intact.
12. Ruff, formatting, mypy, tests and lockfile checks pass.
13. README and Phase 4 documentation explain the complete incident lifecycle and service usage.

The result should be a functional incident-intelligence engine, not simply a database table containing alerts.
