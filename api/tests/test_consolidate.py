from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from kivi.db.enums import AliasMatchMethod, EntityType, ExtractionOutcome, MemoryStatus, MemoryType
from kivi.db.models import Dictation, Entity, EntityAlias, ExtractionRun, Memory, MemoryEntity, MemorySource
from kivi.memory.consolidate import consolidate_claim
from kivi.memory.extract import ExtractedClaim
from kivi.models.provider import Completion


class FakeProvider:
    """A provider that returns a fixed verdict without any network call — the
    four consolidation outcomes need to be deterministic, not dependent on a
    live model agreeing with the test's premise."""

    name = "fake"

    def __init__(self, verdict_json: str):
        self.verdict_json = verdict_json

    async def complete(self, messages, *, schema=None, tier):
        return Completion(
            content=self.verdict_json, tokens_in=1, tokens_out=1,
            model="fake", latency_ms=0, cost_usd=0.0,
        )


def _claim(text: str) -> ExtractedClaim:
    return ExtractedClaim(
        text=text, category="decision", source_span=text,
        char_start=0, char_end=len(text), entities=[],
    )


async def _new_dictation(session: AsyncSession, suffix: str) -> str:
    """A second dictation, representing the later one that restates/refines/
    contradicts the original claim — memory_source has a real FK to dictation,
    so the consolidation call needs this row to actually exist."""
    dictation_id = f"dict_new_{suffix}"
    session.add(Dictation(
        id=dictation_id, spoken_at=datetime.now(timezone.utc),
        raw_asr="x", formatted_output="x", content_hash=f"hash_new_{suffix}",
    ))
    await session.flush()
    return dictation_id


async def _seed(session: AsyncSession, claim_text: str, claim_hash: str) -> tuple[Entity, Memory, ExtractionRun, str]:
    entity = Entity(entity_type=EntityType.PROJECT, canonical_name="Tara")
    session.add(entity)
    await session.flush()
    session.add(EntityAlias(
        entity_id=entity.id, alias="Tara", normalized="tara", match_method=AliasMatchMethod.EXACT,
    ))

    dictation_id = f"dict_seed_{claim_hash}"
    session.add(Dictation(
        id=dictation_id, spoken_at=datetime.now(timezone.utc),
        raw_asr="x", formatted_output="x", content_hash=f"hash_seed_{claim_hash}",
    ))
    await session.flush()

    run = ExtractionRun(
        dictation_id=dictation_id, outcome=ExtractionOutcome.MEMORIES_EXTRACTED,
        reason="seed", model="test", prompt_version="test",
    )
    session.add(run)
    await session.flush()

    memory = Memory(
        memory_type=MemoryType.DECISION, claim=claim_text, claim_hash=claim_hash,
        status=MemoryStatus.ACTIVE, valid_from=datetime.now(timezone.utc), extraction_run_id=run.id,
    )
    session.add(memory)
    await session.flush()
    session.add(MemorySource(memory_id=memory.id, dictation_id=dictation_id))
    session.add(MemoryEntity(memory_id=memory.id, entity_id=entity.id))
    await session.flush()

    return entity, memory, run, dictation_id


@pytest.mark.asyncio
async def test_exact_hash_match_is_duplicate_without_calling_the_model(db_session: AsyncSession) -> None:
    entity, memory, run, _ = await _seed(db_session, "Tara launches Feb 20", "hash_a")
    new_dictation_id = await _new_dictation(db_session, "a")
    claim = _claim("Tara launches Feb 20")
    provider = FakeProvider('{"relation": "unrelated", "reason": "should never be read"}')

    outcome, completions = await consolidate_claim(
        db_session, provider, claim, "hash_a", None, [entity.id], new_dictation_id,
        memory.valid_from, run.id,
    )

    assert outcome.action == "duplicate"
    assert outcome.memory_id == memory.id
    assert completions == []  # the hash alone proves it — no LLM call needed
    await db_session.flush()
    await db_session.refresh(memory)
    assert memory.occurrence_count == 2


