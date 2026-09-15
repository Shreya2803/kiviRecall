import datetime
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kivi.db.models import Dictation


@dataclass
class FindResult:
    answer: str
    found_dictation_ids: list[str] = field(default_factory=list)
    verdict: str = "insufficient"
    reason: str = ""


async def find_dictation(
    session: AsyncSession,
    *,
    app_context: str | None = None,
    time_before: datetime.datetime | None = None,
    time_after: datetime.datetime | None = None,
    limit: int = 10,
) -> FindResult:
    """A direct lookup of what was actually dictated — never touches memory or
    an embedding, so it's fast by construction (CLAUDE.md: ~40ms, not 1.5s)."""
    stmt = select(Dictation).order_by(Dictation.spoken_at.desc()).limit(limit)
    if app_context:
        stmt = stmt.where(Dictation.app_context.ilike(f"%{app_context}%"))
    if time_after:
        stmt = stmt.where(Dictation.spoken_at >= time_after)
    if time_before:
        stmt = stmt.where(Dictation.spoken_at <= time_before)
    rows = (await session.execute(stmt)).scalars().all()

    if not rows:
        return FindResult(
            answer="I didn't find anything you dictated matching that.",
            verdict="insufficient", reason="no matching dictations",
        )

    lines = [f"Found {len(rows)} dictation(s):"]
    for d in rows:
        lines.append(f"- [{d.spoken_at.isoformat()}] ({d.app_context or 'unknown app'}) {d.formatted_output}")
    return FindResult(
        answer="\n".join(lines), found_dictation_ids=[d.id for d in rows],
        verdict="sufficient", reason="direct dictation lookup",
    )
