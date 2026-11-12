# Phase 3: Data Reliability Intelligence

## Objective

Extend Sentinel beyond individual data-quality assessments. Sentinel must be able to compare dataset versions, detect meaningful changes in their structure and distributions, identify anomalous observations, and assemble evidence that helps explain when and where reliability deteriorated.

This phase delivers an integrated, reproducible intelligence layer. It must build on the existing dataset, versioning, profiling, and quality-assessment foundations rather than duplicate them.

## 1. Scope

### 1.1 Schema drift

Compare the schema of a target dataset version against its selected baseline.

Detect and report:

- Added columns.
- Removed columns.
- Data-type changes.
- Nullability changes, where reliably inferable.
- Other relevant schema differences supported by the existing schema representation.

Each finding must identify the affected column, baseline value, target value, and change classification.

A schema change is not automatically a failure. Its severity must depend on the configured detection policy and its implications.

### 1.2 Distribution drift

Compare corresponding columns across the target and baseline versions.

Support appropriate metrics for different column types:

**Numeric columns**
- Kolmogorov–Smirnov (KS) statistic and p-value.
- Population Stability Index (PSI).
- Wasserstein distance.

**Categorical columns**
- Jensen–Shannon divergence.
- PSI.

Requirements:

- Select metrics according to column type and data suitability.
- Handle nulls, empty columns, constant distributions, and insufficient samples explicitly.
- Record sample sizes and relevant metric values.
- Do not silently treat an unavailable or invalid comparison as zero drift.
- Make metric thresholds configurable through a detection policy.
- Keep statistical significance separate from practical significance. A statistically significant difference must not automatically become a high-severity finding.
- Record both the statistical result and the practical threshold used to classify it.

Metric implementation must be mathematically sound and tested against known cases. Document metric conventions, including PSI binning and divergence normalisation.

### 1.3 Anomaly detection

Implement two complementary levels of detection.

**Univariate detection**
- Numeric outliers using robust statistical methods, such as IQR or modified Z-score.
- Rare or unexpectedly frequent categorical values where meaningful.
- Configurable thresholds.
- Evidence including affected column, anomaly count, rate, and relevant statistical boundaries.

**Multivariate detection**
- Isolation Forest.
- Local Outlier Factor (LOF).

Multivariate detection must be configurable rather than blindly executed on every dataset. It must handle unsuitable data, high dimensionality, small samples, and non-numeric columns safely. Preprocessing decisions must be explicit and reproducible.

For each detector, record its configuration and the number and proportion of anomalous observations.

Do not persist entire anomalous records by default. Store bounded evidence such as row indices or stable row references and aggregate statistics. Avoid unnecessarily copying potentially sensitive field values into findings.

### 1.4 Historical intelligence

Support multiple baseline strategies:

- **Previous version:** compare the target with its immediately preceding version.
- **Initial version:** compare the target with the dataset's first version.
- **Rolling baseline:** compare the target with a bounded window of preceding versions.
- **Explicit version:** compare against a user-selected version belonging to the same dataset.

The baseline must be resolved explicitly and stored with the resulting assessment. A result must remain interpretable even if newer versions are ingested later.

For rolling baselines, document and implement a consistent method of combining the selected historical versions. Do not silently substitute another strategy when a requested baseline cannot be constructed.

Provide historical retrieval that makes it possible to:

- Inspect reliability assessments across versions.
- Track drift and anomaly metrics over time.
- Identify the earliest observed version containing a particular change.
- Distinguish sudden changes from changes that accumulate over successive versions.

The initial version, which has no preceding version, must be handled explicitly rather than treated as a failed comparison.

### 1.5 Deterministic investigation

Introduce an investigation layer that combines available evidence from:

- Schema drift.
- Distribution drift.
- Anomaly detection.
- Existing data-quality assessments.

It should identify:

- Which columns are affected.
- Which changes occurred together.
- Whether quality deterioration coincides with schema or distribution changes.
- The first version in which a change was observed, where historical evidence permits.
- The baseline and target versions involved.
- The statistical evidence supporting each conclusion.

