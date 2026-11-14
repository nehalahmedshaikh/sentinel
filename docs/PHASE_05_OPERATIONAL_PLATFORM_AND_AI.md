# Phase 5 — Operational Platform and AI

## Objective

Turn Sentinel's existing dataset, quality, reliability and incident intelligence capabilities into a usable operational application.

Phase 5 introduces an API, durable background execution, scheduled monitoring, an operator dashboard, observability and evidence-grounded AI investigation.

The existing domain services remain the source of business logic. The new application layer must orchestrate and expose those services rather than reimplement them.

## Existing foundations

The following capabilities are already implemented:

- Dataset registration, ingestion, versioning, fingerprinting, schema inference and profiling.
- Persistent quality rules and immutable quality assessments.
- Reliability policies, schema drift, distribution drift and anomaly detection.
- Deterministic investigation and historical reliability assessments.
- Incident generation, severity, correlation, evidence, lifecycle events and reports.
- PostgreSQL persistence and Alembic migrations.

These capabilities must remain backward-compatible.

## 1. Application API

Build a FastAPI application with versioned API routes.

The API must expose operations for:

- Dataset registration, ingestion and retrieval.
- Dataset versions and profiling information.
- Quality rule management and quality assessment history.
- Reliability policy management and reliability assessment history.
- Incident listing, retrieval, lifecycle operations, evidence, events and reports.
- Background job submission and status retrieval.
- Monitoring schedule creation, retrieval, modification, pausing and deletion.
- AI investigation requests and investigation results.

Use Pydantic request and response schemas. Keep HTTP route handlers thin. They must call existing application/domain services rather than duplicate their logic.

Long-running operations must be submitted as background jobs and return a job identifier.

Provide consistent error responses, request validation, pagination where appropriate, and API documentation through FastAPI's OpenAPI support.

## 2. Background execution

Use Celery with Redis as the broker.

The API process must not execute long-running ingestion, assessment or investigation work synchronously.

Implement background tasks for:

- Dataset ingestion.
- Quality assessment.
- Reliability assessment.
- Incident generation or reconciliation following assessments.
- Scheduled monitoring runs.
- AI investigation where configured.

### Persistent job management

PostgreSQL is the authoritative record of job state and history.

Persist at least:

- Job identifier.
- Job type.
- Relevant dataset, version, policy or incident references.
- Status.
- Creation, start and completion timestamps.
- Progress information where meaningful.
- Attempt count.
- Error classification and safe error details.
- Result references or a compact result summary.
- Idempotency key or equivalent deduplication identity.

Use a clear lifecycle:

`QUEUED → RUNNING → SUCCEEDED / FAILED`

Support retries for transient failures, bounded retry policies, and recovery of jobs left in RUNNING after worker interruption.

Celery task messages should carry persistent job identifiers. Do not make Celery's result backend the authoritative job-history store.

Tasks must be idempotent. Duplicate delivery must not create duplicate dataset versions, assessments, incidents or scheduled runs.

Use a transactional outbox or another explicitly reliable dispatch mechanism to avoid losing jobs between PostgreSQL commit and broker publication.

Configure conservative worker concurrency suitable for a resource-constrained development machine.

## 3. Scheduled monitoring

Use Celery Beat to trigger a periodic schedule-dispatch task.

Persist monitoring schedules in PostgreSQL. A schedule should identify:

- Dataset.
- Applicable reliability policy and quality configuration.
- Frequency or interval.
- Enabled/paused state.
- Next scheduled execution.
- Last execution and outcome.
- Creation and modification timestamps.

The periodic dispatcher should identify due schedules and enqueue their work as persistent jobs.

Ensure that concurrent dispatcher executions cannot create duplicate runs for the same schedule occurrence.

Support creating, viewing, updating, pausing, resuming and deleting schedules.

Do not introduce APScheduler or another independent scheduling framework.

## 4. Operator dashboard

Build a server-rendered dashboard using Jinja2 templates and HTMX.

