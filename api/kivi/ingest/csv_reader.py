import csv
from pathlib import Path

from kivi.ingest.common import OPTIONAL_FIELDS, REQUIRED_FIELDS

_ALL_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS
_LANGUAGE_SPLIT_RE = None  # set lazily below to avoid importing re at module load for the common case


def _split_language(raw: str) -> list[str]:
    global _LANGUAGE_SPLIT_RE
    if _LANGUAGE_SPLIT_RE is None:
        import re

        _LANGUAGE_SPLIT_RE = re.compile(r"[,|;]")
    return [part.strip() for part in _LANGUAGE_SPLIT_RE.split(raw) if part.strip()]


def infer_mapping(header: list[str]) -> dict[str, str]:
    """Best-effort column mapping when the reviewer's CSV doesn't use our
    canonical field names verbatim: match case- and whitespace/underscore-
    insensitively (so "Raw ASR", "raw-asr", "RAWASR" all find raw_asr).
    Returns {canonical_field: csv_column_name} for whatever it could match —
    an explicit --map flag on the CLI always overrides this per field."""
    normalized = {col.strip().lower().replace(" ", "").replace("-", "").replace("_", ""): col for col in header}
    mapping = {}
    for field in _ALL_FIELDS:
        key = field.replace("_", "")
        if key in normalized:
            mapping[field] = normalized[key]
    return mapping


def read_csv(path: Path, mapping: dict[str, str] | None = None) -> tuple[list[dict], dict[str, str]]:
    """Reads a CSV into the same record shape read_jsonl produces. `mapping`
    is {canonical_field: csv_column_name}; any canonical field not in it falls
    back to infer_mapping's best-effort guess for that header. Returns
    (records, resolved_mapping) — the resolved mapping is surfaced to the
    caller (kivi.cli's `import`) so a reviewer can see exactly which of their
    columns fed which canonical field before trusting the import."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        guessed = infer_mapping(header)
        resolved = {**guessed, **(mapping or {})}
        # Every field explicitly mapped must actually exist in the header —
        # a typo'd --map is a configuration error, not a per-record warning.
        for field, col in resolved.items():
            if col not in header:
                raise ValueError(f"--map {field}={col} but the CSV has no column {col!r} (header: {header})")

        records = []
        for row in reader:
            record: dict = {}
            for field in _ALL_FIELDS:
                col = resolved.get(field)
                value = row.get(col, "").strip() if col else ""
                if not value:
                    continue
                if field == "language":
                    record[field] = _split_language(value)
                elif field == "duration_ms":
                    try:
                        record[field] = int(value)
                    except ValueError:
                        pass  # left out entirely — degrades to "missing optional field", not a crash
                else:
                    record[field] = value
            records.append(record)
    return records, resolved
