# Phase 1 — Dataset Foundation

**Status:** Ready for implementation  
**Version:** 1.0  
**Prerequisites:** Project foundation and local PostgreSQL setup  
**Primary objective:** Establish reliable dataset registration, immutable versioning, file storage and statistical profiling.

## 1. Objective

Implement the first functional foundation of Sentinel.

The system must accept a dataset, register its identity, preserve an immutable copy of its contents, infer its schema, generate a statistical profile, persist the results and make them retrievable.

This phase establishes the domain and persistence foundations for subsequent detection, investigation and incident-management functionality.

## 2. Scope

### Included

- Dataset registration.
- CSV, Parquet and JSON ingestion.
- JSON Lines / NDJSON ingestion.
- Input validation.
- SHA-256 content fingerprinting.
- Dataset identity and version management.
- Immutable file snapshots.
- Schema inference and fingerprinting.
- Statistical profiling.
- PostgreSQL persistence.
- SQLAlchemy models and repositories.
- Alembic migrations.
- Service-layer interfaces.
- Unit and integration tests.
- Configuration for database and storage locations.

### Excluded

- REST API and FastAPI.
- CLI and graphical interface.
- Background job processing.
- Anomaly detection.
- Drift detection.
- Configurable quality rules.
- Incident management.
- Investigation and root-cause analysis.
- LLM integration.
- Cloud storage and remote data sources.
- Automatic dataset remediation.

Do not implement excluded functionality prematurely.

## 3. Architectural structure

Use a modular structure that separates domain logic from infrastructure.

A provisional package organisation:

```text
src/sentinel/
├── __init__.py
├── config/
├── domain/
│   ├── models/
│   └── exceptions/
├── ingestion/
│   ├── readers/
│   └── service.py
├── profiling/
├── storage/
├── persistence/
│   ├── models/
│   ├── repositories/
│   └── database.py
└── ...
```

This is a suggested organisation, not a requirement to create empty modules for every future feature.

Create modules only when they have actual responsibilities.

### Architectural rules

- Domain concepts must not depend directly on SQLAlchemy ORM models.
- Persistence must be accessed through repository interfaces or appropriately separated repository implementations.
- Polars-specific operations should remain within ingestion and profiling boundaries.
- Pydantic models should represent validated inputs and outputs where appropriate.
- Application services should coordinate operations without containing every implementation detail.
- Avoid unnecessary dependency-injection frameworks and generic plugin systems.

## 4. Dataset identity

A dataset represents a logical collection of related observations over time.

A dataset must be registered using an explicit, user-provided slug.

Example:

```text
slug: customer-transactions
display_name: Customer Transactions
```

### Dataset entity

Minimum attributes:

- `id`: UUID primary key.
- `slug`: unique, normalised identifier.
- `display_name`: human-readable name.
- `description`: optional description.
- `created_at`: UTC timestamp.
- `updated_at`: UTC timestamp.

The slug must be unique and must not be derived implicitly from the source filename.

The source filename is metadata, not dataset identity.

## 5. Dataset versioning

A dataset version represents an immutable observation of a logical dataset at a particular ingestion time.

### DatasetVersion entity

Minimum attributes:

- `id`: UUID primary key.
- `dataset_id`: foreign key.
- `version_number`: positive sequential integer within its dataset.
- `source_filename`: original filename.
- `format`: detected and validated file format.
- `content_sha256`: SHA-256 fingerprint of original file bytes.
- `file_size_bytes`: original file size.
- `storage_key`: relative path to the managed snapshot.
- `schema`: structured representation of inferred schema.
- `schema_fingerprint`: fingerprint of the canonical schema representation.
- `row_count`: number of observations.
- `column_count`: number of columns.
- `profile`: structured statistical profile.
- `ingested_at`: UTC timestamp.

### Versioning rules

