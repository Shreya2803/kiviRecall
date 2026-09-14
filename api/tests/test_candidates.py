import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from kivi.db.enums import AliasMatchMethod, EntityType, ExtractionOutcome, MemoryStatus, MemoryType
from kivi.db.models import Dictation, Entity, EntityAlias, ExtractionRun, Memory, MemoryEntity, MemorySource
from kivi.retrieval.candidates import entity_candidates

NOW = datetime.datetime(2026, 3, 1, tzinfo=datetime.timezone.utc)


@pytest.mark.asyncio
async def test_memory_linked_to_two_matched_entities_is_returned_once(db_session: AsyncSession) -> None:
    """Regression: a memory joined against 2+ entity_ids used to duplicate under
    SELECT DISTINCT with an ORDER BY column outside the select list, which
    Postgres rejects outright rather than silently deduping."""
    dictation_id = "dict_multi_entity"
    db_session.add(Dictation(
        id=dictation_id, spoken_at=NOW, raw_asr="x", formatted_output="x",
        content_hash="hash_multi_entity",
    ))
    await db_session.flush()

    run = ExtractionRun(
        dictation_id=dictation_id, outcome=ExtractionOutcome.MEMORIES_EXTRACTED,
        reason="seed", model="test", prompt_version="test",
    )
    db_session.add(run)
    await db_session.flush()

    memory = Memory(
        memory_type=MemoryType.PROJECT_STATE, claim="Rohit and Priya both work on Tara",
        claim_hash="hash_multi", status=MemoryStatus.ACTIVE, valid_from=NOW, extraction_run_id=run.id,
    )
    db_session.add(memory)
    await db_session.flush()
    db_session.add(MemorySource(memory_id=memory.id, dictation_id=dictation_id))

    entity_ids = []
    for name in ("Rohit", "Priya"):
        entity = Entity(entity_type=EntityType.PERSON, canonical_name=name)
        db_session.add(entity)
        await db_session.flush()
        db_session.add(EntityAlias(
            entity_id=entity.id, alias=name, normalized=name.lower(), match_method=AliasMatchMethod.EXACT,
        ))
        db_session.add(MemoryEntity(memory_id=memory.id, entity_id=entity.id))
        entity_ids.append(entity.id)
    await db_session.flush()

    results = await entity_candidates(db_session, entity_ids, limit=30)

    assert [c.memory_id for c in results] == [memory.id]
