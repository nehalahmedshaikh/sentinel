# Development Setup

## Requirements

- Python 3.14
- uv
- PostgreSQL 18 or a compatible supported PostgreSQL release

## Python environment

Synchronise dependencies:

```bash
uv sync
```

Run commands through the project environment using `uv run`.

## PostgreSQL

Sentinel uses PostgreSQL for persistent metadata.

The local development setup uses two databases:

- `sentinel`: development database.
- `sentinel_test`: automated integration-test database.

The application must never run destructive test operations against the development database.

## Environment variables

Configure the following variables:

| Variable | Purpose |
|---|---|
| `SENTINEL_DATABASE_URL` | Development database connection |
| `SENTINEL_TEST_DATABASE_URL` | Integration-test database connection |
| `SENTINEL_DATA_DIR` | Managed dataset storage root |

Example local configuration using PostgreSQL peer authentication:

```bash
export SENTINEL_DATABASE_URL="postgresql+psycopg://$USER@/sentinel"
export SENTINEL_TEST_DATABASE_URL="postgresql+psycopg://$USER@/sentinel_test"
export SENTINEL_DATA_DIR="$HOME/.local/share/sentinel"
```

These variables should be supplied through the user's shell environment or an appropriate local configuration mechanism.

Do not commit credentials or machine-specific secrets.

GitHub Actions supplies its own PostgreSQL test database through workflow environment variables.

## Database migrations

Use Alembic for all database schema changes.

Do not modify an existing migration after it has been applied to a shared database. Create a new migration instead.

## Quality checks

Run the complete local quality suite:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest --cov=sentinel --cov-report=term-missing
```

## Development conventions

- Keep application code under `src/sentinel`.
- Keep automated tests under `tests`.
- Use temporary directories for filesystem tests.
- Use the dedicated test database for integration tests.
- Never commit datasets, generated reports, credentials or local database files.
- Document consequential architectural decisions in `docs/decisions/`.