Do not introduce React, Vue, Angular, Node.js, npm or a separate frontend application.

The dashboard should provide:

### Overview
- Dataset count and recent activity.
- Recent quality and reliability assessments.
- Open incidents by severity.
- Recent job failures and currently running jobs.
- Monitoring schedule status.

### Datasets
- Dataset inventory.
- Dataset details and version history.
- Schema and profiling summaries.
- Ingestion interface with clear validation feedback.

### Quality
- Quality rules and their configuration.
- Assessment history.
- Findings, violations and evidence.

### Reliability
- Reliability policies and configuration.
- Assessment history.
- Schema and distribution drift findings.
- Anomaly summaries and affected columns.

### Incidents
- Incident list with filtering and severity.
- Incident detail and lifecycle state.
- Evidence and event timeline.
- Deterministic incident report.

### Operations
- Job queue/history and job details.
- Monitoring schedules and their execution history.
- Clear status and failure information.

### AI investigations
- Request an investigation of a selected incident.
- Display the generated explanation and referenced evidence.
- Clearly distinguish observed facts, evidence-backed interpretations and uncertainty.

Use responsive styling and a consistent application layout. Use lightweight SVG/CSS visualizations initially; do not add a charting framework without a concrete need.

## 5. Evidence-grounded AI investigation

Implement a small provider abstraction using HTTPX and Pydantic contracts.

Support configurable OpenAI-compatible endpoints, including compatible hosted services and Ollama-compatible endpoints.

AI must be optional and disabled by default.

The AI component must:

1. Receive an explicit investigation request.
2. Retrieve relevant evidence through approved Sentinel services.
3. Send structured evidence and a constrained instruction to the configured model.
4. Validate the response against a defined Pydantic schema.
5. Persist the explanation, evidence references, provider/model metadata and timestamps.
6. Make the result retrievable through the API and dashboard.

The response should distinguish:

- Verified observations.
- Evidence-supported hypotheses.
- Uncertainty and missing evidence.
- Suggested next investigative steps.

AI must not invent measurements, findings, records or causal relationships. Every factual claim about Sentinel data must refer to available evidence.

The model must not receive unrestricted database access or arbitrary code execution. It must not modify rules, policies, datasets or incident lifecycle state. AI investigation is read-only.

If AI is disabled, unavailable, times out or returns invalid output, deterministic Sentinel functionality must continue to work. Persist an appropriate failure state without losing the underlying incident or assessment.

Do not introduce LangChain, CrewAI, AutoGen or another agent framework.

## 6. Authentication and application safety

This phase targets a single local operator.

- Bind to `127.0.0.1` by default.
- Protect operational API routes with a configurable API key.
- Keep health endpoints minimally informative and safe.
- Restrict CORS to configured origins.
- Validate uploaded file types and impose configurable upload-size limits.
- Store uploaded files using the existing managed storage conventions.
- Never expose secrets, credentials or sensitive filesystem paths in responses or logs.
- Do not introduce multi-tenant RBAC or OAuth in this phase.

The application must not default to public network exposure.

## 7. Observability

Implement:

- Structured application logs.
- Health and readiness endpoints.
- Prometheus-compatible metrics.
- Job execution counters and durations.
- Job failure and retry metrics.
- Assessment and incident processing metrics.
- Basic application and dependency diagnostics.

Metrics must not expose secrets or unbounded high-cardinality labels.

## 8. Configuration and operation

Centralize application configuration and validate it at startup.

Document the commands required to run:

- The API.
- The Celery worker.
- Celery Beat.
- Tests and database migrations.

Provide a development configuration example without real credentials.

The system must run without Docker and without an installed local language model.

## 9. Testing

Add unit, integration and API tests for:

- API validation, authentication and error handling.
- Dataset and assessment operations through the API.
- Job creation, state transitions, failure and retry behavior.
- Duplicate task delivery and idempotency.
- Worker interruption and stale-job recovery.
- Scheduled execution and duplicate-dispatch prevention.
- Dashboard routes and essential workflows.
- AI provider configuration, schema validation, timeout and failure behavior.
- Evidence references and read-only AI constraints.
- Health checks and metrics.

