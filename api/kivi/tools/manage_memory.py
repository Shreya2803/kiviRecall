import datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kivi.db.enums import ExtractionOutcome, MemoryOrigin, MemoryStatus, UserActionType
from kivi.db.models import Dictation, ExtractionRun, Memory, MemoryEntity, MemorySource, UserAction
from kivi.ingest.jsonl_reader import compute_content_hash
from kivi.memory.pipeline import compute_claim_hash


class MemoryNotFoundError(LookupError):
    pass


class ClaimTombstonedError(ValueError):
    """The corrected text hashes to a claim the person already forgot once —
    forget-tombstones survive more than just re-import."""


async def confirm_memory(session: AsyncSession, memory_id: int, reason: str | None = None) -> None:
    memory = await session.get(Memory, memory_id)
    if memory is None:
        raise MemoryNotFoundError(f"memory {memory_id} not found")
    now = datetime.datetime.now(datetime.timezone.utc)
    memory.last_confirmed_at = now
    session.add(UserAction(memory_id=memory.id, action=UserActionType.CONFIRM, reason=reason))
    await session.commit()


async def forget_memory(session: AsyncSession, memory_id: int, reason: str | None = None) -> None:
    memory = await session.get(Memory, memory_id)
    if memory is None:
        raise MemoryNotFoundError(f"memory {memory_id} not found")
    memory.status = MemoryStatus.FORGOTTEN
    session.add(UserAction(memory_id=memory.id, action=UserActionType.FORGET, reason=reason))
    await session.commit()


async def correct_memory(
    session: AsyncSession, memory_id: int, corrected_claim: str, reason: str | None = None
) -> int:
    """Supersede old, create new with origin='user', confidence 1.0, pinned.

    A user-typed correction has no dictation behind it, but CLAUDE.md's
    provenance invariant (>=1 memory_source row, every memory traces back to
    an extraction_run) is non-negotiable regardless of origin — so this
    synthesizes a minimal Dictation + ExtractionRun to anchor it, exactly the
    shape the write pipeline already produces for an extracted memory.
    """
    old = await session.get(Memory, memory_id)
    if old is None:
        raise MemoryNotFoundError(f"memory {memory_id} not found")

    claim_hash = compute_claim_hash(corrected_claim)
    tombstoned = await session.scalar(
        select(Memory).where(Memory.claim_hash == claim_hash, Memory.status == MemoryStatus.FORGOTTEN)
    )
    if tombstoned is not None:
        raise ClaimTombstonedError(f"claim_hash {claim_hash} was forgotten and cannot be recreated")

    now = datetime.datetime.now(datetime.timezone.utc)
    dictation_id = f"correction_{uuid4().hex[:16]}"
    session.add(Dictation(
        id=dictation_id, spoken_at=now, raw_asr=corrected_claim, formatted_output=corrected_claim,
        app_context="Hey Kivi correction",
        content_hash=compute_content_hash(dictation_id, corrected_claim),
    ))
    await session.flush()

    run = ExtractionRun(
        dictation_id=dictation_id, outcome=ExtractionOutcome.MEMORIES_EXTRACTED,
        reason=f"user correction: {reason or 'no reason given'}",
        memories_created_count=1, model="user", prompt_version="n/a",
    )
    session.add(run)
    await session.flush()

    old_entity_ids = (
        await session.execute(select(MemoryEntity.entity_id).where(MemoryEntity.memory_id == old.id))
    ).scalars().all()

    new_memory = Memory(
        memory_type=old.memory_type, claim=corrected_claim, claim_hash=claim_hash,
        status=MemoryStatus.ACTIVE, valid_from=now, extraction_run_id=run.id,
        origin=MemoryOrigin.USER, confidence=1.0, pinned=True,
    )
    session.add(new_memory)
    await session.flush()
    session.add(MemorySource(memory_id=new_memory.id, dictation_id=dictation_id))
    for entity_id in old_entity_ids:
        session.add(MemoryEntity(memory_id=new_memory.id, entity_id=entity_id))

    old.status = MemoryStatus.SUPERSEDED
    old.valid_to = now
    old.superseded_by_id = new_memory.id

    session.add(UserAction(
        memory_id=old.id, action=UserActionType.CORRECT, resulting_memory_id=new_memory.id, reason=reason,
    ))
    await session.commit()
    return new_memory.id
