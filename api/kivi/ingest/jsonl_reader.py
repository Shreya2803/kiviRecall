import hashlib
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kivi.db.models import Dictation

REQUIRED_FIELDS = ("id", "spoken_at", "raw_asr", "formatted_output")
OPTIONAL_FIELDS = ("app_context", "window_title", "language", "duration_ms", "style_id")


def compute_content_hash(raw_asr: str, formatted_output: str) -> str:
    return hashlib.sha256(f"{raw_asr}\n{formatted_output}".encode("utf-8")).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def validate_record(record: dict) -> list[str]:
    """Only the four required fields are guaranteed by the data contract
    (CLAUDE.md §6). Missing optional fields degrade gracefully — a warning, not
    an error — missing required fields raise."""
    for f in REQUIRED_FIELDS:
        if not record.get(f):
            raise ValueError(f"record {record.get('id', '?')} missing required field: {f}")
    return [f"missing optional field: {f}" for f in OPTIONAL_FIELDS if f not in record]


async def ingest_records(session: AsyncSession, records: list[dict]) -> tuple[list[Dictation], int]:
    """Returns (newly inserted Dictation rows, count skipped as already-imported).
    Idempotent on content_hash — re-running import over the same corpus inserts
    nothing twice."""
    inserted: list[Dictation] = []
    skipped = 0
    for record in records:
        validate_record(record)
        content_hash = compute_content_hash(record["raw_asr"], record["formatted_output"])
        existing = await session.scalar(select(Dictation).where(Dictation.content_hash == content_hash))
        if existing is not None:
            skipped += 1
            continue
        dictation = Dictation(
            id=record["id"],
            spoken_at=datetime.fromisoformat(record["spoken_at"]),
            raw_asr=record["raw_asr"],
            formatted_output=record["formatted_output"],
            app_context=record.get("app_context"),
            window_title=record.get("window_title"),
            language=record.get("language"),
            duration_ms=record.get("duration_ms"),
            style_id=record.get("style_id"),
            content_hash=content_hash,
        )
        session.add(dictation)
        inserted.append(dictation)
    await session.commit()
    return inserted, skipped