Use the existing PostgreSQL test database and migration workflow. Do not silently skip database integration tests.

Tests must not require a paid AI service or a locally installed model. Use deterministic fake/mock providers.

## 10. Documentation

Add:

- API and application usage documentation.
- Background execution and scheduling documentation.
- Dashboard usage documentation.
- AI provider configuration and safety documentation.
- Operational troubleshooting guidance.
- An architectural decision record for this phase.

Update the README with the new capabilities and local run instructions.

## Explicit non-goals

Do not implement:

- Autonomous remediation or automatic data correction.
- External ticketing integrations.
- Email, SMS or chat notifications.
- Multi-tenant account management.
- OAuth or enterprise identity management.
- Kubernetes or distributed deployment.
- A separate JavaScript frontend.
- A local LLM installation requirement.
- A general-purpose autonomous agent framework.

## Acceptance criteria

Phase 5 is complete when:

1. Sentinel can be operated through a documented API.
2. Long-running operations execute through Celery workers.
3. Job state and history survive process restarts in PostgreSQL.
4. Scheduled monitoring reliably creates persistent jobs without duplicate runs.
5. The dashboard exposes the core dataset, quality, reliability, incident and operations workflows.
6. AI investigation works through a configurable provider interface and remains optional.
7. AI explanations reference actual Sentinel evidence and cannot mutate Sentinel state.
8. Authentication, upload validation, health checks and metrics are implemented.
9. Tests cover the new application and operational workflows.
10. Existing Phase 1–4 behavior and tests remain intact.
11. Documentation and development run instructions are complete.
12. Ruff, formatting, mypy, lockfile checks and the complete test suite pass.

## Implemented behavior and local operation

The implementation is organized under `sentinel.api`, `sentinel.operations`, `sentinel.dashboard`, and `sentinel.ai`. API and dashboard handlers call the existing Phase 1–4 services. Long-running submissions create a `jobs` row and `job_outbox` row in one PostgreSQL transaction. Celery Beat periodically publishes pending outbox rows, dispatches due schedules, and recovers stale running jobs. Celery's result backend is not used; query PostgreSQL through `/api/v1/jobs` or the operator UI.

The Phase 5 Alembic revision is `0006_operational_platform`, after `0005_incident_finding_category`. Apply it only after selecting the intended database:

```bash
uv run alembic upgrade head
uv run alembic current
```

For integration testing, set both `SENTINEL_DATABASE_URL` and `SENTINEL_TEST_DATABASE_URL` to the dedicated `sentinel_test` database. The test fixtures query `current_database()` and fail before touching records if the name differs. They preserve existing rows and use unique dataset/job identifiers.

Copy `.env.example` to `.env`, set database URLs, managed data directory, a long random `SENTINEL_API_KEY`, and Redis URL. Start the processes independently:

```bash
uv run uvicorn sentinel.api.app:app --host 127.0.0.1 --port 8000
uv run celery -A sentinel.operations.celery_app:celery_app worker --loglevel=INFO --concurrency=1
uv run celery -A sentinel.operations.celery_app:celery_app beat --loglevel=INFO
```

The operator dashboard is at `/`. Operational API routes are under `/api/v1` and require `Authorization: Bearer <SENTINEL_API_KEY>`. `/health` reports process liveness; `/ready` checks PostgreSQL and Redis; `/metrics` exposes bounded-label PostgreSQL-derived counters and job-duration buckets. The dashboard is local-first and does not have separate user authentication.

Dataset uploads use `POST /api/v1/datasets/{dataset_key}/upload` as multipart form data with `file`, `display_name`, and optional `description`. They return a persistent job. The upload is size limited and extension/content checked, first staged in a random managed `incoming` directory, and removed after successful processing or a terminal failure. A transiently retried job retains its staged source. Never point the managed data directory at an untrusted shared location.

