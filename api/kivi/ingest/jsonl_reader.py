import json
from pathlib import Path

# Re-exported so existing call sites (kivi.cli, eval/run_eval.py) don't need
# to know ingestion moved to a format-agnostic module.
from kivi.ingest.common import (  # noqa: F401
    OPTIONAL_FIELDS,
    REQUIRED_FIELDS,
    compute_content_hash,
    ingest_records,
    validate_records,
)


def read_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records
