# Sentinel — Project Specification

**Status:** Initial specification  
**Version:** 0.1  
**Implementation status:** Not started

## 1. Product overview

Sentinel is a data reliability and incident investigation platform.

It monitors datasets across successive observations, identifies quality problems and unexpected changes, investigates potential causes, and produces evidence-backed reports.

The system should move beyond isolated data validation. Its central capability is connecting observations over time to identify, investigate and manage data reliability incidents.

## 2. Objectives

Sentinel should:

1. Ingest datasets through configurable interfaces.
2. Infer, record and compare dataset schemas.
3. Generate reproducible statistical profiles.
4. Validate data against configurable quality expectations.
5. Detect anomalies and distribution changes.
6. Compare observations across dataset versions.
7. Investigate incidents using temporal, statistical and relational evidence.
8. Preserve findings and investigation history.
9. Expose functionality through a documented API.
10. Eventually support LLM-assisted synthesis of verified findings.

## 3. System boundaries

### In scope

- File-based ingestion initially: CSV, Parquet and JSON.
- Dataset identification and version tracking.
- Schema inference and schema comparison.
- Statistical profiling.
- Configurable quality checks.
- Anomaly detection.
- Data and schema drift detection.
- Historical comparisons.
- Incident creation and lifecycle management.
- Evidence-backed investigation.
- API and background processing.
- Optional AI-assisted reporting.

### Explicitly out of scope initially

- Direct modification or repair of source datasets.
- Automatic remediation of production data pipelines.
- Building a general-purpose ETL platform.
- Supporting every database, cloud storage provider or streaming system from the beginning.
- Treating LLM-generated explanations as verified root causes.
- Requiring a GPU or locally hosted LLM.

These boundaries can evolve, but expansion must be deliberate.

## 4. Core conceptual components

### 4.1 Dataset ingestion

Responsible for accepting datasets and converting source-specific representations into internal, validated representations.

Expected responsibilities:

- File format handling.
- Dataset identification.
- Input validation.
- Schema inference.
- Dataset metadata extraction.
- Version registration.
- Fingerprinting.
- Clear reporting of ingestion failures.

### 4.2 Data profiling

Responsible for generating descriptive information about datasets.

Expected capabilities:

- Row and column counts.
- Data types.
- Missing-value statistics.
- Cardinality and uniqueness.
- Descriptive statistics.
- Distribution summaries.
- Correlations where appropriate.
- Configurable constraints.

Profiles must be reproducible and associated with the dataset version from which they were generated.

### 4.3 Detection framework

Responsible for identifying deviations and violations.

Detection categories:

- Data quality violations.
- Schema changes.
- Univariate anomalies.
- Multivariate anomalies.
- Distribution drift.
- Temporal changes.
- Concept drift when suitable labels and observations are available.

Potential statistical methods include:

- Population Stability Index.
- Kolmogorov–Smirnov test.
- Jensen–Shannon divergence.
- Wasserstein distance.
- Isolation Forest.
- Local Outlier Factor.
- Statistical thresholds and robust estimators.

Method selection, assumptions, limitations and suitability must be evaluated before implementation.

Detection results should include the method used, relevant configuration, severity, affected features and supporting evidence.

### 4.4 Investigation engine

Responsible for connecting findings and observations into an investigation.

It should answer questions such as:

- When did the problem first appear?
- Which dataset versions are affected?
- Which columns are involved?
- Did the schema change?
- Did related columns change at the same time?
- Can affected records be identified?
- What evidence supports each potential explanation?
- Which explanations remain uncertain?

The system must distinguish observed facts, statistical evidence and hypotheses.

A suspected cause must not be represented as a confirmed root cause without sufficient evidence.

### 4.5 Incident management

Responsible for turning significant findings into persistent, manageable incidents.

Expected capabilities:

- Incident creation.
- Severity classification.
- Status and lifecycle tracking.
- Acknowledgement and resolution.
- Related findings and evidence.
- Historical records.
- Reproducible reports.

### 4.6 AI investigation layer

This is a later capability.

An LLM may:

- Summarise verified findings.
- Explain statistical results.
- Connect evidence into a coherent narrative.
- Suggest investigation steps.
- Produce human-readable incident reports.

It must not independently invent measurements, perform unverified calculations or fabricate evidence.

The deterministic analysis layer remains authoritative.

