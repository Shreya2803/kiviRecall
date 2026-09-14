import asyncio
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from kivi.db.session import engine
from kivi.db.enums import AliasMatchMethod, EntityType, ExtractionOutcome, MemoryStatus, MemoryType
from kivi.db.models import Dictation, Entity, EntityAlias, ExtractionRun, Memory, MemoryEntity, MemorySource
from kivi.memory.consolidate import consolidate_claim
from kivi.memory.extract import ExtractedClaim


class FakeProvider:
    name = "fake"
    def __init__(self, verdict_json):
        self.verdict_json = verdict_json
    async def complete(self, messages, *, schema=None, tier):
        from kivi.models.provider import Completion
        return Completion(content=self.verdict_json, tokens_in=1, tokens_out=1, model="fake", latency_ms=0, cost_usd=0.0)


def _claim(text):
    return ExtractedClaim(text=text, category="decision", source_span=text, char_start=0, char_end=len(text), entities=[])


async def main():
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)

        entity = Entity(entity_type=EntityType.PROJECT, canonical_name="Tara")
        session.add(entity)
        await session.flush()
        session.add(EntityAlias(entity_id=entity.id, alias="Tara", normalized="tara", match_method=AliasMatchMethod.EXACT))

        dictation = Dictation(id="dict_debug", spoken_at=datetime.now(timezone.utc), raw_asr="x", formatted_output="x", content_hash="hash_debug_seed")
        session.add(dictation)

        run = ExtractionRun(dictation_id="dict_debug", outcome=ExtractionOutcome.MEMORIES_EXTRACTED, reason="seed", model="test", prompt_version="test")
        session.add(run)
        await session.flush()

        memory = Memory(memory_type=MemoryType.DECISION, claim="Tara launches Feb 20", claim_hash="hash_c",
                         status=MemoryStatus.ACTIVE, valid_from=datetime.now(timezone.utc), extraction_run_id=run.id)
        session.add(memory)
        await session.flush()
        print("seeded memory id:", memory.id, "status:", memory.status)
        session.add(MemorySource(memory_id=memory.id, dictation_id="dict_debug"))
        session.add(MemoryEntity(memory_id=memory.id, entity_id=entity.id))
        await session.flush()

        claim = _claim("Tara launches March 10 instead")
        provider = FakeProvider('{"relation": "contradiction", "reason": "the date moved"}')

        outcome, completions = await consolidate_claim(
            session, provider, claim, "hash_c_new", None, [entity.id], "dict_debug_2",
            memory.valid_from, run.id,
        )
        print("outcome:", outcome)
        print("memory object status right after (same python object?):", memory.status, "id match:", id(memory))

        await session.refresh(memory)
        print("memory status after refresh:", memory.status)

        await trans.rollback()

asyncio.run(main())
