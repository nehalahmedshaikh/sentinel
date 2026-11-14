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

**Phases 1–5 implemented.** Sentinel supports dataset registration, immutable ingestion versions, managed source snapshots, deterministic profiling and quality assessments, schema and distribution drift, configurable anomaly detection, historical baselines, evidence-backed incident management, durable background jobs, scheduled monitoring, an operator dashboard, a versioned API, and optional evidence-grounded AI investigations.

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

## Operational platform

Phase 5 adds a FastAPI application, PostgreSQL job and schedule records, a transactional outbox, Celery workers and Beat, a Jinja2/HTMX dashboard, Prometheus metrics, and an optional OpenAI-compatible investigation provider. Redis is the task broker; PostgreSQL remains authoritative for jobs and results. AI is disabled unless explicitly enabled.

Copy `.env.example` to `.env` and set a private API key plus the local PostgreSQL and Redis URLs. The example uses separate `sentinel` and `sentinel_test` databases. Keep both database variables pointed at their intended database; integration tests refuse to run unless PostgreSQL reports `sentinel_test`.

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn sentinel.api.app:app --host 127.0.0.1 --port 8000
```

In separate terminals, start Redis, then run a worker and the periodic dispatcher:

```bash
uv run celery -A sentinel.operations.celery_app:celery_app worker --loglevel=INFO --concurrency=1
uv run celery -A sentinel.operations.celery_app:celery_app beat --loglevel=INFO
```

Open the dashboard at `http://127.0.0.1:8000/`. The API uses `/api/v1`; OpenAPI is at `/docs`, liveness at `/health`, dependency readiness at `/ready`, and Prometheus metrics at `/metrics`. Send `Authorization: Bearer $SENTINEL_API_KEY` to operational API routes. The dashboard is intended for local operator use and does not implement multi-user authentication.

Uploads are limited by `SENTINEL_UPLOAD_MAX_BYTES` and `SENTINEL_ALLOWED_FILE_TYPES`, validated before a job is committed, staged below the managed data directory, and removed after success or a terminal failure. Broker publication is retried from PostgreSQL outbox rows. If Redis is unavailable, jobs remain visible as queued and are published when the dispatcher can reconnect.

For AI, set `SENTINEL_AI_ENABLED=true`, `SENTINEL_AI_BASE_URL`, and `SENTINEL_AI_MODEL`; set `SENTINEL_AI_API_KEY` only when required by the provider. The endpoint may be an origin or an OpenAI-compatible `/v1` base URL. Investigations are read-only, send bounded evidence metadata rather than dataset records, require evidence UUIDs for observations and hypotheses, and persist provider failures without changing Sentinel findings.

Useful checks:

```bash
uv run pytest --cov=sentinel --cov-report=term-missing
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv lock --check
git diff --check
```

See [Phase 5 operational documentation](docs/PHASE_05_OPERATIONAL_PLATFORM_AND_AI.md) for configuration details, service endpoints, task behavior, and troubleshooting.

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

Incident policies are dataset scoped. Through the API, list them with `GET /api/v1/incidents/datasets/{dataset_slug}/policies`, create one with `POST` to the same path and `{"name":"default"}`, update with `PUT /api/v1/incidents/policies/{policy_id}`, and enable or disable with `POST /api/v1/incidents/policies/{policy_id}/enabled?enabled=true|false`. Omitting `config` uses the existing `IncidentPolicyConfig` defaults (including its ERROR/CRITICAL source severity thresholds); no separate policy engine or automatic seed policy is introduced. The dataset detail dashboard lists, creates, edits, and enables/disables these policies. Its quality and reliability assessment histories offer incident generation forms that bind the chosen persisted assessment to an enabled policy for that dataset. API incident generation uses `POST /api/v1/incidents/generate` with `source_type`, `assessment_id`, and `policy_id`. Repeated submissions use the same idempotency identity.

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
- [Phase 5 specification](docs/PHASE_05_OPERATIONAL_PLATFORM_AND_AI.md)
- [ADR 0005 — Operational Platform and AI](docs/decisions/0005-operational-platform-and-ai.md)
