"""Format detection and Polars-backed readers."""

import json
from pathlib import Path

import polars as pl

from sentinel.domain.exceptions import UnsupportedFormatError, ValidationError


def detect_format(path: Path) -> str:
    """Validate and return the supported representation from the suffix."""
    suffix = path.suffix.lower()
    formats = {
        ".csv": "csv",
        ".parquet": "parquet",
        ".json": "json",
        ".jsonl": "jsonl",
        ".ndjson": "jsonl",
    }
    try:
        return formats[suffix]
    except KeyError as exc:
        raise UnsupportedFormatError(f"Unsupported file extension: {suffix or '(none)'}") from exc


def read_frame(path: Path, file_format: str) -> pl.DataFrame:
    """Parse one supported tabular representation into an eager frame."""
    try:
        if file_format == "csv":
            frame = pl.read_csv(path)
        elif file_format == "parquet":
            frame = pl.read_parquet(path)
        elif file_format == "jsonl":
            frame = pl.read_ndjson(path)
        else:
            with path.open("r", encoding="utf-8") as stream:
                value = json.load(stream)
            if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
                raise ValidationError("JSON input must be a top-level array of objects")
            frame = pl.DataFrame(value, infer_schema_length=None)
    except ValidationError:
        raise
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        pl.exceptions.PolarsError,
        ValueError,
    ) as exc:
        raise ValidationError(f"Unable to read {file_format} input: {exc}") from exc
    if frame.width == 0:
        raise ValidationError("Input contains no columns")
    return frame
