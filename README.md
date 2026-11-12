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

**Pre-implementation.**

The project is currently undergoing foundational setup and architectural specification. Planned capabilities are not yet implemented.

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
