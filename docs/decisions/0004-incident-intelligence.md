# ADR 0004: Incident Intelligence

- **Status:** Accepted
- **Date:** 2026-11-13

## Context

Sentinel can ingest and version datasets, evaluate configurable quality rules, detect reliability problems, compare historical versions, and generate deterministic investigations.

However, assessments and findings are analytical records. They do not provide an operational representation of a continuing problem.

A single underlying problem may produce multiple findings across multiple assessments and dataset versions. Treating each finding as an independent alert would create duplication and make historical investigation difficult.

Sentinel therefore needs a distinct incident domain.

## Decisions

### 1. Incidents are separate from assessments

An assessment represents one evaluation of a particular dataset version.

An incident represents a potentially continuing operational problem.

An incident may reference findings from multiple assessments and versions. Existing assessment records remain immutable.

### 2. Evidence and event history are append-only

Incident evidence records the analytical findings supporting an incident.

Incident events record changes to the incident over time.

Neither should be silently deleted or rewritten as the incident changes status, severity, or correlation.

### 3. Correlation is deterministic and conservative

Correlation uses explicit signals such as dataset identity, affected columns, finding categories, and temporal proximity.

A stable correlation identity helps identify repeated observations.

Ambiguous relationships must not result in automatic merging. Explicit linking or merging must preserve original identifiers, evidence, and event histories.

### 4. Severity is separate from status

Severity expresses operational importance.

Status expresses lifecycle state.

Both have independent transition and persistence semantics.

Severity decisions must retain their supporting evidence and policy context.

### 5. Incident lifecycle is validated

Incident status changes must go through explicit service operations.

Each transition creates an immutable event. Arbitrary status updates are not permitted.

### 6. Investigation reports are deterministic

Reports are generated from persisted assessments, findings, evidence, and event history.

They distinguish observations, statistical evidence, correlations, and hypotheses.

No LLM is required to produce or validate a report.

### 7. No new infrastructure dependencies

Phase 4 uses the existing PostgreSQL, SQLAlchemy, Pydantic, Alembic and testing infrastructure.

Distributed processing, APIs, dashboards and external integrations are deferred.

### 8. Preserve original records during incident merging

Incident merging must be auditable.

The surviving incident retains its identity. The retired incident retains its identity and historical records, with an explicit reference to the surviving incident.

No evidence or event history may be discarded as a side effect of merging.

## Consequences

**Benefits**
- Analytical findings become operationally manageable.
- Repeated findings can be correlated rather than producing duplicate incidents.
- Incident history is auditable.
- Severity and lifecycle semantics are explicit.
- Reports can be reproduced from persisted evidence.
- Later APIs, dashboards and AI investigation can build on a stable incident domain.

**Costs and constraints**
- Correlation requires careful deterministic rules.
- Incident lifecycle and severity need explicit validation.
- Evidence and event histories increase persistence requirements.
- Concurrency must be handled at the database and service levels.
- Incident merging requires preservation of historical identities.

## Deferred decisions

The following are outside this ADR:

- API design and authentication.
- Background scheduling and distributed processing.
- Notification delivery.
- Dashboard design.
- LLM-assisted investigation.
- External ticketing and incident-management integrations.
