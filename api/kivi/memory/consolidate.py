import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kivi.db.enums import MemoryStatus
from kivi.db.models import Entity, Memory, MemoryEntity, MemorySource
from kivi.memory.extract import ExtractedClaim
from kivi.models.provider import Completion, ModelProvider

PROMPT_VERSION = "consolidate_v1"
_PROMPT = (Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.md").read_text(encoding="utf-8")

Relation = Literal["duplicate", "refinement", "contradiction", "unrelated"]
Action = Literal["tombstoned_skip", "duplicate", "refinement", "contradiction", "unrelated"]


class ConsolidationVerdict(BaseModel):
    relation: Relation
    reason: str


@dataclass
class ConsolidationOutcome:
    action: Action
    memory_id: int | None


async def classify_relation(
    provider: ModelProvider, new_claim_text: str, existing_claim_text: str
) -> tuple[ConsolidationVerdict, Completion]:
    messages = [
        {"role": "system", "content": _PROMPT},
        {"role": "user", "content": f"Existing belief: {existing_claim_text}\nNew statement: {new_claim_text}"},
    ]
    completion = await provider.complete(messages, schema=ConsolidationVerdict, tier="extraction")
    return ConsolidationVerdict.model_validate_json(completion.content), completion


async def _reinforce(session: AsyncSession, existing: Memory, dictation_id: str) -> None:
    # Two claims from the SAME dictation can both classify as duplicates of the
    # same existing memory — idempotent on (memory_id, dictation_id), or the
    # second insert collides with memory_source's composite primary key.
    already_linked = await session.scalar(
        select(MemorySource).where(
            MemorySource.memory_id == existing.id, MemorySource.dictation_id == dictation_id
        )
    )
    if already_linked is not None:
        return
    existing.occurrence_count += 1
    session.add(MemorySource(memory_id=existing.id, dictation_id=dictation_id))


async def _link_entities(session: AsyncSession, memory_id: int, entity_ids: list[int]) -> None:
    for entity_id in set(entity_ids):
        session.add(MemoryEntity(memory_id=memory_id, entity_id=entity_id))


async def _entity_confidence(session: AsyncSession, entity_ids: list[int]) -> float:
    """A memory inherits the confidence of the entities it names — a claim
    about a brand-new, needs_review entity is exactly as uncertain as that
    entity is. No entities (e.g. a bare preference) means no resolution risk
    to discount for, so it defaults to fully confident."""
    if not entity_ids:
        return 1.0
    avg = await session.scalar(select(func.avg(Entity.confidence)).where(Entity.id.in_(entity_ids)))
    return float(avg) if avg is not None else 1.0


async def _create_new(
    session: AsyncSession,
    claim: ExtractedClaim,
    claim_hash: str,
    embedding: list[float] | None,
    entity_ids: list[int],
    dictation_id: str,
    valid_from: datetime.datetime,
    extraction_run_id: int,
    superseded_id: int | None,
) -> int:
    confidence = await _entity_confidence(session, entity_ids)
    memory = Memory(
        memory_type=claim.category,  # a KEEP_CATEGORIES value, matches MemoryType exactly
        claim=claim.text,
        claim_hash=claim_hash,
        embedding=embedding,
        confidence=confidence,
        status=MemoryStatus.ACTIVE,
        valid_from=valid_from,
        extraction_run_id=extraction_run_id,
    )
    session.add(memory)
    await session.flush()

    session.add(MemorySource(memory_id=memory.id, dictation_id=dictation_id))
    await _link_entities(session, memory.id, entity_ids)

    if superseded_id is not None:
        old = await session.get(Memory, superseded_id)
        old.status = MemoryStatus.SUPERSEDED
        old.valid_to = valid_from
        old.superseded_by_id = memory.id

    return memory.id


async def consolidate_claim(
    session: AsyncSession,
    provider: ModelProvider,
    claim: ExtractedClaim,
    claim_hash: str,
    embedding: list[float] | None,
    entity_ids: list[int],
    dictation_id: str,
    valid_from: datetime.datetime,
    extraction_run_id: int,
) -> tuple[ConsolidationOutcome, list[Completion]]:
    completions: list[Completion] = []

    # Forget survives re-import: a tombstoned claim_hash must never be recreated.
    tombstoned = await session.scalar(
        select(Memory).where(Memory.claim_hash == claim_hash, Memory.status == MemoryStatus.FORGOTTEN)
    )
    if tombstoned is not None:
        return ConsolidationOutcome("tombstoned_skip", None), completions

    # Exact claim_hash match against an active memory is a duplicate by
    # definition — skip the LLM call, the hash already proves it.
    exact_active = await session.scalar(
        select(Memory).where(Memory.claim_hash == claim_hash, Memory.status == MemoryStatus.ACTIVE)
    )
    if exact_active is not None:
        await _reinforce(session, exact_active, dictation_id)
        return ConsolidationOutcome("duplicate", exact_active.id), completions

    if not entity_ids:
        memory_id = await _create_new(
            session, claim, claim_hash, embedding, entity_ids, dictation_id, valid_from,
            extraction_run_id, superseded_id=None,
        )
        return ConsolidationOutcome("unrelated", memory_id), completions

    candidates = (
        await session.execute(
            select(Memory)
            .join(MemoryEntity, MemoryEntity.memory_id == Memory.id)
            .where(MemoryEntity.entity_id.in_(entity_ids), Memory.status == MemoryStatus.ACTIVE)
            .distinct()
        )
    ).scalars().all()

    for existing in candidates:
        verdict, completion = await classify_relation(provider, claim.text, existing.claim)
        completions.append(completion)

        if verdict.relation == "duplicate":
            await _reinforce(session, existing, dictation_id)
            return ConsolidationOutcome("duplicate", existing.id), completions

        if verdict.relation in ("refinement", "contradiction"):
            memory_id = await _create_new(
                session, claim, claim_hash, embedding, entity_ids, dictation_id, valid_from,
                extraction_run_id, superseded_id=existing.id,
            )
            return ConsolidationOutcome(verdict.relation, memory_id), completions
        # "unrelated" — keep checking the remaining candidates

    memory_id = await _create_new(
        session, claim, claim_hash, embedding, entity_ids, dictation_id, valid_from,
        extraction_run_id, superseded_id=None,
    )
    return ConsolidationOutcome("unrelated", memory_id), completions