@pytest.mark.asyncio
async def test_refinement_supersedes_the_old_memory(db_session: AsyncSession) -> None:
    entity, memory, run, _ = await _seed(db_session, "Tara launches in February", "hash_b")
    new_dictation_id = await _new_dictation(db_session, "b")
    claim = _claim("Tara launches February 20th specifically")
    provider = FakeProvider('{"relation": "refinement", "reason": "narrows a vague date"}')

    outcome, completions = await consolidate_claim(
        db_session, provider, claim, "hash_b_new", None, [entity.id], new_dictation_id,
        memory.valid_from, run.id,
    )

    assert outcome.action == "refinement"
    assert outcome.memory_id != memory.id
    assert len(completions) == 1
    await db_session.flush()
    await db_session.refresh(memory)
    assert memory.status == MemoryStatus.SUPERSEDED
    assert memory.superseded_by_id == outcome.memory_id


@pytest.mark.asyncio
async def test_contradiction_supersedes_the_old_memory(db_session: AsyncSession) -> None:
    entity, memory, run, _ = await _seed(db_session, "Tara launches Feb 20", "hash_c")
    new_dictation_id = await _new_dictation(db_session, "c")
    claim = _claim("Tara launches March 10 instead")
    provider = FakeProvider('{"relation": "contradiction", "reason": "the date moved"}')

    outcome, completions = await consolidate_claim(
        db_session, provider, claim, "hash_c_new", None, [entity.id], new_dictation_id,
        memory.valid_from, run.id,
    )

    assert outcome.action == "contradiction"
    assert outcome.memory_id != memory.id
    await db_session.flush()
    await db_session.refresh(memory)
    assert memory.status == MemoryStatus.SUPERSEDED
    assert memory.superseded_by_id == outcome.memory_id


@pytest.mark.asyncio
async def test_unrelated_creates_an_independent_memory(db_session: AsyncSession) -> None:
    entity, memory, run, _ = await _seed(db_session, "Tara launches Feb 20", "hash_d")
    new_dictation_id = await _new_dictation(db_session, "d")
    claim = _claim("Tara uses CloudEdge for hosting")
    provider = FakeProvider('{"relation": "unrelated", "reason": "a different fact about the same project"}')

    outcome, completions = await consolidate_claim(
        db_session, provider, claim, "hash_d_new", None, [entity.id], new_dictation_id,
        memory.valid_from, run.id,
    )

    assert outcome.action == "unrelated"
    assert outcome.memory_id != memory.id
    await db_session.flush()
    await db_session.refresh(memory)
    assert memory.status == MemoryStatus.ACTIVE  # untouched — no supersession


@pytest.mark.asyncio
async def test_tombstoned_claim_hash_is_never_recreated(db_session: AsyncSession) -> None:
    entity, memory, run, _ = await _seed(db_session, "Old forgotten claim", "hash_e")
    memory.status = MemoryStatus.FORGOTTEN
    await db_session.flush()
    new_dictation_id = await _new_dictation(db_session, "e")

    claim = _claim("Old forgotten claim")
    provider = FakeProvider('{"relation": "unrelated", "reason": "should never be read"}')

    outcome, completions = await consolidate_claim(
        db_session, provider, claim, "hash_e", None, [entity.id], new_dictation_id,
        memory.valid_from, run.id,
    )

    assert outcome.action == "tombstoned_skip"
    assert outcome.memory_id is None
    assert completions == []


@pytest.mark.asyncio
async def test_two_claims_in_one_dictation_duplicating_the_same_memory_is_idempotent(
    db_session: AsyncSession,
) -> None:
    """Regression: two claims extracted from the same dictation both matching the
    same existing memory as a duplicate used to violate memory_source's composite
    primary key on the second reinforce."""
    entity, memory, run, _ = await _seed(db_session, "Tara launches Feb 20", "hash_f")
    new_dictation_id = await _new_dictation(db_session, "f")
    provider = FakeProvider('{"relation": "unrelated", "reason": "should never be read"}')

    first, _ = await consolidate_claim(
        db_session, provider, _claim("Tara launches Feb 20"), "hash_f", None, [entity.id],
        new_dictation_id, memory.valid_from, run.id,
    )
    second, _ = await consolidate_claim(
        db_session, provider, _claim("Tara launches Feb 20"), "hash_f", None, [entity.id],
        new_dictation_id, memory.valid_from, run.id,
    )

    assert first.action == "duplicate"
    assert second.action == "duplicate"
    await db_session.flush()
    await db_session.refresh(memory)
    assert memory.occurrence_count == 2  # not 3 — the second reinforce was a no-op
