"""Validated runtime configuration shared by API and workers."""

from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sentinel.config import data_directory, database_url


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    environment: str = "development"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    database_url: str
    test_database_url: str | None = None
    data_dir: str
    redis_url: str = "redis://127.0.0.1:6379/0"
    celery_concurrency: int = Field(default=1, ge=1, le=32)
    api_key: str = Field(min_length=16, repr=False)
    upload_max_bytes: int = Field(default=50_000_000, ge=1_000, le=2_000_000_000)
    allowed_file_types: tuple[str, ...] = ("csv", "parquet", "json", "jsonl", "ndjson")
    cors_origins: tuple[str, ...] = ()
    ai_enabled: bool = False
    ai_base_url: str | None = None
    ai_api_key: str | None = Field(default=None, repr=False)
    ai_model: str | None = None
    task_max_retries: int = Field(default=3, ge=0, le=10)
    task_retry_delay_seconds: int = Field(default=10, ge=1, le=3600)
    schedule_dispatch_seconds: int = Field(default=30, ge=5, le=3600)
    outbox_dispatch_seconds: int = Field(default=2, ge=1, le=300)
    stale_job_seconds: int = Field(default=900, ge=60, le=86400)

    @field_validator("allowed_file_types", mode="before")
    @classmethod
    def parse_file_types(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(
                item.strip().lower().lstrip(".") for item in value.split(",") if item.strip()
            )
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value

    @model_validator(mode="after")
    def validate_ai(self) -> Settings:
        if self.ai_enabled and (not self.ai_base_url or not self.ai_model):
            raise ValueError("AI requires SENTINEL_AI_BASE_URL and SENTINEL_AI_MODEL")
        if any(
            not item.replace(".", "").replace("-", "").isalnum() for item in self.allowed_file_types
        ):
            raise ValueError("allowed file types must be extensions without path separators")
        return self

    @classmethod
    def from_environment(cls) -> Settings:
        import os

        return cls.model_validate(
            {
                "environment": os.getenv("SENTINEL_ENV", "development"),
                "api_host": os.getenv("SENTINEL_API_HOST", "127.0.0.1"),
                "api_port": os.getenv("SENTINEL_API_PORT", "8000"),
                "database_url": database_url(),
                "test_database_url": os.getenv("SENTINEL_TEST_DATABASE_URL"),
                "data_dir": str(data_directory()),
                "redis_url": os.getenv("SENTINEL_REDIS_URL", "redis://127.0.0.1:6379/0"),
                "celery_concurrency": os.getenv("SENTINEL_CELERY_CONCURRENCY", "1"),
                "api_key": os.getenv("SENTINEL_API_KEY", ""),
                "upload_max_bytes": os.getenv("SENTINEL_UPLOAD_MAX_BYTES", "50000000"),
                "allowed_file_types": os.getenv(
                    "SENTINEL_ALLOWED_FILE_TYPES", "csv,parquet,json,jsonl,ndjson"
                ),
                "cors_origins": os.getenv("SENTINEL_CORS_ORIGINS", ""),
                "ai_enabled": os.getenv("SENTINEL_AI_ENABLED", "false").lower()
                in {"1", "true", "yes"},
                "ai_base_url": os.getenv("SENTINEL_AI_BASE_URL") or None,
                "ai_api_key": os.getenv("SENTINEL_AI_API_KEY") or None,
                "ai_model": os.getenv("SENTINEL_AI_MODEL") or None,
                "task_max_retries": os.getenv("SENTINEL_TASK_MAX_RETRIES", "3"),
                "task_retry_delay_seconds": os.getenv("SENTINEL_TASK_RETRY_DELAY_SECONDS", "10"),
                "schedule_dispatch_seconds": os.getenv("SENTINEL_SCHEDULE_DISPATCH_SECONDS", "30"),
                "outbox_dispatch_seconds": os.getenv("SENTINEL_OUTBOX_DISPATCH_SECONDS", "2"),
                "stale_job_seconds": os.getenv("SENTINEL_STALE_JOB_SECONDS", "900"),
            }
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_environment()
