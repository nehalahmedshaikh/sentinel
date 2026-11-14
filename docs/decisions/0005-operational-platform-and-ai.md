# ADR 0005: Operational Platform and AI

- **Status:** Accepted
- **Date:** 2026-11-13

## Context

Sentinel has completed its foundational dataset, quality, reliability and incident intelligence capabilities.

The next requirement is to expose those capabilities as an operational application with an API, durable background execution, scheduled monitoring, a dashboard and optional AI-assisted investigation.

The architecture should remain Python-first, resource-conscious and maintainable. It should avoid introducing unnecessary frontend infrastructure, heavyweight agent frameworks or mandatory local model inference.

## Decisions

### 1. API: FastAPI

FastAPI provides the HTTP interface, request validation, response schemas and OpenAPI documentation.

The API is an application boundary, not a replacement for the existing service layer. Existing services remain responsible for domain behavior.

### 2. Background execution: Celery and Redis

Celery executes long-running operations independently of API requests. Redis acts as the broker.

PostgreSQL remains authoritative for persistent job records, execution state and history. Celery's result backend is not the source of truth for Sentinel jobs.

Tasks must be idempotent and support bounded retries and interrupted-worker recovery.

### 3. Scheduling: Celery Beat and PostgreSQL

Celery Beat periodically triggers a scheduler-dispatch task.

Individual monitoring schedules are stored in PostgreSQL. The dispatcher identifies due schedules and creates persistent jobs.

No separate scheduler framework is introduced.

### 4. Frontend: Jinja2 and HTMX

The operator dashboard uses server-rendered Jinja2 templates with HTMX for partial updates and interactive workflows.

No separate React/Vue application or Node.js build system is introduced in this phase.

Styling and visualizations remain lightweight and integrated with the Python application.

### 5. AI integration: provider adapter using HTTPX

AI inference is accessed through a small provider abstraction using HTTPX and structured Pydantic contracts.

The interface supports OpenAI-compatible endpoints, including compatible hosted services and Ollama-compatible endpoints.

AI is optional and disabled by default. No local model installation is required.

The AI component interprets evidence obtained through approved Sentinel services. It does not have unrestricted database access, arbitrary code execution or permission to mutate Sentinel state.

No agent framework is introduced.

### 6. Authentication: single-operator API key

The initial deployment is local-first and single-operator.

Operational API routes require a configurable API key. The API binds to localhost by default. Multi-tenant RBAC and OAuth are outside this phase.

### 7. Observability: standard logging and Prometheus metrics

Use structured application logging, health/readiness endpoints and `prometheus-client`.

Do not introduce a mandatory external monitoring stack.

### 8. Deployment: local processes

The initial supported operating model consists of:

- FastAPI/Uvicorn.
- Celery worker.
- Celery Beat.
- PostgreSQL.
- Redis.

Docker and distributed deployment are deferred.

## Consequences

### Positive

- Reuses the existing Python domain and persistence layers.
- Keeps the frontend and backend in one application ecosystem.
- Supports durable asynchronous work and scheduled monitoring.
- Separates job history from transient broker state.
- Makes AI replaceable and optional.
- Keeps local development feasible on resource-constrained hardware.
- Avoids unnecessary infrastructure and frontend maintenance.

### Trade-offs

- Server-rendered pages are less suited to highly interactive, client-heavy interfaces than a dedicated SPA.
- Celery and Redis introduce operational processes that must be managed.
- The database-backed scheduling dispatcher requires careful concurrency and idempotency controls.
- OpenAI-compatible provider support does not guarantee identical behavior across model providers; response validation remains necessary.
- Single-operator API-key authentication is not a substitute for a full public-facing security architecture.

## Deferred decisions

The following remain outside this ADR:

- Multi-user and multi-tenant access control.
- Public internet deployment and production-grade identity management.
- External notification and ticketing systems.
- Autonomous remediation.
- Dedicated frontend application.
- Local model hosting and GPU infrastructure.
- Distributed orchestration and container deployment.
