# Sentinel

**An autonomous data reliability and incident investigation platform.**

Sentinel is an engineering project focused on detecting, investigating, and managing data reliability incidents.

Data pipelines evolve. Schemas change, distributions drift, data quality deteriorates, and anomalies emerge. These problems can remain unnoticed until they affect downstream systems, analytical results, or business decisions.

Sentinel aims to provide a systematic way to monitor datasets, identify emerging problems, investigate their causes, and produce reproducible, evidence-backed incident reports.

## Vision

Build a modular, extensible data reliability platform combining:

- Deterministic statistical analysis.
- Data quality validation.
- Anomaly and drift detection.
- Historical dataset comparison.
- Evidence-based incident investigation.
- Persistent incident management.
- AI-assisted investigation and reporting.

The system should be useful independently of its AI capabilities. Statistical findings and incident evidence must remain verifiable without an LLM.

## Planned capabilities

- Data ingestion: CSV, Parquet, and JSON.
- Schema inference and version tracking.
- Dataset profiling and statistical summaries.
- Data quality and constraint validation.
- Univariate and multivariate anomaly detection.
- Schema and distribution drift detection.
- Historical comparison and incident investigation.
- Evidence preservation and reproducible reports.
- REST API and background processing.
- Optional LLM-assisted investigation.

## Engineering principles

- Deterministic analytical results.
- Reproducibility.
- Modular architecture and separation of concerns.
- Explicit interfaces and validated data contracts.
- Automated testing.
- Evidence-backed conclusions.
- Documentation of architectural decisions.
- Incremental implementation without unnecessary dependencies.

## Project status

**Phases 1–4 implemented.** Sentinel supports dataset registration, immutable ingestion versions, managed source snapshots, deterministic profiling and quality assessments, schema and distribution drift, configurable anomaly detection, historical baselines, deterministic investigations, and evidence-backed persistent incident management. APIs and background processing remain future phases.

## Development

Python 3.14 and `uv` are used for environment and dependency management.

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

See [AGENTS.md](AGENTS.md) for engineering instructions and [docs/PROJECT_SPEC.md](docs/PROJECT_SPEC.md) for the project scope and architectural direction.

## Dataset foundation

Set `SENTINEL_DATABASE_URL` to a PostgreSQL connection and optionally set `SENTINEL_DATA_DIR` (defaults to `~/.local/share/sentinel`). Initialize an empty database with:

```bash
uv run alembic upgrade head
```

The application service is `sentinel.ingestion.service.DatasetService`; construct it with a SQLAlchemy session and `FileStore`. Register with a validated `DatasetInput`, ingest with `service.ingest(Path("data.csv"), dataset_input)`, then retrieve using `retrieve_dataset`, `retrieve_versions` or `retrieve_version`. Supported files are comma-delimited header CSV, Parquet, JSON arrays of objects, and JSON Lines/NDJSON. Profiles are eager Polars calculations; the complete frame is loaded into memory. Floating NaN and infinite values are excluded from numeric minimum, maximum, mean and standard deviation; null counts remain distinct from NaN.

Integration tests use only `SENTINEL_TEST_DATABASE_URL` and refuse connections unless PostgreSQL reports the database name `sentinel_test`. Apply migrations to that database before running them.

## Data quality rules

Phase 2 supports `required_column`, `data_type`, `not_null`, `null_percentage`, `unique`, `min_value`, `max_value`, `range`, `accepted_values`, and `duplicate_rows`. Rule parameters are validated by type-specific Pydantic contracts. For example, after ingesting a version:

```python
from sentinel.quality.contracts import QualityRuleInput, RuleType, Severity
from sentinel.quality.service import QualityService

quality = QualityService(session, file_store)
quality.create_rule(
    "customer-transactions",
    QualityRuleInput(
        name="transaction id required",
        rule_type=RuleType.NOT_NULL,
        parameters={"kind": "not_null", "column_name": "transaction_id"},
        severity=Severity.ERROR,
    ),
)
assessment = quality.evaluate_version(dataset_version_id)
```

Use `get_rules`, `update_rule`, `set_rule_enabled`, and `delete_rule` to manage expectations. Rules with historical findings cannot be deleted; disable them instead. Use `retrieve_assessment`, `retrieve_findings`, or `assessment_history` to read persisted results. Null checks do not treat NaN as null; uniqueness treats all nulls as one distinct value; accepted-values rules treat null as unaccepted. Row-dependent rules report ERROR for empty datasets. An empty enabled ruleset also returns ERROR with a message stating that no assertions ran. Numeric summaries exclude NaN from observed finite bounds and count NaN as violating numeric predicates.

## Reliability intelligence

Create and manage a dataset-specific policy with `ReliabilityService.create_policy`, `get_policies`, `update_policy`, and `set_policy_enabled`. A typical configuration explicitly enables multivariate detection:

```python
from sentinel.reliability.contracts import (
    DetectionPolicyConfig,
    DetectionPolicyInput,
    MultivariateAnomalyPolicy,
    NumericDriftPolicy,
    ReliabilityAssessmentInput,
    BaselineStrategy,
)
from sentinel.reliability.service import ReliabilityService

reliability = ReliabilityService(session, file_store)
policy = reliability.create_policy(
    "customer-transactions",
    DetectionPolicyInput(
        name="production defaults",
        config=DetectionPolicyConfig(
            numeric_drift=NumericDriftPolicy(psi_threshold=0.2),
            multivariate_anomaly=MultivariateAnomalyPolicy(enabled=True),
        ),
    ),
)
result = reliability.run_assessment(
    ReliabilityAssessmentInput(
        dataset_version_id=dataset_version_id,
        policy_id=policy.id,
        baseline_strategy=BaselineStrategy.PREVIOUS,
    )
)
```