### 4.7 API and orchestration

The API will eventually expose dataset, profiling, detection and incident functionality.

Background processing will be introduced where workloads justify it.

Operational concerns include:

- Structured logging.
- Health checks.
- Metrics.
- Processing status.
- Failure handling.
- Retry behaviour.
- Observability.

## 5. High-level architecture

The provisional logical architecture is:

```text
                 DATA SOURCES
                      |
                      v
               INGESTION LAYER
                      |
                      v
              DATASET MANAGEMENT
                      |
                      v
              PROFILING ENGINE
                      |
                      v
              DETECTION FRAMEWORK
               /       |       \
              /        |        \
          QUALITY    DRIFT    ANOMALY
              \        |        /
               \       |       /
                      |
                      v
             INVESTIGATION ENGINE
                      |
             +--------+--------+
             |                 |
             v                 v
       DETERMINISTIC       AI SYNTHESIS
         ANALYSIS             (LATER)
             |                 |
             +--------+--------+
                      |
                      v
               INCIDENT MANAGER
                      |
                      v
                PERSISTENCE
                      |
                      v
                  API LAYER
                      |
                      v
                CLIENT / UI
```

This is a logical architecture, not a commitment to a particular deployment topology.

## 6. Architectural principles

- Separation of concerns.
- Explicit domain models and interfaces.
- Deterministic analytical operations.
- Reproducibility.
- Evidence preservation.
- Extensibility without premature abstraction.
- Validated inputs and outputs.
- Clear error boundaries.
- Testability.
- Observability.
- Documentation of consequential decisions.

## 7. Technology direction

### Established decisions

| Concern | Technology |
|---|---|
| Language | Python 3.14 |
| Environment and package management | uv |
| Data processing | Polars |
| Domain and application contracts | Pydantic |
| Persistence | PostgreSQL |
| ORM | SQLAlchemy 2.x |
| Database migrations | Alembic |
| PostgreSQL driver | Psycopg 3 |
| Testing | pytest |
| Linting and formatting | Ruff |
| Static typing | mypy |

### Deferred decisions

| Concern | Candidate |
|---|---|
| API | FastAPI |
| Statistical analysis | SciPy, where needed |
| Machine learning | scikit-learn |
| Background processing | To be evaluated |
| Containerisation | Docker |
| CI | GitHub Actions |
| Documentation site | MkDocs |
| LLM integration | Provider-independent interface |

Deferred technologies should be introduced only when an implementation requirement justifies them.

See `docs/decisions/` for the rationale behind accepted architectural decisions.

## 8. Non-functional requirements

The system should aim for:

### Correctness
Analytical outputs must be mathematically defensible and validated against known cases.

### Reproducibility
The same dataset, configuration and software version should produce consistent results, subject to documented sources of nondeterminism.

### Reliability
Failures must be explicit, observable and recoverable where appropriate.

### Maintainability
Components should have clear responsibilities and stable interfaces.

### Performance
Processing should be designed to accommodate increasing dataset sizes without requiring the entire system to operate in memory.

### Extensibility
New ingestion adapters, detectors and investigation strategies should be addable without rewriting unrelated components.

### Auditability
Findings, configurations and evidence should be traceable to the dataset versions and processing operations that produced them.

### Security
Dataset contents, credentials and sensitive information must be handled deliberately. Secrets must never be committed to the repository.

## 9. Provisional implementation roadmap

1. Project foundation and development tooling.
2. Ingestion and dataset version management.
3. Data profiling.
4. Quality validation and detection framework.
5. Drift and anomaly detection.
6. Persistence and incident management.
7. Investigation engine.
8. API and background processing.
9. AI-assisted investigation.
10. Production hardening and deployment.

Phase boundaries and ordering may change as architectural decisions are made.

## 10. Definition of done

A feature is not complete merely because its implementation exists.

Completion requires:

- Defined behaviour and acceptance criteria.
- Appropriate automated tests.
- Input validation and error handling.
- Passing linting, formatting and type checks.
- Documentation of relevant interfaces and limitations.
- No unexplained architectural deviations.
- A reproducible way to verify the feature.

## 11. Current state

Sentinel is in its foundational setup stage.

No ingestion, profiling, detection, investigation or incident-management functionality should be assumed to exist.

The first implementation milestone will establish the domain foundation and a small, end-to-end vertical slice before the system is expanded.
