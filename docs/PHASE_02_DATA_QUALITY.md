# Phase 2 — Data Quality Detection

**Status:** Ready for implementation  
**Version:** 1.0  
**Prerequisite:** Phase 1 — Dataset Foundation

## 1. Objective

Implement a configurable, persistent data-quality rule engine.

Sentinel must allow users to define expectations about datasets, evaluate those expectations against specific dataset versions, and preserve structured evidence of the results.

The engine must be deterministic, independently testable and integrated with the existing dataset foundation.

## 2. Scope

### Included

- Configurable dataset-specific quality rules.
- Persistent rule definitions.
- Rule validation.
- Rule creation, retrieval, updating, enabling and disabling.
- Rule evaluation against immutable dataset versions.
- Quality assessments.
- Structured quality findings.
- Assessment and ruleset fingerprinting.
- Assessment persistence and retrieval.
- Unit and PostgreSQL integration tests.

### Excluded

- Schema drift detection.
- Statistical distribution drift.
- Machine-learning anomaly detection.
- Cross-column expressions.
- Automated data correction.
- Incident creation and management.
- REST API and graphical interface.
- Background processing.
- LLM integration.
- External data-quality frameworks.

## 3. Core domain concepts

### 3.1 QualityRule

A quality rule defines an expectation about a dataset.

Minimum attributes:

- `id`: UUID.
- `dataset_id`: associated dataset.
- `name`: human-readable rule name.
- `rule_type`: supported rule identifier.
- `column_name`: target column, where applicable.
- `parameters`: validated rule-specific configuration.
- `severity`: INFO, WARNING, ERROR or CRITICAL.
- `enabled`: whether the rule participates in assessments.
- `created_at`: UTC timestamp.
- `updated_at`: UTC timestamp.

Rules must be associated with an existing dataset.

Rule names should be unique within a dataset.

Rule parameters must be validated according to the selected rule type.

Updating a rule must not modify previously recorded assessment results.

### 3.2 QualityAssessment

A quality assessment represents the evaluation of a particular dataset version against a particular enabled ruleset.

Minimum attributes:

- `id`: UUID.
- `dataset_version_id`: evaluated dataset version.
- `ruleset_fingerprint`: deterministic fingerprint of the evaluated ruleset.
- `rules_snapshot`: complete snapshot of the enabled rule definitions and parameters used.
- `status`: PASS, FAIL or ERROR.
- `total_rules`: number of evaluated rules.
- `passed_rules`: number of passing rules.
- `failed_rules`: number of failing rules.
- `error_rules`: number of rules that could not be evaluated.
- `evaluated_at`: UTC timestamp.

An assessment must be reproducible and immutable after completion.

The same dataset version evaluated against the same ruleset fingerprint should return the existing assessment instead of creating a duplicate.

A changed ruleset must produce a different assessment.

### 3.3 QualityFinding

A finding records the outcome of an individual rule evaluation.

Minimum attributes:

- `id`: UUID.
- `assessment_id`: associated assessment.
- `rule_id`: associated rule, where available.
- `rule_snapshot`: rule definition used during evaluation.
- `status`: PASS, FAIL or ERROR.
- `severity`: INFO, WARNING, ERROR or CRITICAL.
- `evaluated_records`: number of records evaluated, where applicable.
- `violating_records`: number of records violating the rule, where applicable.
- `violation_percentage`: percentage of evaluated records violating the rule, where applicable.
- `evidence`: structured observed values and supporting statistics.
- `message`: human-readable explanation.

Every evaluated rule must produce exactly one finding, including successful evaluations.

Do not silently omit passing rules.

## 4. Supported rules

Implement the following initial rule types.

| Rule type | Description | Example |
|---|---|---|
| `required_column` | Column must exist | `customer_id` |
| `data_type` | Column must have expected type | `customer_age: Int64` |
| `not_null` | Column cannot contain nulls | `customer_id` |
| `null_percentage` | Null percentage must not exceed threshold | `email <= 5%` |
| `unique` | Values must be unique | `transaction_id` |
| `min_value` | Numeric minimum | `amount >= 0` |
| `max_value` | Numeric maximum | `age <= 100` |
| `range` | Numeric inclusive range | `age: 18–100` |
| `accepted_values` | Values must belong to an allowed set | `status` |
| `duplicate_rows` | Entire duplicate records are prohibited | All columns |

### Rule semantics

- Required-column checks must work even when the column does not exist.
- Null checks must distinguish nulls from floating-point NaN values.
- Unique checks must explicitly define their null treatment. Use nulls as a single distinct value for uniqueness evaluation.
- Numeric rules must reject incompatible data types with an ERROR finding.
- Range bounds are inclusive.
- Accepted-values checks must have explicit null treatment. Null values should not count as accepted values; users can define a separate `not_null` rule when necessary.
- Duplicate-row detection must operate across the complete row, not just individual columns.
- An empty dataset must not automatically pass every rule. Define and document the evaluation semantics for zero records.
- A missing target column must produce an ERROR finding rather than silently passing.

Do not silently coerce incompatible values into expected types.

## 5. Rule configuration

Rules must be configurable through application services and persisted in PostgreSQL.

Do not hard-code expectations into the profiler or ingestion pipeline.

Rule creation and updating must validate:

- Rule type.
- Required parameters.
- Parameter types and bounds.
- Column-name requirements.
- Severity.
- Dataset association.

