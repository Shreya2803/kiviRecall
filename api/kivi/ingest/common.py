import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kivi.db.models import Dictation

# CLAUDE.md §6: only these four fields are guaranteed by the data contract.
# Everything else must degrade gracefully — a warning, never an error.
REQUIRED_FIELDS = ("id", "spoken_at", "raw_asr", "formatted_output")
OPTIONAL_FIELDS = ("app_context", "window_title", "language", "duration_ms", "style_id")


def compute_content_hash(raw_asr: str, formatted_output: str) -> str:
    return hashlib.sha256(f"{raw_asr}\n{formatted_output}".encode("utf-8")).hexdigest()


@dataclass
class ValidationResult:
    """The whole batch is validated before anything touches the database. A
    record missing a required field is dropped with a reason and never reaches
    ingestion; a record only missing optional fields is kept, with a warning —
    never a hard failure, per CLAUDE.md's data contract."""

    valid: list[dict] = field(default_factory=list)
    warnings_by_id: dict[str, list[str]] = field(default_factory=dict)
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (id, reason)


def validate_records(records: list[dict]) -> ValidationResult:
    result = ValidationResult()
    for i, record in enumerate(records):
        label = str(record.get("id") or f"<record {i}, no id>")
        missing_required = [f for f in REQUIRED_FIELDS if not record.get(f)]
        if missing_required:
            result.rejected.append((label, f"missing required field(s): {', '.join(missing_required)}"))
            continue
        warnings = [f"missing optional field: {f}" for f in OPTIONAL_FIELDS if not record.get(f)]
        if warnings:
            result.warnings_by_id[label] = warnings
        result.valid.append(record)
    return result


async def ingest_records(session: AsyncSession, records: list[dict]) -> tuple[list[Dictation], int]:
    """Validates the whole batch first (see validate_records), then ingests
    only what's valid. Returns (newly inserted Dictation rows, count skipped as
    already-imported). Idempotent on content_hash — re-running import over the
    same corpus inserts nothing twice. Records rejected for missing required
    fields are silently excluded here — callers that want to report them
    should call validate_records themselves first (kivi.cli's `import` does)."""
    validation = validate_records(records)
    inserted: list[Dictation] = []
    skipped = 0
    for record in validation.valid:
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
