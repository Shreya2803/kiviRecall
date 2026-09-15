from pathlib import Path

from kivi.ingest.common import validate_records
from kivi.ingest.csv_reader import infer_mapping, read_csv


def test_validate_records_rejects_missing_required_field():
    records = [
        {"id": "d1", "spoken_at": "2026-01-01T00:00:00+00:00", "raw_asr": "hi", "formatted_output": "Hi"},
        {"id": "d2", "spoken_at": "2026-01-01T00:00:00+00:00", "formatted_output": "missing raw_asr"},
    ]
    result = validate_records(records)
    assert [r["id"] for r in result.valid] == ["d1"]
    assert result.rejected == [("d2", "missing required field(s): raw_asr")]


def test_validate_records_warns_on_missing_optional_field_but_keeps_it():
    records = [{"id": "d1", "spoken_at": "2026-01-01T00:00:00+00:00", "raw_asr": "hi", "formatted_output": "Hi"}]
    result = validate_records(records)
    assert len(result.valid) == 1
    assert result.rejected == []
    assert "d1" in result.warnings_by_id
    assert any("app_context" in w for w in result.warnings_by_id["d1"])


def test_validate_records_one_bad_row_does_not_sink_the_batch():
    records = [
        {"id": "d1", "spoken_at": "2026-01-01T00:00:00+00:00", "raw_asr": "a", "formatted_output": "A"},
        {"id": "d2"},  # missing everything required
        {"id": "d3", "spoken_at": "2026-01-01T00:00:00+00:00", "raw_asr": "c", "formatted_output": "C"},
    ]
    result = validate_records(records)
    assert [r["id"] for r in result.valid] == ["d1", "d3"]
    assert len(result.rejected) == 1


def test_infer_mapping_matches_case_and_spacing_insensitively():
    # infer_mapping is deliberately literal (matches a header to a canonical
    # field only once normalised whitespace/case/punctuation is stripped) —
    # anything less literal than that, like "Record ID" for "id", is exactly
    # what --map exists to override explicitly rather than guess at.
    header = ["ID", "Spoken At", "Raw ASR", "Formatted Output", "App Context"]
    mapping = infer_mapping(header)
    assert mapping["id"] == "ID"
    assert mapping["spoken_at"] == "Spoken At"
    assert mapping["raw_asr"] == "Raw ASR"
    assert mapping["formatted_output"] == "Formatted Output"
    assert mapping["app_context"] == "App Context"


def test_read_csv_with_explicit_mapping_splits_language_and_casts_duration(tmp_path: Path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(
        "RID,TS,Raw,Clean,Langs,Dur\n"
        "c1,2026-04-01T09:00:00+05:30,raw text,Clean text,hi|en,5000\n",
        encoding="utf-8",
    )
    records, resolved = read_csv(
        csv_path,
        {"id": "RID", "spoken_at": "TS", "raw_asr": "Raw", "formatted_output": "Clean", "language": "Langs", "duration_ms": "Dur"},
    )
    assert resolved["id"] == "RID"
    assert len(records) == 1
    assert records[0]["language"] == ["hi", "en"]
    assert records[0]["duration_ms"] == 5000


def test_read_csv_rejects_unknown_mapped_column(tmp_path: Path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text("A,B\n1,2\n", encoding="utf-8")
    try:
        read_csv(csv_path, {"id": "NotAColumn"})
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "NotAColumn" in str(exc)


def test_read_csv_empty_cell_is_treated_as_missing_not_empty_string(tmp_path: Path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(
        "id,spoken_at,raw_asr,formatted_output,app_context\n"
        "c1,2026-04-01T09:00:00+05:30,raw,Clean,\n",
        encoding="utf-8",
    )
    records, _ = read_csv(csv_path)
    assert "app_context" not in records[0]
