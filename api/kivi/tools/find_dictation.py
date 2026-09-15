import datetime
from dataclasses import dataclass, field

from sqlalchemy import func, select
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
    an embedding, so it's fast by construction ."""
    filters = []
    if app_context:
        filters.append(Dictation.app_context.ilike(f"%{app_context}%"))
    if time_after:
        filters.append(Dictation.spoken_at >= time_after)
    if time_before:
        filters.append(Dictation.spoken_at <= time_before)

    stmt = select(Dictation).order_by(Dictation.spoken_at.desc()).limit(limit)
    count_stmt = select(func.count()).select_from(Dictation)
    for f in filters:
        stmt = stmt.where(f)
        count_stmt = count_stmt.where(f)

    rows = (await session.execute(stmt)).scalars().all()
    total = await session.scalar(count_stmt) or 0

    if not rows:
        return FindResult(
            answer="I didn't find anything you dictated matching that.",
            verdict="insufficient", reason="no matching dictations",
        )

    # Never silently truncate: if more matched than fit in one response, say so
    # rather than presenting a partial list as if it were the whole answer.
    header = (
        f"Found {total} dictation(s), showing the most recent {len(rows)}:"
        if total > len(rows) else f"Found {len(rows)} dictation(s):"
    )
    lines = [header]
    for d in rows:
        lines.append(f"- [{d.spoken_at.isoformat()}] ({d.app_context or 'unknown app'}) {d.formatted_output}")
    return FindResult(
        answer="\n".join(lines), found_dictation_ids=[d.id for d in rows],
        verdict="sufficient", reason="direct dictation lookup",
    )
