"""Validated, infrastructure-independent dataset contracts."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DatasetInput(BaseModel):
    """Explicit dataset registration details."""

    model_config = ConfigDict(str_strip_whitespace=True)
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    display_name: str = Field(min_length=1, max_length=250)
    description: str | None = None


class ColumnSchema(BaseModel):
    """One ordered column in an inferred schema."""

    name: str
    data_type: str
    position: int = Field(ge=0)


class ColumnProfile(BaseModel):
    """Descriptive statistics for one column."""

    name: str
    data_type: str
    null_count: int = Field(ge=0)
    null_percentage: float
    distinct_count: int = Field(ge=0)
    minimum: int | float | None = None
    maximum: int | float | None = None
    mean: float | None = None
    standard_deviation: float | None = None


class DataProfile(BaseModel):
    """Reproducible dataset-level profile."""

    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    total_missing_values: int = Field(ge=0)
    missing_percentage: float
    columns: list[ColumnProfile]


class DatasetView(BaseModel):
    """Public dataset representation, independent of ORM classes."""

    id: UUID
    slug: str
    display_name: str
    description: str | None
    created_at: datetime
    updated_at: datetime


class DatasetVersionView(BaseModel):
    """Public immutable version representation."""

    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    dataset_id: UUID
    version_number: int
    source_filename: str
    format: str
    content_sha256: str
    file_size_bytes: int
    storage_key: str
    schema_: list[ColumnSchema] = Field(validation_alias="schema", serialization_alias="schema")
    schema_fingerprint: str
    row_count: int
    column_count: int
    profile: DataProfile
    ingested_at: datetime


class Registration(BaseModel):
    """Dataset and version result from ingestion."""

    dataset: DatasetView
    version: DatasetVersionView


def schema_as_dicts(columns: list[ColumnSchema]) -> list[dict[str, Any]]:
    """Convert the schema contract to JSON-friendly mappings."""
    return [item.model_dump(mode="json") for item in columns]
