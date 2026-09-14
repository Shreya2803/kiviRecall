import re
from dataclasses import dataclass
from pathlib import Path

from indic_transliteration import sanscript
from indic_transliteration.sanscript import transliterate
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kivi.db.enums import AliasMatchMethod, EntityType
from kivi.db.models import Entity, EntityAlias
from kivi.models.provider import Completion, ModelProvider

PROMPT_VERSION = "disambiguate_v1"
_DISAMBIG_PROMPT = (Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.md").read_text(encoding="utf-8")

_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")

TRIGRAM_THRESHOLD = 0.4
NEW_ENTITY_CONFIDENCE = 0.3
DISAMBIGUATED_CONFIDENCE = 0.7
MAX_TIE_CANDIDATES = 5


def _strip_to_key(text: str) -> str:
    return _NON_ALNUM_RE.sub("", text.strip().lower())


def normalize_alias(surface_form: str) -> str:
    """Transliterates Devanagari to Latin before stripping — a no-op for text
    that's already Latin-script, so this stays cheap for the common case."""
    t = surface_form.strip().lower()
    if _DEVANAGARI_RE.search(t):
        t = transliterate(t, sanscript.DEVANAGARI, sanscript.ITRANS).lower()
    return _NON_ALNUM_RE.sub("", t)


class DisambiguationChoice(BaseModel):
    chosen_entity_id: int | None
    reason: str


@dataclass
class ResolvedEntity:
    entity_id: int
    is_new: bool
    match_method: AliasMatchMethod
    confidence: float


async def _lookup_exact(session: AsyncSession, normalized: str) -> EntityAlias | None:
    return await session.scalar(select(EntityAlias).where(EntityAlias.normalized == normalized))


async def _graduate(session: AsyncSession, entity_id: int) -> None:
    """A new entity starts low-confidence and flagged (CLAUDE.md §7: bias toward
    under-merging — we weren't sure it was real). An exact/transliteration match
    is unambiguous by construction, so once one fires against an entity a second
    time, the original doubt is resolved and it should stop being penalised
    forever. Without this, retrieval's confidence boost would permanently
    discount every entity-anchored memory relative to entity-less ones — the
    opposite of what the entity join is supposed to do."""
    entity = await session.get(Entity, entity_id)
    if entity is not None and (entity.confidence < 1.0 or entity.needs_review):
        entity.confidence = 1.0
        entity.needs_review = False


async def _disambiguate(
    provider: ModelProvider, surface_form: str, context: str, candidates: list[Entity]
) -> tuple[int | None, Completion]:
    candidate_desc = "\n".join(f"- id={c.id}: {c.canonical_name} ({c.entity_type.value})" for c in candidates)
    messages = [
        {"role": "system", "content": _DISAMBIG_PROMPT},
        {
            "role": "user",
            "content": f"Mention: {surface_form!r}\nSentence: {context!r}\nCandidates:\n{candidate_desc}",
        },
    ]
    completion = await provider.complete(messages, schema=DisambiguationChoice, tier="extraction")
    choice = DisambiguationChoice.model_validate_json(completion.content)
    return choice.chosen_entity_id, completion


async def resolve_entity(
    session: AsyncSession,
    provider: ModelProvider,
    surface_form: str,
    entity_type: EntityType,
    context: str,
    create_if_missing: bool = True,
) -> tuple[ResolvedEntity | None, list[Completion]]:
    """The four passes from CLAUDE.md §7, cheapest first. `create_if_missing`
    lets the read path reuse this exact function without ever minting a new
    entity for a question — nothing resolved there is a strong abstention
    signal, not license to fuzzy-match the nearest topic."""
    completions: list[Completion] = []

    raw_key = _strip_to_key(surface_form) if not _DEVANAGARI_RE.search(surface_form) else None

    # Pass 1: exact match, no transliteration needed.
    if raw_key:
        existing = await _lookup_exact(session, raw_key)
        if existing:
            await _graduate(session, existing.entity_id)
            return ResolvedEntity(existing.entity_id, False, AliasMatchMethod.EXACT, 1.0), completions

    # Pass 2: transliteration-normalised match.
    translit_key = normalize_alias(surface_form)
    if translit_key and translit_key != raw_key:
        existing = await _lookup_exact(session, translit_key)
        if existing:
            await _graduate(session, existing.entity_id)
            return (
                ResolvedEntity(existing.entity_id, False, AliasMatchMethod.TRANSLITERATION, 1.0),
                completions,
            )

    normalized = translit_key or raw_key
    if not normalized:
        return None, completions  # nothing to resolve against — drop, don't guess

    # Pass 3: phonetic + trigram similarity, deduped to distinct entities.
    phonetic_key = await session.scalar(select(func.dmetaphone(normalized)))
    rows = (
        await session.execute(
            select(
                EntityAlias.entity_id,
                func.max(func.similarity(EntityAlias.normalized, normalized)).label("sim"),
            )
            .where(
                (func.similarity(EntityAlias.normalized, normalized) > TRIGRAM_THRESHOLD)
                | (EntityAlias.phonetic_key == phonetic_key)
            )
            .group_by(EntityAlias.entity_id)
            .order_by(func.max(func.similarity(EntityAlias.normalized, normalized)).desc())
        )
    ).all()

    if len(rows) == 1:
        entity_id, sim = rows[0]
        return (
            ResolvedEntity(entity_id, False, AliasMatchMethod.PHONETIC_TRIGRAM, float(min(sim, 0.95))),
            completions,
        )

    if len(rows) >= 2:
        # Pass 4: model-assisted, only because two or more candidates tied.
        top_ids = [r[0] for r in rows[:MAX_TIE_CANDIDATES]]
        candidates = (await session.execute(select(Entity).where(Entity.id.in_(top_ids)))).scalars().all()
        chosen_id, completion = await _disambiguate(provider, surface_form, context, candidates)
        completions.append(completion)
        if chosen_id is not None:
            return (
                ResolvedEntity(chosen_id, False, AliasMatchMethod.MODEL_ASSISTED, DISAMBIGUATED_CONFIDENCE),
                completions,
            )
        return None, completions  # model couldn't decide either — drop, don't guess

    # No candidates at all.
    if not create_if_missing:
        return None, completions

    entity = Entity(
        entity_type=entity_type,
        canonical_name=surface_form,
        confidence=NEW_ENTITY_CONFIDENCE,
        needs_review=True,
    )
    session.add(entity)
    await session.flush()
    session.add(EntityAlias(
        entity_id=entity.id, alias=surface_form, normalized=normalized, match_method=AliasMatchMethod.EXACT,
    ))
    await session.flush()
    return (
        ResolvedEntity(entity.id, True, AliasMatchMethod.EXACT, NEW_ENTITY_CONFIDENCE),
        completions,
    )