Quality work is queued with `POST /api/v1/quality/versions/{version_id}/assessments`; reliability work uses `POST /api/v1/reliability/assessments` with a validated `ReliabilityAssessmentInput`. Incident generation is queued with `POST /api/v1/incidents/generate` and an `IncidentGenerationInput` containing source type, assessment ID, and incident policy ID. These operations reuse their existing application services, which preserve assessment and incident idempotency. Schedule dispatch runs quality against the latest version and optionally uses its selected reliability and incident policies. Each scheduled occurrence has a unique `schedule:{schedule_id}:{occurrence}` key and a persistent history row. Deletion is a soft delete so execution history remains available to existing references.

Incident policies already belong to the Phase 4 `IncidentPolicyRow` and `IncidentService`; Phase 5 exposes that service rather than defining a second policy model. List policies with `GET /api/v1/incidents/datasets/{dataset_key}/policies`, create with `POST` on that collection, update with `PUT /api/v1/incidents/policies/{policy_id}`, and enable/disable with `POST /api/v1/incidents/policies/{policy_id}/enabled?enabled=true|false`. A minimal create body such as `{"name":"default"}` uses the existing `IncidentPolicyConfig` defaults, including the documented quality/reliability source severity thresholds. Policies remain dataset scoped, and disabled policies are not offered by the dashboard generation forms. On a dataset detail page, operators can discover and manage policies, inspect persisted quality/reliability assessment history, and queue incident generation by selecting a source assessment and an enabled policy. The dashboard handler validates that both persisted records belong to that dataset before enqueueing the same idempotent job used by the API. No migration is needed for these routes because Phase 4 already persists incident policies.

In the incident list, the “Any dataset” option is disabled so browsers omit `dataset_id` from the GET query entirely; this avoids sending an empty string to the UUID-validated filter.

The dashboard supports registration inventory, upload, version/profile views, quality rule creation, reliability policy configuration, manual assessment submission, incident filtering and lifecycle actions, deterministic report display, job details, schedule pause/resume/deletion/history, and optional AI investigation. Dashboard reliability policy configuration accepts the same JSON shape as `DetectionPolicyInput.config`; the more complete rule and policy controls remain available through the API.

For AI, set `SENTINEL_AI_ENABLED=true`, `SENTINEL_AI_BASE_URL`, and `SENTINEL_AI_MODEL`; set `SENTINEL_AI_API_KEY` when the endpoint requires it. A base origin is expanded to `/v1/chat/completions`; a base URL ending in `/v1` receives `/chat/completions`. The worker sends at most 50 evidence summaries with a fixed allowlist of metrics and no dataset records. The provider response is validated and every observation/hypothesis must cite an evidence UUID present in that submitted payload. Results and failure classification are stored in `ai_investigations`; AI cannot write Sentinel records other than its own result and job state.

Troubleshooting:

- Jobs remain queued: check Redis connectivity and that the Beat process is publishing the PostgreSQL outbox; the outbox rows remain pending for later retry.
- Jobs repeatedly retry: inspect their `error_class`, broker/database connectivity, and worker logs; retries are bounded by `SENTINEL_TASK_MAX_RETRIES` and `SENTINEL_TASK_RETRY_DELAY_SECONDS`.
- A job remains running after worker termination: the Beat recovery task requeues jobs older than `SENTINEL_STALE_JOB_SECONDS`.
- Readiness fails while liveness passes: verify the database URL and Redis URL independently.
- AI investigations fail: verify AI enablement, endpoint/model configuration, provider reachability, and the persisted failure class. Deterministic assessment and incident results are not changed by AI provider failures.

The initial dashboard loads HTMX from its configured CDN, so polling and partial updates require browser network access to that asset. Server-rendered pages and form workflows remain available without JavaScript. The API, worker, Redis broker, and PostgreSQL are local development components; this release is not a public internet hardened multi-user deployment.
