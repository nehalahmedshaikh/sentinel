# ADR 0003: Data Reliability Intelligence

- **Status:** Accepted
- **Date:** 2026-11-12

## Context

Sentinel currently supports dataset ingestion, immutable versioning, profiling, and configurable data-quality assessments.

The next capability is to understand how datasets change across versions. This requires schema comparison, statistical drift detection, anomaly detection, historical baselines, and a mechanism for combining their evidence.

These capabilities must be reproducible, interpretable, and integrated with the existing domain model.

## Decisions

### 1. Multiple explicit baseline strategies

Support four strategies:

- Previous version.
- Initial version.
- Rolling historical window.
- Explicit version from the same dataset.

The resolved baseline version or versions must be stored with every assessment.

A baseline is part of the assessment's identity. It must not be inferred later from the dataset's current state.

### 2. Separate detection policy from assessment

A detection policy defines which detectors run, their parameters, and their thresholds.

An assessment records an immutable snapshot of the policy used.

A deterministic policy fingerprint supports reproducibility and idempotency.

Changing a policy must not alter previously completed assessments.

### 3. Separate statistical and operational significance

Statistical tests and distance metrics describe differences between distributions. They do not independently determine operational importance.

Findings must retain the underlying metric values and statistical results while applying separately configured practical thresholds and severity rules.

### 4. Use established scientific libraries selectively

Use:

- SciPy for statistical tests and distribution metrics.
- scikit-learn for Isolation Forest and Local Outlier Factor.
- Polars for dataset loading, transformations, and data processing.

Do not introduce a full external data-monitoring framework. Sentinel should own its domain model, policy semantics, persistence, and investigation logic.

### 5. Preserve detector independence

Each detector should have a clear responsibility and produce structured findings.

A detector failure must be recorded without discarding valid results from other detectors. Assessment status must represent partial completion where appropriate.

### 6. Deterministic investigation

Investigation combines persisted evidence from drift, anomaly, schema, and existing quality assessments.

It may identify temporal and statistical relationships but must distinguish correlation from causation.

LLM-generated explanations and autonomous actions are deferred.

### 7. Persist immutable assessment evidence

Persist the target version, resolved baseline, policy snapshot, detector configuration, findings, and relevant evidence.

Completed assessments are immutable. Repeated requests with the same target, resolved baseline, and policy should be idempotent.

Use database constraints and transaction-safe service logic to protect against concurrent duplicate assessments.

### 8. Keep the initial scope within one dataset

Phase 3 compares versions of the same dataset. Cross-dataset comparisons are deferred until the dataset identity and compatibility requirements are better established.

## Consequences

**Benefits**
- Historical comparisons remain reproducible.
- Different baseline strategies answer different operational questions.
- Statistical differences are not automatically confused with operational incidents.
- Findings can be combined into a deterministic investigation.
- The architecture can later support incident management and API exposure without rewriting the detection engine.

**Costs and constraints**
- Multiple detectors and baseline strategies increase testing requirements.
- Rolling baselines require careful handling of multiple historical versions.
- Statistical methods need explicit treatment of small samples, missing values, and unsuitable data.
- Persisted evidence and policy snapshots increase storage requirements.

## Deferred decisions

The following remain outside this ADR's scope:

- Incident lifecycle and alerting.
- Distributed execution and background scheduling.
- API and dashboard design.
- LLM provider and investigation-agent architecture.
- Cross-dataset similarity and reference-dataset management.