Use clear, validated application contracts.

Rule-specific parameters must not be accepted as arbitrary unvalidated dictionaries.

The exact internal representation may be chosen by the implementation, provided these contracts are enforced.

## 6. Evaluation engine

Create a rule evaluation architecture that separates:

1. Rule definition and validation.
2. Rule selection.
3. Rule evaluation.
4. Finding construction.
5. Assessment aggregation.
6. Persistence.

Each supported rule type should have an identifiable evaluation implementation.

Avoid one enormous conditional function containing every rule's logic.

Use Polars for data evaluation.

The engine should avoid repeatedly scanning or collecting the entire dataset unnecessarily where computations can be combined.

Do not compromise correctness to optimise prematurely.

## 7. Assessment status

An assessment has one overall status:

- PASS: all rules passed.
- FAIL: at least one rule failed.
- ERROR: no rules failed, but at least one rule could not be evaluated.

Precedence:

`FAIL > ERROR > PASS`

An empty ruleset should produce a clearly documented result rather than being silently interpreted as meaningful evidence of data quality.

The assessment should also contain counts of passing, failing and errored rules.

Severity is independent of evaluation status.

A WARNING-severity rule can fail without changing its evaluation status from FAIL.

## 8. Reproducibility and idempotency

Calculate a deterministic ruleset fingerprint from the canonical representation of enabled rule definitions.

The fingerprint must include all parameters and severity settings that affect evaluation.

The rules snapshot stored in an assessment must preserve the exact definitions evaluated.

For a given dataset version and ruleset fingerprint:

- Return the existing completed assessment if one exists.
- Do not create duplicate findings.
- A changed rule definition or enabled-rule set must result in a different fingerprint.

Use database constraints and transactional protection to prevent duplicate assessments under concurrent execution.

Assessment results must remain unchanged if the underlying rule definitions are subsequently updated or disabled.

## 9. Persistence

Extend the existing PostgreSQL schema with appropriate entities and relationships.

At minimum, persist:

- Quality rules.
- Quality assessments.
- Quality findings.

Use SQLAlchemy 2.x and Alembic.

Create a new migration. Do not modify the existing Phase 1 migration.

Enforce referential integrity and appropriate uniqueness constraints.

Assessments and findings must be immutable after successful completion.

Use appropriate indexes for retrieval by dataset, dataset version, assessment and rule.

## 10. Application services

Expose functionality through application services.

Conceptually, support:

- Create a quality rule.
- Retrieve rules for a dataset.
- Update a rule.
- Enable or disable a rule.
- Delete a rule if it has no historical dependency, or preserve it through an appropriate lifecycle strategy.
- Evaluate a dataset version.
- Retrieve an assessment.
- Retrieve findings for an assessment.
- Retrieve assessment history for a dataset.

Do not implement FastAPI or a CLI in this phase.

Do not expose SQLAlchemy models directly as public application contracts.

## 11. Error handling

Define meaningful errors for:

- Invalid rule definitions.
- Unsupported rule types.
- Invalid parameters.
- Missing datasets.
- Missing dataset versions.
- Incompatible column types.
- Evaluation failures.
- Persistence failures.
- Duplicate or conflicting operations.

An individual rule evaluation failure should produce an ERROR finding without silently discarding the other rule results.

Unexpected infrastructure failures must be distinguished from ordinary rule evaluation errors.

## 12. Testing strategy

Use pytest and the existing dedicated PostgreSQL test database.

### Unit tests

Test:

- Rule configuration validation.
- Rule parameter boundaries.
- Every supported rule type.
- Null and NaN semantics.
- Empty datasets.
- Missing columns.
- Incompatible data types.
- Severity handling.
- Assessment status aggregation.
- Ruleset fingerprint determinism.
- Finding construction.

### Integration tests

Test:

- Rule creation and retrieval.
- Rule updates and enable/disable operations.
- Rule persistence.
- Evaluation of a dataset version.
- Assessment and finding persistence.
- Assessment retrieval.
- Assessment history.
- Idempotent assessment creation.
- Changed ruleset producing a new assessment.
- Historical assessment preservation after rule updates.
- Concurrent assessment creation.
- Database constraints and migration from the Phase 1 schema.

Use deterministic fixture datasets and temporary storage.

Never run destructive test operations against the development database.

## 13. Acceptance criteria

Phase 2 is complete when:

1. All ten specified rule types work.
2. Rules can be created, retrieved, updated and enabled/disabled.
3. Rules are persisted and associated with datasets.
4. Dataset versions can be evaluated against enabled rules.
5. Every evaluated rule produces a structured finding.
6. Assessment status and rule counts are correct.
7. Findings preserve useful evidence.
8. Ruleset fingerprints are deterministic.
9. Duplicate assessments are prevented.
10. Historical assessments remain unchanged after rule updates.
11. PostgreSQL persistence and Alembic migration work.
12. Unit and integration tests pass.
13. Ruff, formatting and mypy checks pass.
14. Documentation explains supported rules and their semantics.

## 14. Deliverables

- Quality-rule domain models and contracts.
- Rule validation.
- Rule evaluation engine.
- Implementations of all ten rule types.
- Assessment aggregation.
- Finding construction.
- Ruleset fingerprinting.
- PostgreSQL persistence.
- Alembic migration.
- Application services.
- Unit and integration tests.
- Documentation and usage examples.

Do not implement Phase 3 functionality as part of this assignment.
