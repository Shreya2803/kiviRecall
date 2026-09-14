import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from kivi.db.enums import AliasMatchMethod, EntityType, ExtractionOutcome, MemoryStatus, MemoryType
from kivi.db.models import Dictation, Entity, EntityAlias, ExtractionRun, Memory, MemoryEntity, MemorySource
from kivi.retrieval.candidates import Candidate
from kivi.retrieval.fusion import fuse

NOW = datetime.datetime(2026, 3, 1, tzinfo=datetime.timezone.utc)


async def _seed_memory(
    session: AsyncSession,
    suffix: str,
    *,
    claim: str = "Tara launches March 20",
    memory_type: MemoryType = MemoryType.PROJECT_STATE,
    occurrence_count: int = 1,
    entity_confidence: float | None = 1.0,
    pinned: bool = False,
    valid_from: datetime.datetime = NOW,
    valid_to: datetime.datetime | None = None,
) -> Memory:
    dictation_id = f"dict_{suffix}"
    session.add(Dictation(
        id=dictation_id, spoken_at=NOW, raw_asr="x", formatted_output="x",
        content_hash=f"hash_{suffix}",
    ))
    await session.flush()

    run = ExtractionRun(
        dictation_id=dictation_id, outcome=ExtractionOutcome.MEMORIES_EXTRACTED,
        reason="seed", model="test", prompt_version="test",
    )
    session.add(run)
    await session.flush()

    memory = Memory(
        memory_type=memory_type, claim=claim, claim_hash=f"claimhash_{suffix}",
        status=MemoryStatus.ACTIVE, valid_from=valid_from, valid_to=valid_to,
        extraction_run_id=run.id, occurrence_count=occurrence_count, pinned=pinned,
    )
    session.add(memory)
    await session.flush()
    session.add(MemorySource(memory_id=memory.id, dictation_id=dictation_id))

    if entity_confidence is not None:
        entity = Entity(
            entity_type=EntityType.PROJECT, canonical_name=f"Project{suffix}", confidence=entity_confidence,
        )
        session.add(entity)
        await session.flush()
        session.add(EntityAlias(
            entity_id=entity.id, alias=f"Project{suffix}", normalized=f"project{suffix}",
            match_method=AliasMatchMethod.EXACT,
        ))
        session.add(MemoryEntity(memory_id=memory.id, entity_id=entity.id))

    await session.flush()
    return memory


@pytest.mark.asyncio
async def test_higher_occurrence_count_boosts_above_equal_rrf_score(db_session: AsyncSession) -> None:
    reinforced = await _seed_memory(db_session, "a", occurrence_count=5)
    single = await _seed_memory(db_session, "b", occurrence_count=1)

    lexical = [
        Candidate(memory_id=reinforced.id, source="lexical", score=0.5, rank=0),
        Candidate(memory_id=single.id, source="lexical", score=0.5, rank=0),
    ]

    selected, _ = await fuse(db_session, [], lexical, [], k=60, top_n=10)

    assert selected[0].memory_id == reinforced.id
    assert selected[0].boosted_score > selected[1].boosted_score


@pytest.mark.asyncio
async def test_low_confidence_entity_reduces_boost(db_session: AsyncSession) -> None:
    confident = await _seed_memory(db_session, "c", entity_confidence=1.0)
    unsure = await _seed_memory(db_session, "d", entity_confidence=0.3)

    lexical = [
        Candidate(memory_id=confident.id, source="lexical", score=0.5, rank=0),
        Candidate(memory_id=unsure.id, source="lexical", score=0.5, rank=0),
    ]

    selected, _ = await fuse(db_session, [], lexical, [], k=60, top_n=10)

    assert selected[0].memory_id == confident.id
    assert selected[0].confidence > selected[1].confidence


@pytest.mark.asyncio
async def test_type_filter_excludes_non_matching_memory(db_session: AsyncSession) -> None:
    role_memory = await _seed_memory(db_session, "e", memory_type=MemoryType.ROLE)
    decision_memory = await _seed_memory(db_session, "f", memory_type=MemoryType.DECISION)

    lexical = [
        Candidate(memory_id=role_memory.id, source="lexical", score=0.5, rank=0),
        Candidate(memory_id=decision_memory.id, source="lexical", score=0.5, rank=1),
    ]

    selected, trace = await fuse(db_session, [], lexical, [], k=60, top_n=10, types=["role"])

    assert [fc.memory_id for fc in selected] == [role_memory.id]
    decision_row = next(t for t in trace if t["memory_id"] == decision_memory.id)
    assert decision_row["passed_hard_filters"] is False
    assert decision_row["selected"] is False


@pytest.mark.asyncio
async def test_pinned_memory_sorts_first_despite_lower_score(db_session: AsyncSession) -> None:
    pinned = await _seed_memory(db_session, "g", pinned=True)
    unpinned = await _seed_memory(db_session, "h", pinned=False)

    lexical = [
        Candidate(memory_id=pinned.id, source="lexical", score=0.1, rank=5),
        Candidate(memory_id=unpinned.id, source="lexical", score=0.9, rank=0),
    ]

    selected, _ = await fuse(db_session, [], lexical, [], k=60, top_n=10)

    assert selected[0].memory_id == pinned.id


@pytest.mark.asyncio
async def test_time_window_excludes_memory_valid_only_before_window(db_session: AsyncSession) -> None:
    old_period = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    old_period_end = datetime.datetime(2026, 1, 15, tzinfo=datetime.timezone.utc)
    expired = await _seed_memory(db_session, "i", valid_from=old_period, valid_to=old_period_end)
    current = await _seed_memory(db_session, "j", valid_from=NOW, valid_to=None)

    lexical = [
        Candidate(memory_id=expired.id, source="lexical", score=0.5, rank=0),
        Candidate(memory_id=current.id, source="lexical", score=0.5, rank=1),
    ]

    time_after = datetime.datetime(2026, 2, 1, tzinfo=datetime.timezone.utc)
    selected, _ = await fuse(db_session, [], lexical, [], k=60, top_n=10, time_after=time_after)

    assert [fc.memory_id for fc in selected] == [current.id]
