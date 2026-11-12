"""Environment-based application configuration."""

import os
from pathlib import Path


def database_url(*, testing: bool = False) -> str:
    """Return the configured database URL, failing clearly if absent."""
    key = "SENTINEL_TEST_DATABASE_URL" if testing else "SENTINEL_DATABASE_URL"
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"{key} must be configured")
    return value


def data_directory() -> Path:
    """Return the managed storage root."""
    return Path(os.getenv("SENTINEL_DATA_DIR", "~/.local/share/sentinel")).expanduser()