Investigation output must distinguish observed facts from correlations and possible explanations. It must not claim causation merely because two changes occurred together.

This phase uses deterministic analysis only. Do not add an LLM or generate unsupported natural-language explanations.

## 2. Architecture and persistence

Implement the intelligence layer using the existing project conventions and domain boundaries.

Expected capabilities include:

- Validated detection-policy configuration.
- Baseline resolution.
- Schema comparison.
- Distribution comparison.
- Anomaly detection.
- Persistent reliability assessments and findings.
- Historical retrieval.
- Deterministic investigation summaries.

Persist enough information to reproduce and interpret each assessment:

- Dataset identity.
- Target version.
- Resolved baseline version or versions.
- Baseline strategy.
- Detection-policy snapshot and deterministic fingerprint.
- Detector and metric configuration.
- Assessment status and timestamps.
- Findings, evidence, thresholds, and severity.
- Any errors encountered by individual detectors.

Assessments must be immutable once completed. Re-running the same target, resolved baseline, and policy must behave idempotently. Concurrent requests must not create duplicate assessments.

Use an Alembic migration for schema changes. Do not modify or rewrite existing Phase 1 or Phase 2 migrations.

Reuse existing quality-assessment records and services. Do not create a second data-quality engine.

## 3. Reliability semantics

Keep these concepts distinct:

- **Detection result:** what a detector observed.
- **Statistical significance:** whether the observed difference is unlikely under the relevant statistical null hypothesis.
- **Practical significance:** whether the magnitude of the difference crosses a configured operational threshold.
- **Severity:** the operational importance assigned to a finding.
- **Assessment status:** whether the analysis completed successfully, partially, or unsuccessfully.

A detector error must not silently become a PASS or a zero-valued metric. Where one detector fails, preserve valid results from other detectors and record the error.

A lack of sufficient data must be represented explicitly.

## 4. Public service interface

Provide a coherent service interface for:

- Creating, retrieving, updating, and disabling detection policies.
- Running a reliability assessment for a dataset version.
- Selecting the baseline strategy and any required baseline version.
- Retrieving an assessment and its findings.
- Retrieving historical assessments and trends.
- Retrieving deterministic investigation results.

Use Pydantic models for validated inputs and outputs, consistent with the existing project.

Avoid exposing persistence models directly as the public service interface.

## 5. Testing requirements

Add unit and PostgreSQL integration tests.

At minimum, test:

- Added, removed, and modified columns.
- Numeric and categorical drift under known distribution changes.
- Similar distributions that should not trigger meaningful drift.
- Statistical significance versus practical significance.
- Nulls, constant columns, empty datasets, and insufficient samples.
- Numeric outlier detection.
- Categorical anomalies.
- Isolation Forest and LOF on suitable datasets.
- Multivariate detector behaviour on unsuitable datasets.
- All baseline strategies.
- Missing or invalid baselines.
- First-version behaviour.
- Rolling-baseline construction.
- Historical retrieval and change-onset identification.
- Integration with existing quality assessments.
- Detector errors and partial assessments.
- Policy fingerprinting and idempotency.
- Concurrent assessment requests.
- Persistence and migration behaviour.

Use deterministic synthetic datasets with known expected outcomes. Avoid brittle assertions that depend on exact floating-point values where tolerances are more appropriate.

## 6. Non-goals

Do not implement in this phase:

- Incident creation, acknowledgement, assignment, or resolution.
- REST API or dashboard.
- Background workers or distributed scheduling.
- Redis, Celery, or message brokers.
- LLM integration or autonomous agents.
- Notifications or alert delivery.
- Cross-dataset comparisons.
- Automatic causal claims.
- Automatic remediation.

These belong to later phases.

## 7. Acceptance criteria

Phase 3 is complete when:

1. Sentinel can compare dataset versions using all supported baseline strategies.
2. Schema drift, distribution drift, and univariate/multivariate anomalies are detected and persisted.
3. Detection policies and their fingerprints make assessments reproducible.
4. Statistical significance and practical significance are treated separately.
5. Historical assessments can be retrieved to identify changes and their onset.
6. A deterministic investigation service combines reliability and existing quality evidence without making unsupported causal claims.
7. PostgreSQL migrations and integration tests pass against the test database.
8. Existing Phase 1 and Phase 2 behaviour remains intact.
9. Ruff, formatting, mypy, tests, and lockfile checks pass.
10. Documentation explains how to configure policies, select baselines, run assessments, and interpret findings.

