"""Deterministic profile generation using Polars."""

import hashlib
import json
import math

import polars as pl

from sentinel.domain.contracts import ColumnProfile, ColumnSchema, DataProfile


def infer_schema(frame: pl.DataFrame) -> list[ColumnSchema]:
    """Represent the frame schema in source column order."""
    return [
        ColumnSchema(name=name, data_type=str(dtype), position=index)
        for index, (name, dtype) in enumerate(frame.schema.items())
    ]


def fingerprint_schema(schema: list[ColumnSchema]) -> str:
    """Hash the canonical ordered schema representation."""
    canonical = json.dumps(
        [column.model_dump(mode="json") for column in schema],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_profile(frame: pl.DataFrame) -> DataProfile:
    """Calculate bounded, exact descriptive statistics for a frame."""
    rows, columns = frame.shape
    profiles: list[ColumnProfile] = []
    for name, dtype in frame.schema.items():
        series = frame.get_column(name)
        null_count = series.null_count()
        distinct = series.n_unique()
        values: dict[str, int | float | None] = {}
        if dtype.is_numeric():
            clean = series.drop_nulls()
            if dtype in (pl.Float32, pl.Float64):
                clean = clean.filter(clean.is_finite())
            minimum, maximum = clean.min(), clean.max()
            mean, std = clean.mean(), clean.std(ddof=1)
            values = {
                "minimum": _finite_number(minimum),
                "maximum": _finite_number(maximum),
                "mean": _finite_number(mean),
                "standard_deviation": _finite_number(std),
            }
        profiles.append(
            ColumnProfile(
                name=name,
                data_type=str(dtype),
                null_count=null_count,
                null_percentage=(null_count / rows * 100) if rows else 0.0,
                distinct_count=distinct,
                **values,
            )
        )
    missing = sum(item.null_count for item in profiles)
    return DataProfile(
        row_count=rows,
        column_count=columns,
        total_missing_values=missing,
        missing_percentage=(missing / (rows * columns) * 100) if rows and columns else 0.0,
        columns=profiles,
    )


def _finite_number(value: object) -> int | float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    if number.is_integer() and isinstance(value, int):
        return value
    return number
