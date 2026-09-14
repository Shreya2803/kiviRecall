from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kivi.db.enums import MemoryStatus
from kivi.db.models import Memory, MemoryEntity

CandidateSource = Literal["entity", "lexical", "semantic"]


@dataclass
class Candidate:
    memory_id: int
    source: CandidateSource
    score: float | None  # None for entity-anchored — recall-oriented, unscored
    rank: int  # 0-based position within this generator's own ordering


async def entity_candidates(
    session: AsyncSession, entity_ids: list[int], limit: int
) -> list[Candidate]:
    """Recall-oriented and unscored on purpose: this is the join that recovers
    distributed facts (CLAUDE.md), so it returns everything linked to a resolved
    entity rather than ranking by any notion of relevance."""
    if not entity_ids:
        return []
    rows = (
        await session.execute(
            select(Memory.id, Memory.occurrence_count, Memory.valid_from)
            .join(MemoryEntity, MemoryEntity.memory_id == Memory.id)
            .where(MemoryEntity.entity_id.in_(entity_ids), Memory.status == MemoryStatus.ACTIVE)
            .distinct()
            .order_by(Memory.occurrence_count.desc(), Memory.valid_from.desc())
            .limit(limit)
        )
    ).all()
    return [
        Candidate(memory_id=row.id, source="entity", score=None, rank=i) for i, row in enumerate(rows)
    ]


async def lexical_candidates(session: AsyncSession, query_text: str, limit: int) -> list[Candidate]:
    tsquery = func.plainto_tsquery("english", query_text)
    rank = func.ts_rank_cd(Memory.claim_tsv, tsquery).label("rank")
    rows = (
        await session.execute(
            select(Memory.id, rank)
            .where(Memory.status == MemoryStatus.ACTIVE, Memory.claim_tsv.op("@@")(tsquery))
            .order_by(rank.desc())
            .limit(limit)
        )
    ).all()
    return [
        Candidate(memory_id=mid, source="lexical", score=float(score), rank=i)
        for i, (mid, score) in enumerate(rows)
    ]


async def semantic_candidates(
    session: AsyncSession, query_embedding: list[float], limit: int
) -> list[Candidate]:
    distance = Memory.embedding.cosine_distance(query_embedding).label("distance")
    rows = (
        await session.execute(
            select(Memory.id, distance)
            .where(Memory.status == MemoryStatus.ACTIVE, Memory.embedding.is_not(None))
            .order_by(distance.asc())
            .limit(limit)
        )
    ).all()
    return [
        Candidate(memory_id=mid, source="semantic", score=1.0 - float(dist), rank=i)
        for i, (mid, dist) in enumerate(rows)
    ]
