import math
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kivi.db.models import Entity, Memory, MemoryEntity
from kivi.retrieval.candidates import Candidate


@dataclass
class FusedCandidate:
    memory_id: int
    memory: Memory
    rrf_score: float
    boosted_score: float
    confidence: float
    component_scores: dict[str, float | None] = field(default_factory=dict)
    selected: bool = False


async def _memory_confidence(session: AsyncSession, memory_ids: list[int]) -> dict[int, float]:
    """A memory inherits the confidence of the entities it is anchored to — a
    claim about a brand-new, needs_review entity is exactly as uncertain as that
    entity is. Memories with no entities (e.g. a bare preference) default to 1.0:
    there is no entity-resolution risk to discount for."""
    if not memory_ids:
        return {}
    rows = (
        await session.execute(
            select(MemoryEntity.memory_id, func.avg(Entity.confidence))
            .join(Entity, Entity.id == MemoryEntity.entity_id)
            .where(MemoryEntity.memory_id.in_(memory_ids))
            .group_by(MemoryEntity.memory_id)
        )
    ).all()
    confidences = {mid: float(avg) for mid, avg in rows}
    return {mid: confidences.get(mid, 1.0) for mid in memory_ids}


def _passes_hard_filters(
    memory: Memory,
    *,
    time_before,
    time_after,
    types: list[str] | None,
) -> bool:
    if types and memory.memory_type.value not in types:
        return False
    if time_after is not None and memory.valid_to is not None and memory.valid_to < time_after:
        return False
    if time_before is not None and memory.valid_from > time_before:
        return False
    return True


async def fuse(
    session: AsyncSession,
    entity_cands: list[Candidate],
    lexical_cands: list[Candidate],
    semantic_cands: list[Candidate],
    *,
    k: int,
    top_n: int,
    time_before=None,
    time_after=None,
    types: list[str] | None = None,
) -> tuple[list[FusedCandidate], list[dict]]:
    """Reciprocal rank fusion across the three generators, then hard filters,
    then a confidence/reinforcement boost, then pinned-first ordering. Returns
    the selected top_n plus a trace entry for EVERY candidate any generator
    surfaced (query_trace records all of them, not just the winners)."""
    component_scores: dict[int, dict[str, float | None]] = {}
    rrf_scores: dict[int, float] = {}

    for generator_name, cands in (("entity", entity_cands), ("lexical", lexical_cands), ("semantic", semantic_cands)):
        for c in cands:
            component_scores.setdefault(c.memory_id, {"entity": None, "lexical": None, "semantic": None})
            # Entity candidates are deliberately unscored (score=None) — record
            # the recall-order rank instead, so a real match at rank 0 is never
            # visually indistinguishable in the trace from no match at all.
            display_value = c.rank if generator_name == "entity" else c.score
            component_scores[c.memory_id][generator_name] = display_value
            rrf_scores[c.memory_id] = rrf_scores.get(c.memory_id, 0.0) + 1.0 / (k + c.rank + 1)

    memory_ids = list(rrf_scores.keys())
    if not memory_ids:
        return [], []

    memories = (
        await session.execute(select(Memory).where(Memory.id.in_(memory_ids)))
    ).scalars().all()
    memory_by_id = {m.id: m for m in memories}
    confidence_by_id = await _memory_confidence(session, memory_ids)

    all_candidates: list[FusedCandidate] = []
    for mid in memory_ids:
        memory = memory_by_id.get(mid)
        if memory is None:
            continue  # superseded/forgotten between candidate generation and here — skip
        confidence = confidence_by_id.get(mid, 1.0)
        rrf_score = rrf_scores[mid]
        boosted = rrf_score * confidence * (1.0 + math.log(memory.occurrence_count))
        all_candidates.append(
            FusedCandidate(
                memory_id=mid, memory=memory, rrf_score=rrf_score, boosted_score=boosted,
                confidence=confidence, component_scores=component_scores[mid],
            )
        )

    filtered = [
        fc for fc in all_candidates
        if _passes_hard_filters(fc.memory, time_before=time_before, time_after=time_after, types=types)
    ]
    filtered.sort(key=lambda fc: (not fc.memory.pinned, -fc.boosted_score))
    selected = filtered[:top_n]
    selected_ids = {fc.memory_id for fc in selected}
    for fc in all_candidates:
        fc.selected = fc.memory_id in selected_ids

    trace = [
        {
            "memory_id": fc.memory_id,
            "component_scores": fc.component_scores,
            "rrf_score": round(fc.rrf_score, 6),
            "confidence": round(fc.confidence, 3),
            "boosted_score": round(fc.boosted_score, 6),
            "pinned": fc.memory.pinned,
            "passed_hard_filters": fc.memory_id in {f.memory_id for f in filtered},
            "selected": fc.selected,
        }
        for fc in all_candidates
    ]
    return selected, trace
