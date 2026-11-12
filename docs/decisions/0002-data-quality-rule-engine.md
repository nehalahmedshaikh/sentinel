# ADR 0002: Configurable Data Quality Rule Engine

**Status:** Accepted  
**Date:** 2026-11-12

## 1. Decision: Configurable, dataset-specific rules

Quality expectations will be defined as persistent rules associated with individual datasets.

**Context:** Different datasets have different quality requirements. Hard-coded checks would make Sentinel inflexible and difficult to reuse.

**Consequences:**
- Rules are stored in PostgreSQL.
- Rules are managed through application services.
- Each rule has a defined type, parameters, severity and enabled state.
- Rule definitions are validated before persistence.

## 2. Decision: Separate rules, assessments and findings

These are separate domain concepts.

- QualityRule describes an expectation.
- QualityAssessment records an evaluation against a particular dataset version.
- QualityFinding records an individual rule's result and evidence.

**Rationale:** This separation preserves the distinction between expectations, evaluation events and observed violations.

**Consequences:**
- An assessment produces one finding per evaluated rule.
- Findings preserve the rule definition used during evaluation.
- Assessment history remains meaningful when rules change.

## 3. Decision: Preserve assessment reproducibility

Each assessment stores a snapshot of its evaluated ruleset and a deterministic ruleset fingerprint.

**Rationale:** Updating a rule must not retrospectively change the meaning of an earlier assessment.

**Consequences:**
- Identical dataset versions and ruleset fingerprints produce idempotent assessments.
- Changed rulesets produce distinct assessments.
- Historical findings and assessment results remain immutable.

## 4. Decision: Polars-based evaluation

Use Polars for the initial rule evaluation engine.

**Rationale:** Polars is already the project's processing engine. Introducing another data-quality or DataFrame framework would create unnecessary duplication.

**Consequences:**
- Rule evaluation implementations use Polars internally.
- Domain contracts remain independent of Polars-specific objects.
- Evaluation logic must be independently testable.

## 5. Decision: Explicit evaluation statuses and severity

Evaluation status and severity are independent.

Evaluation statuses:

- PASS
- FAIL
- ERROR

Severity levels:

- INFO
- WARNING
- ERROR
- CRITICAL

**Rationale:** A rule's outcome and its operational importance represent different dimensions.

**Consequences:**
- A warning-severity rule can fail without being an ERROR.
- An evaluation error is not treated as a successful check.
- Assessment aggregation follows the precedence defined in the Phase 2 specification.

## 6. Decision: Defer cross-column expressions

Cross-column rules and arbitrary expressions are excluded from Phase 2.

**Rationale:** They require a more general expression model, validation strategy and execution-safety considerations.

**Consequences:** Phase 2 focuses on a well-defined set of column-level and dataset-level rules. Cross-column evaluation can be designed separately when required.
