# Instructions for Coding Agents

## 1. Project context

Sentinel is an autonomous data reliability and incident investigation platform.

Its purpose is to ingest datasets, profile their contents, detect data quality problems, anomalies and drift, investigate incidents using verifiable evidence, and manage the resulting incidents.

Read `README.md` and `docs/PROJECT_SPEC.md` before beginning work.

## 2. General engineering principles

- Build maintainable software, not throwaway demonstrations.
- Prefer simple, explicit designs over unnecessary abstractions.
- Maintain clear separation of concerns.
- Use type annotations throughout application code.
- Validate inputs at system boundaries.
- Make failures explicit and informative.
- Prefer deterministic behaviour wherever practical.
- Preserve reproducibility of analytical operations.
- Write tests alongside implementation.
- Document non-obvious decisions and trade-offs.

## 3. Architectural constraints

- Statistical calculations and detection must be deterministic and independently testable.
- LLMs must never be treated as the source of statistical truth.
- AI-generated explanations must be grounded in verified analytical results.
- Keep ingestion, profiling, detection, investigation, persistence, orchestration and API concerns separate.
- Avoid coupling domain logic to infrastructure implementations.
- Do not introduce frameworks or dependencies without a clear requirement.
- Do not prematurely implement future roadmap functionality.

## 4. Implementation discipline

For each assigned task:

1. Inspect the existing repository and relevant documentation.
2. Understand the existing architecture before changing it.
3. Identify the files and interfaces affected.
4. Implement only the requested scope.
5. Add or update appropriate tests.
6. Run relevant tests and quality checks.
7. Report changed files, decisions, test results and outstanding concerns.

Do not rewrite unrelated components.

Do not silently change established interfaces or architectural decisions.

If a task requires an architectural decision that has not been made, explain the alternatives and ask for clarification rather than inventing a consequential design.

## 5. Dependencies

- Use `uv` for dependency management.
- Add dependencies only when justified by the current task.
- Prefer established, maintained libraries.
- Do not install dependencies globally.
- Do not introduce overlapping libraries without a documented reason.
- Update `pyproject.toml` and `uv.lock` together.
- Explain the purpose of every newly introduced runtime dependency.

## 6. Testing

- Use pytest.
- Test normal behaviour, edge cases and failure conditions.
- Test statistical calculations against known expected results.
- Prefer deterministic tests.
- Avoid tests that depend on network access or external services unless explicitly required.
- Do not weaken or remove tests merely to make an implementation pass.
- Do not claim tests passed unless they were actually executed.

## 7. Code quality

- Follow Ruff linting and formatting configuration.
- Maintain strict type checking with mypy.
- Prefer descriptive names and small, cohesive functions.
- Avoid unnecessary comments that merely restate the code.
- Document public interfaces and non-obvious behaviour.
- Avoid broad exception handling that conceals failures.

## 8. Git discipline

- Keep changes focused.
- Do not rewrite Git history.
- Do not create commits unless explicitly requested.
- Never commit secrets, credentials, private datasets or generated artifacts.
- Report any unrelated pre-existing modifications before changing files.

## 9. Communication

At the end of each task, report:

- What was implemented.
- Important design decisions.
- Files created or modified.
- Tests and quality checks executed, including their results.
- Known limitations.
- Any unresolved architectural questions.

Do not claim functionality exists merely because it is planned.