The result should be a usable reliability-intelligence engine, not merely a collection of disconnected statistical functions.

## Implementation notes

### Service usage

`sentinel.reliability.service.ReliabilityService` accepts a SQLAlchemy `Session` and the existing managed `FileStore`. Create policies with `DetectionPolicyInput`, then call `run_assessment(ReliabilityAssessmentInput(...))`. The assessment request requires a persisted dataset-version UUID and policy UUID. `BaselineStrategy.PREVIOUS` is the default. `INITIAL` compares against the first version, `ROLLING` requires a positive `rolling_window`, and `EXPLICIT` requires an `explicit_baseline_version_id` belonging to the target dataset. Explicit self-comparison is rejected.

Policies are dataset-scoped and support create, list, update, and enable/disable operations. Policy configuration is typed with Pydantic models. The stable policy fingerprint includes every enabled detector setting, severity, metric threshold, and preprocessing option. The assessment identity includes the target version, strategy, resolved baseline version IDs, policy ID, and policy fingerprint.

Use `retrieve_assessment`, `assessment_history`, `change_onset`, and `investigate` for persisted results. `change_onset(dataset, detector, finding_type, column_name)` returns the earliest target version with a matching DETECTED finding. Historical assessments retain policy and baseline snapshots after policy edits.

### Baseline and metric conventions

- A version-one target has no baseline. It receives `NO_BASELINE` with explicit unavailable comparison findings; target-only anomaly detectors can still run.
- A rolling baseline selects the latest preceding `window` versions, then sorts them by ascending version number. Distribution data is pooled using Polars `diagonal_relaxed` concatenation. Schema comparison uses the latest selected version's schema. The target version is never part of the pool.
- Numeric metrics use finite, non-null samples. KS is unavailable below its configured minimum sample count; PSI and Wasserstein remain available if each side has at least one finite value. Numeric PSI uses unique internal quantiles from the baseline, with out-of-range target points assigned to end bins. For a constant baseline, bins represent equality with that constant versus all other values. PSI proportions use `1e-6` smoothing.
- Categorical metrics exclude nulls, form the sorted union of category labels, and calculate squared base-2 Jensen–Shannon distance and category-bin PSI with the same smoothing. Empty sides produce UNAVAILABLE, never a zero metric.
- Statistical significance is reported separately from practical thresholds. A KS p-value alone does not classify drift. Wasserstein thresholds are absolute values in the feature's units.
- Schema nullability is not reported as changed because Phase 1 stores no declared nullability and observed zero nulls cannot establish a non-null constraint. Added, removed, and type-changed columns are reported with baseline and target values.
- Numeric univariate methods use inclusive IQR fences or a modified Z-score threshold. Categorical rare-value evidence stores a truncated SHA-256 token rather than the value. Multivariate processing is opt-in; it uses numeric columns in source order, capped by `max_features`, removes constant/all-null columns, median-imputes null/non-finite values, applies `StandardScaler`, and seeds Isolation Forest. LOF uses `min(configured_neighbors, row_count - 1)`. Up to 20 row indices are retained as references; full records are never copied to findings.

Finding statuses are DETECTED, NOT_DETECTED, UNAVAILABLE, and ERROR. Assessments are COMPLETE when all configured comparisons are available, PARTIAL when at least one detector is unavailable or fails, ERROR when enabled detectors all fail, and NO_BASELINE for a target with no resolved comparison baseline. Assessment rows are unique by target version and deterministic assessment identity; a transaction-scoped dataset lock serializes rules/policy changes and assessment creation.

The migration identifier is `0003_reliability_intelligence`, following `0002_quality_rules`. Migration application is performed with Alembic using the intended `SENTINEL_DATABASE_URL`; integration environments use `SENTINEL_TEST_DATABASE_URL` and must identify as `sentinel_test` before setup.
