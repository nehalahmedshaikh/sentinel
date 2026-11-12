# ADR 0001: Foundational Architecture Decisions

**Status:** Accepted  
**Date:** 2026-11-12

This record documents the initial architectural decisions for Sentinel.

## 1. Data processing engine: Polars

**Decision:** Use Polars as the primary tabular data processing engine.

**Context:** Sentinel needs efficient ingestion, profiling, statistical computation and eventually large-scale dataset processing.

**Alternatives considered:**
- pandas.
- Polars.
- Maintaining both.

**Rationale:**
- Columnar processing model.
- Native implementation.
- Strong support for CSV, Parquet and JSON.
- Lazy execution and streaming capabilities available for future use.
- Avoids maintaining two competing DataFrame abstractions.

**Consequences:**
- Polars is the internal processing representation.
- Domain contracts must not expose Polars-specific implementation details.
- Conversion to other representations should occur only at explicit boundaries.
- Initial implementation may use eager processing, with scalability limitations documented.

## 2. Dataset identity and versioning

**Decision:** Use explicitly registered logical datasets, UUID identifiers and sequential immutable versions.

**Context:** Filenames are not reliable dataset identifiers. Sentinel needs to compare observations of the same logical dataset over time.

**Rationale:**
- Explicit slugs provide stable human-readable identities.
- UUIDs provide internal identifiers independent of filenames.
- Sequential version numbers make dataset history understandable.
- SHA-256 fingerprints support content identification and idempotent ingestion.

**Consequences:**
- Dataset registration requires an explicit slug.
- A changed file creates a new immutable version.
- Re-ingesting identical content into the same dataset returns the existing version.
- Database constraints and transactional concurrency control are required.

## 3. Persistence: PostgreSQL

**Decision:** Use PostgreSQL from the beginning, accessed through SQLAlchemy 2.x and managed through Alembic.

**Context:** Sentinel will maintain dataset identities, version histories, profiles, findings and eventually incidents. The system will eventually support concurrent processing and historical queries.

**Alternatives considered:**
- SQLite initially, with a later migration.
- PostgreSQL from the beginning.

**Rationale:**
- Strong transactional guarantees.
- Relational integrity and constraints.
- Appropriate concurrency support.
- Structured JSON storage.
- Avoids designing an initial persistence layer around SQLite-specific limitations.

**Consequences:**
- Local PostgreSQL is required for development.
- Automated integration tests use a dedicated test database.
- Domain logic must remain independent of SQLAlchemy models.
- Database schema changes must be managed through Alembic migrations.

## 4. File storage: managed local filesystem

**Decision:** Preserve ingested source files as immutable snapshots in a managed filesystem directory.

**Context:** Database records alone cannot preserve the source data needed to reproduce historical profiling and investigations.

**Rationale:**
- Avoids storing large binary datasets in relational tables.
- Preserves original bytes.
- Provides a simple initial storage backend.
- Allows a future object-storage implementation without changing dataset identity or profiling contracts.

**Consequences:**
- The storage root is configurable through `SENTINEL_DATA_DIR`.
- The default root is `~/.local/share/sentinel`.
- Database records reference managed storage locations.
- File and database operations require explicit failure handling because they do not share a single transaction.

## 5. Domain contracts: Pydantic

**Decision:** Use Pydantic for validated application inputs and outputs where appropriate.

**Context:** Sentinel needs explicit, serialisable contracts between application layers.

**Rationale:**
- Runtime validation.
- Clear field constraints.
- Serialisation support.
- Type-oriented interfaces.

**Consequences:**
- Public domain/application contracts must not expose ORM entities.
- Pydantic models should not be introduced indiscriminately for every internal object.
- Domain concepts should remain independent of infrastructure implementations.

## 6. AI integration: deferred

**Decision:** Do not introduce an LLM or agent framework in the initial implementation.

**Context:** Sentinel's analytical foundation must be independently reliable.

**Rationale:**
- Statistical findings must be deterministic and verifiable.
- AI explanations must be grounded in evidence.
- Introducing an LLM before the evidence model exists would encourage unsupported conclusions.

**Consequences:**
- The initial system is useful without AI.
- Future AI integration will consume verified findings through explicit interfaces.
- No LangChain or agent framework is justified at this stage.