The baseline strategies are `previous`, `initial`, `rolling` (requires `rolling_window`), and `explicit` (requires a same-dataset `explicit_baseline_version_id`). Rolling comparisons pool the selected preceding versions in ascending version order using Polars `diagonal_relaxed` concatenation for distribution metrics; schema comparison uses the newest selected baseline version. The target is excluded from baseline selection. The first version receives a persisted `NO_BASELINE` assessment while target-only anomaly detectors may still run.

Numeric drift reports two-sample KS statistic/p-value, PSI, and raw-unit Wasserstein distance. PSI uses quantile cut points from the baseline; duplicate cut points collapse to fewer bins, while a constant baseline uses match/non-match bins. Categorical drift reports base-2 Jensen–Shannon divergence (squared SciPy distance, range 0–1) and category-based PSI. Nulls are excluded from distribution metrics but their counts are recorded; numeric NaN and infinities are excluded and counted. Practical thresholds determine DETECTED independently of KS significance. Wasserstein thresholds use the feature's original units and should be tuned to that feature.

Numeric univariate anomalies use IQR fences or modified Z-scores. Categorical anomalies hash rare category labels before storing evidence. Isolation Forest and LOF are disabled by default; when enabled, preprocessing selects numeric columns in source order up to `max_features`, removes all-null/constant columns, median-imputes missing/non-finite values, and applies `StandardScaler`. Anomaly evidence stores at most 20 row indices and aggregate counts, not full records. `assessment_history` retrieves persisted trends and `change_onset` finds the earliest assessment with a matching detected finding. `investigate(version_id)` joins reliability evidence with persisted Phase 2 quality assessments and reports co-occurrence as correlation, never causation.

Migration `0003_reliability_intelligence` adds policy, assessment, and finding tables. Apply with `uv run alembic upgrade head` only after setting `SENTINEL_DATABASE_URL` to the intended database. PostgreSQL integration tests require migrations to be present in the database named `sentinel_test`.

## Incident intelligence

Create a dataset-specific incident policy and process completed quality or reliability assessments. Policies select qualifying finding types and source severities, configure the correlation window, and provide thresholds for evidence-backed severity. A finding qualifies only when a quality result is `FAIL` or a reliability result is `DETECTED`; error, unavailable, and passing outcomes are retained as non-qualifying decisions.

```python
from sentinel.incidents.contracts import IncidentPolicyInput
from sentinel.incidents.service import IncidentService

incidents = IncidentService(session)
policy = incidents.create_policy("customer-transactions", IncidentPolicyInput(name="default"))
created = incidents.process_quality_assessment(quality_result.assessment.id, policy.id)
reliability_created = incidents.process_reliability_assessment(
    reliability_result.assessment.id, policy.id
)
```

Correlation fingerprints hash the dataset UUID, exact finding category, and normalized affected-column set. New evidence attaches to one unambiguous, same-category incident within the configured time window when the Jaccard overlap between affected-column sets reaches `correlation_minimum_column_overlap` (default 1.0, exact set). Resolved matches may reopen on qualifying recurrence. Ambiguous or column-less matches create separate incidents. Dataset identity is always part of the fingerprint. The selected overlap and decision are preserved with evidence. `merge()` and `link()` are explicit, audited same-dataset operations; merged evidence and timelines remain queryable through the surviving incident report.

Use `list_incidents`, `retrieve`, `acknowledge`, `start_investigation`, `add_note`, `set_severity`, `resolve`, and `reopen` to manage incidents. Lifecycle changes are validated and create immutable events. Automatic severity considers source severity, observed violation counts, and drift magnitude; evidence can escalate severity, while later less severe evidence cannot automatically downgrade it. Manual changes record a separate decision.

`report(incident_id)` deterministically assembles persisted evidence, timeline, affected versions/columns, facts, statistical evidence, recorded correlations, hypotheses, missing evidence, and resolution details. `historical_summary(dataset)` aggregates incident status/severity, affected columns, categories, recurring versions, reopenings, and automatic severity escalations. Severity decisions use source severity, affected record counts, affected-column count, drift magnitude, and repeated evidence. Incident events, evidence links, and generation decisions are database-protected against updates and deletes.

Migration `0004_incident_intelligence` adds incident policies, incidents, evidence, events, and idempotent generation-decision tables. Apply with `uv run alembic upgrade head` only after setting `SENTINEL_DATABASE_URL` to the intended database. For Phase 4 integration tests, verify and migrate only `sentinel_test`.

## Documentation

- [Project specification](docs/PROJECT_SPEC.md)
- [Development setup](docs/DEVELOPMENT.md)
- [Phase 1: Dataset foundation](docs/PHASE_01_DATASET_FOUNDATION.md)
- [Architecture decisions](docs/decisions/README.md)
- [Initial architecture decisions](docs/decisions/0001-foundational-architecture.md)
- [Coding agent instructions](AGENTS.md)
- [Phase 2: Data quality detection](docs/PHASE_02_DATA_QUALITY.md)
- [Data quality rule engine decision](docs/decisions/0002-data-quality-rule-engine.md)
- [Phase 3: Data Reliability Intelligence](docs/PHASE_03_RELIABILITY_INTELLIGENCE.md)
- [ADR 0003: Data Reliability Intelligence](docs/decisions/0003-reliability-intelligence.md)
- [Phase 4: Incident Intelligence](docs/PHASE_04_INCIDENT_INTELLIGENCE.md)
- [ADR 0004: Incident Intelligence](docs/decisions/0004-incident-intelligence.md)