1. Version numbers start at 1 for each dataset.
2. Versions are immutable after successful registration.
3. Changed file content creates a new version.
4. Identical content ingested into the same dataset returns the existing version.
5. Identical content registered under a different dataset slug represents a separate dataset.
6. Version numbers must be assigned safely under concurrent ingestion.
7. Enforce uniqueness of `(dataset_id, version_number)`.
8. Enforce uniqueness of `(dataset_id, content_sha256)`.

Use database transactions and appropriate locking or equivalent concurrency control to prevent duplicate version numbers and duplicate registrations.

Do not update old versions when new data arrives.

## 6. File ingestion

### Supported formats

| Format | Supported representation |
|---|---|
| CSV | Header-based CSV with comma delimiter |
| Parquet | Standard Parquet file |
| JSON | Top-level array of JSON objects |
| JSON Lines | One JSON object per line |
| NDJSON | Newline-delimited JSON objects |

Do not silently interpret arbitrary JSON structures as tabular data.

Unsupported structures must produce informative validation errors.

### Ingestion workflow

1. Receive a source path and explicit dataset slug.
2. Validate the source path and file.
3. Validate the file extension and format.
4. Calculate SHA-256 while copying the source into a temporary managed-storage location.
5. Parse the staged file using Polars.
6. Validate the resulting tabular representation.
7. Infer schema and calculate the schema fingerprint.
8. Generate the statistical profile.
9. Register or retrieve the logical dataset.
10. Determine whether the content already exists for that dataset.
11. Assign the next version number if the content is new.
12. Atomically move the staged file into its permanent storage location.
13. Persist metadata, schema and profile transactionally.
14. Return the registered dataset and version information.

Handle failures explicitly.

Temporary files must be cleaned up on failure. If persistence fails after a file has been moved, clean up the newly created unreferenced file where safe.

The implementation must not leave partially registered dataset versions.

### File storage

Default root:

```text
~/.local/share/sentinel/
```

Configurable using:

```text
SENTINEL_DATA_DIR
```

Suggested layout:

```text
sentinel-data/
└── datasets/
    └── <dataset-uuid>/
        └── versions/
            ├── 1/
            │   └── source.csv
            └── 2/
                └── source.parquet
```

Store original file bytes rather than serialising the Polars DataFrame back into a new file.

Use UUID-based internal paths, not user-provided filenames, to construct storage paths.

The original filename should remain metadata.

Never store dataset files inside the Git repository.

### Initial processing limitations

The first implementation may use eager Polars DataFrames.

Do not claim unlimited dataset-size support or streaming ingestion.

Document the memory implications and ensure failures caused by unsupported or malformed input are explicit.

## 7. Schema representation

Represent schemas in a structured, serialisable format.

Each column should include:

- Column name.
- Polars data type.
- Column position.

Preserve column ordering.

Generate a deterministic schema fingerprint from a canonical representation. Identical schemas should produce identical fingerprints.

Do not conflate schema fingerprinting with file-content fingerprinting.

The file fingerprint identifies the original file contents; the schema fingerprint identifies its structural representation.

## 8. Statistical profiling

Generate a reproducible profile for every successfully ingested version.

### Dataset-level metrics

- Row count.
- Column count.
- Total missing values.
- Overall missing-value percentage.

### Column-level metrics

For every column:

- Name.
- Data type.
- Null count.
- Null percentage.
- Exact distinct-value count.

For numeric columns, additionally calculate:

- Minimum.
- Maximum.
- Mean.
- Standard deviation.

For floating-point columns, distinguish nulls from NaN values. Document the treatment of NaN and infinite values in numeric statistics.

Do not calculate expensive or unbounded metrics without a clear reason.

Do not add correlations, drift metrics, anomaly scores or quality-rule evaluations in this phase.

### Profile requirements

- Profiles must be associated with a specific dataset version.
- Profiles must be serialisable and persistable.
- Profile calculations must be independently testable.
- Empty datasets and empty columns must be handled explicitly.
- Results must be deterministic for identical input and configuration.
- Missing values must not cause misleading numeric summaries.

## 9. Persistence

