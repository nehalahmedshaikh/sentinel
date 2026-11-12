"""Unit coverage for dataset foundations."""

import hashlib
from pathlib import Path

import polars as pl
import pytest

from sentinel.domain.contracts import DatasetInput
from sentinel.domain.exceptions import UnsupportedFormatError, ValidationError
from sentinel.ingestion.readers import detect_format, read_frame
from sentinel.profiling.profile import build_profile, fingerprint_schema, infer_schema
from sentinel.storage.filesystem import FileStore


def test_slug_contract_requires_normalized_explicit_slug() -> None:
    assert (
        DatasetInput(slug="customer-transactions", display_name="Customers").slug
        == "customer-transactions"
    )
    with pytest.raises(ValueError):
        DatasetInput(slug="Customer Transactions", display_name="Customers")


def test_format_detection_and_unsupported_format(tmp_path: Path) -> None:
    assert detect_format(tmp_path / "items.NDJSON") == "jsonl"
    with pytest.raises(UnsupportedFormatError, match="Unsupported"):
        detect_format(tmp_path / "items.xlsx")


@pytest.mark.parametrize(
    ("filename", "contents", "expected"),
    [
        ("table.csv", "number,label\n1,a\n2,b\n", ["number", "label"]),
        ("table.json", '[{"number": 1}, {"number": 2}]', ["number"]),
        ("table.jsonl", '{"number":1}\n{"number":2}\n', ["number"]),
    ],
)
def test_read_supported_text_formats(
    tmp_path: Path, filename: str, contents: str, expected: list[str]
) -> None:
    path = tmp_path / filename
    path.write_text(contents, encoding="utf-8")
    frame = read_frame(path, detect_format(path))
    assert frame.columns == expected
    assert frame.height == 2


def test_json_requires_array_of_objects(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"items": []}', encoding="utf-8")
    with pytest.raises(ValidationError, match="top-level array"):
        read_frame(path, "json")


def test_schema_fingerprint_canonical_and_order_sensitive() -> None:
    frame = pl.DataFrame({"a": [1], "b": ["x"]})
    schema = infer_schema(frame)
    assert fingerprint_schema(schema) == fingerprint_schema(infer_schema(frame))
    assert fingerprint_schema(schema) != fingerprint_schema(infer_schema(frame.select("b", "a")))
    assert [item.position for item in schema] == [0, 1]


def test_profile_counts_missing_distinct_and_numeric_metrics() -> None:
    frame = pl.DataFrame(
        {"value": [1.0, 2.0, None, float("nan"), float("inf")], "label": ["a", "a", None, "b", "b"]}
    )
    profile = build_profile(frame)
    assert profile.row_count == 5
    assert profile.column_count == 2
    assert profile.total_missing_values == 2
    value = profile.columns[0]
    assert value.null_count == 1
    assert value.distinct_count == 5
    assert value.minimum == 1
    assert value.maximum == 2
    assert value.mean == 1.5
    assert value.standard_deviation == pytest.approx(0.7071067811865476)


def test_profile_empty_rows_and_empty_numeric_column() -> None:
    frame = pl.DataFrame(schema={"value": pl.Int64})
    profile = build_profile(frame)
    assert profile.row_count == 0
    assert profile.missing_percentage == 0
    assert profile.columns[0].minimum is None


def test_snapshot_preserves_exact_bytes_and_hash(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    contents = b"x,y\r\n1,2\r\n"
    source.write_bytes(contents)
    store = FileStore(tmp_path / "store")
    staged, digest, size = store.stage(source)
    assert staged.read_bytes() == contents
    assert digest == hashlib.sha256(contents).hexdigest()
    assert size == len(contents)
    store.discard(staged)
