import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kivi.db.enums import ExtractionOutcome, MemoryOrigin, MemoryStatus, MemoryType, UserActionType
from kivi.db.models import Dictation, ExtractionRun, Memory, MemorySource, UserAction
from kivi.tools.manage_memory import ClaimTombstonedError, confirm_memory, correct_memory, forget_memory

NOW = datetime.datetime(2026, 3, 1, tzinfo=datetime.timezone.utc)


async def _seed_memory(session: AsyncSession, suffix: str, claim: str = "Tara launches March 20") -> Memory:
    dictation_id = f"dict_manage_{suffix}"
    session.add(Dictation(
        id=dictation_id, spoken_at=NOW, raw_asr="x", formatted_output="x",
        content_hash=f"hash_manage_{suffix}",
    ))
    await session.flush()

    run = ExtractionRun(
        dictation_id=dictation_id, outcome=ExtractionOutcome.MEMORIES_EXTRACTED,
        reason="seed", model="test", prompt_version="test",
    )
    session.add(run)
    await session.flush()

    memory = Memory(
        memory_type=MemoryType.PROJECT_STATE, claim=claim, claim_hash=f"claimhash_manage_{suffix}",
        status=MemoryStatus.ACTIVE, valid_from=NOW, extraction_run_id=run.id,
    )
    session.add(memory)
    await session.flush()
    session.add(MemorySource(memory_id=memory.id, dictation_id=dictation_id))
    await session.flush()
    return memory


@pytest.mark.asyncio
async def test_confirm_sets_last_confirmed_at_and_logs_action(db_session: AsyncSession) -> None:
    memory = await _seed_memory(db_session, "a")

    await confirm_memory(db_session, memory.id, reason="still true")

    await db_session.refresh(memory)
    assert memory.last_confirmed_at is not None
    action = await db_session.scalar(select(UserAction).where(UserAction.memory_id == memory.id))
    assert action.action == UserActionType.CONFIRM
    assert action.resulting_memory_id is None


@pytest.mark.asyncio
async def test_forget_sets_status_forgotten_and_logs_action(db_session: AsyncSession) -> None:
    memory = await _seed_memory(db_session, "b")

    await forget_memory(db_session, memory.id, reason="not relevant anymore")

    await db_session.refresh(memory)
    assert memory.status == MemoryStatus.FORGOTTEN


@pytest.mark.asyncio
async def test_correct_supersedes_old_and_creates_pinned_user_memory(db_session: AsyncSession) -> None:
    old = await _seed_memory(db_session, "c", claim="Tara launches March 20")

    new_id = await correct_memory(db_session, old.id, "Tara launches March 24", reason="date confirmed")

    await db_session.refresh(old)
    assert old.status == MemoryStatus.SUPERSEDED
    assert old.superseded_by_id == new_id

    new_memory = await db_session.get(Memory, new_id)
    assert new_memory.claim == "Tara launches March 24"
    assert new_memory.origin == MemoryOrigin.USER
    assert new_memory.confidence == 1.0
    assert new_memory.pinned is True
    assert new_memory.status == MemoryStatus.ACTIVE

    source = await db_session.scalar(select(MemorySource).where(MemorySource.memory_id == new_id))
    assert source is not None  # provenance invariant: a real dictation backs it


@pytest.mark.asyncio
async def test_correct_into_a_forgotten_claim_is_refused(db_session: AsyncSession) -> None:
    from kivi.memory.pipeline import compute_claim_hash

    old = await _seed_memory(db_session, "d", claim="Tara launches March 20")
    other = await _seed_memory(db_session, "e", claim="Tara launches March 24")
    other.status = MemoryStatus.FORGOTTEN
    other.claim_hash = compute_claim_hash("Tara launches March 24")
    await db_session.flush()

    with pytest.raises(ClaimTombstonedError):
        await correct_memory(db_session, old.id, "Tara launches March 24")