Use PostgreSQL as the relational database.

Use:

- SQLAlchemy 2.x.
- Alembic.
- Psycopg 3.

### Initial relational entities

At minimum:

1. Dataset.
2. DatasetVersion.

Represent structured schema and profile information using appropriate PostgreSQL JSON storage.

Use database constraints to enforce uniqueness and referential integrity.

Use UTC-aware timestamps.

Create an initial Alembic migration and ensure the schema can be created from an empty database.

### Database configuration

Read the database connection string from:

```text
SENTINEL_DATABASE_URL
```

Do not hard-code credentials or connection strings.

Do not commit secrets.

Integration tests must use `SENTINEL_TEST_DATABASE_URL`, never
`SENTINEL_DATABASE_URL`.

Test setup must verify that the configured database is the dedicated
test database before performing destructive operations.

## 10. Application interfaces

Expose the core functionality through application services, not a web API.

At minimum, provide operations conceptually equivalent to:

- Register or retrieve a dataset.
- Ingest a dataset version.
- Retrieve a dataset by slug or ID.
- List dataset versions.
- Retrieve a dataset version.
- Retrieve its statistical profile.

Use clear input and output contracts.

Do not expose SQLAlchemy models directly as public domain contracts.

## 11. Error handling

Define meaningful domain exceptions for cases such as:

- Unsupported file format.
- Invalid or malformed input.
- Empty or unreadable source file.
- Invalid dataset slug.
- Dataset not found.
- Duplicate registration or version conflicts.
- Persistence failure.
- Storage failure.
- Profiling failure.

Do not swallow exceptions or convert every failure into a generic error.

Errors should preserve useful context without exposing credentials or sensitive filesystem details unnecessarily.

## 12. Testing strategy

Use pytest.

### Unit tests

Test independently:

- Dataset slug validation.
- File-format detection.
- SHA-256 calculation.
- Schema inference.
- Schema fingerprint stability.
- Statistical profile calculations.
- Missing-value handling.
- Numeric statistics.
- Empty datasets and columns.
- Invalid input handling.

### Integration tests

Using the dedicated `sentinel_test` PostgreSQL database, test:

- Dataset registration.
- Dataset uniqueness.
- First version creation.
- Sequential version creation.
- Duplicate-content idempotency.
- Dataset-version retrieval.
- Profile persistence and retrieval.
- File snapshot preservation.
- Database and filesystem failure handling.
- Migration from an empty database.

Never run destructive test cleanup against the development database.

Tests must use temporary storage directories and must not modify user source files.

Use small, deterministic fixture datasets.

Do not use real personal or confidential data.

### Quality gates

All of the following must pass:

```bash
uv run pytest --cov=sentinel --cov-report=term-missing
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

## 13. Acceptance criteria

Phase 1 is complete when:

1. CSV, Parquet and supported JSON representations can be ingested.
2. Invalid inputs fail with informative errors.
3. A dataset can be registered using an explicit slug.
4. Dataset versions are sequential and immutable.
5. Re-ingesting identical content is idempotent.
6. Changed content creates a new version.
7. Original file contents are preserved in managed storage.
8. Schema inference and schema fingerprinting work.
9. Statistical profiles contain the specified metrics.
10. Metadata, schemas and profiles persist in PostgreSQL.
11. Retrieval operations return the expected information.
12. Alembic can initialise an empty database.
13. Unit and integration tests pass.
14. Linting, formatting and type checking pass.
15. Documentation explains interfaces, configuration and limitations.

## 14. Deliverables

- Implemented domain models and contracts.
- Ingestion implementation.
- Polars-based readers.
- Managed file storage.
- Schema inference and fingerprinting.
- Statistical profiling.
- PostgreSQL persistence.
- SQLAlchemy models and repositories.
- Alembic migration.
- Application services.
- Unit and integration tests.
- Configuration documentation.
- Updated README where necessary.

Do not begin Phase 2 functionality as part of this assignment.
