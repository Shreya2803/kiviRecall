import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from kivi.db.enums import EntityType
from kivi.db.models import Entity
from kivi.memory.resolve import NEW_ENTITY_CONFIDENCE, resolve_entity


class _NullProvider:
    """Never called: only exact/transliteration matches are exercised here."""

    name = "null"

    async def complete(self, *args, **kwargs):
        raise AssertionError("provider should not be called for an exact match")


@pytest.mark.asyncio
async def test_new_entity_starts_low_confidence_and_flagged(db_session: AsyncSession) -> None:
    resolved, _ = await resolve_entity(
        db_session, _NullProvider(), "Zephyrion9000", EntityType.PROJECT, context="Zephyrion9000 launched today",
    )

    entity = await db_session.get(Entity, resolved.entity_id)
    assert entity.confidence == NEW_ENTITY_CONFIDENCE
    assert entity.needs_review is True


@pytest.mark.asyncio
async def test_second_exact_match_graduates_confidence(db_session: AsyncSession) -> None:
    """Regression: confidence used to stay at its creation-time value forever,
    permanently discounting every entity-anchored memory in retrieval's fusion
    boost relative to entity-less ones — the opposite of the join's purpose."""
    first, _ = await resolve_entity(
        db_session, _NullProvider(), "Zephyrion9000", EntityType.PROJECT, context="Zephyrion9000 launched today",
    )
    second, _ = await resolve_entity(
        db_session, _NullProvider(), "Zephyrion9000", EntityType.PROJECT, context="Zephyrion9000 shipped the first cohort",
    )

    assert second.entity_id == first.entity_id
    entity = await db_session.get(Entity, first.entity_id)
    assert entity.confidence == 1.0
    assert entity.needs_review is False
