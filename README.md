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

**Phase 1 implemented: dataset foundation.** Sentinel supports dataset registration, immutable ingestion versions, managed source snapshots, schema fingerprints, and deterministic statistical profiles. Detection, investigation, incidents, APIs and background processing remain future phases.

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

## Documentation

- [Project specification](docs/PROJECT_SPEC.md)
- [Development setup](docs/DEVELOPMENT.md)
- [Phase 1: Dataset foundation](docs/PHASE_01_DATASET_FOUNDATION.md)
- [Architecture decisions](docs/decisions/README.md)
- [Initial architecture decisions](docs/decisions/0001-foundational-architecture.md)
- [Coding agent instructions](AGENTS.md)
