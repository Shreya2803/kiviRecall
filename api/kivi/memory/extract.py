import logging
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from kivi.models.provider import Completion, ModelProvider

logger = logging.getLogger(__name__)

PROMPT_VERSION = "extract_v2"
_PROMPT = (Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.md").read_text(encoding="utf-8")

ClaimCategory = Literal[
    # keep — matches MemoryType in kivi.db.enums exactly
    "project_state", "role", "decision", "commitment", "terminology", "preference",
    # reject — extracted anyway so policy.py's code-level filter can catch a
    # prompt failure; never rely on the prompt alone to enforce the boundary.
    "mood_emotional", "health_personal", "opinion_about_colleague",
    "financial_or_family", "ambiguous_or_unclear",
]

KEEP_CATEGORIES = frozenset({
    "project_state", "role", "decision", "commitment", "terminology", "preference",
})

EntityTypeLabel = Literal["person", "project", "vendor", "term"]


class EntityMention(BaseModel):
    surface_form: str  # exactly as written in the dictation, not normalised
    entity_type: EntityTypeLabel


class ExtractedClaim(BaseModel):
    text: str
    category: ClaimCategory
    source_span: str
    char_start: int
    char_end: int
    entities: list[EntityMention]


class ExtractionResult(BaseModel):
    claims: list[ExtractedClaim]
    reason: str


# Wire-level twins with str-typed enums: the model occasionally emits a
# near-miss category or entity_type (e.g. "term" for "terminology" — it
# conflates the claim category with the entity_type enum, which does have a
# "term" value). One bad enum value must not fail the whole dictation's
# extraction, so this layer stays lenient and code below normalizes/drops
# individual claims — never trust the prompt alone to enforce the schema.
class _WireEntityMention(BaseModel):
    surface_form: str
    entity_type: str


class _WireClaim(BaseModel):
    text: str
    category: str
    source_span: str
    char_start: int
    char_end: int
    entities: list[_WireEntityMention] = []


class _WireExtractionResult(BaseModel):
    claims: list[_WireClaim]
    reason: str


_CATEGORY_ALIASES: dict[str, str] = {
    "term": "terminology",
    "project": "project_state",
}


def _parse_claim(wire: _WireClaim) -> ExtractedClaim | None:
    category = _CATEGORY_ALIASES.get(wire.category, wire.category)
    try:
        return ExtractedClaim(
            text=wire.text,
            category=category,
            source_span=wire.source_span,
            char_start=wire.char_start,
            char_end=wire.char_end,
            entities=[
                EntityMention(surface_form=e.surface_form, entity_type=e.entity_type)
                for e in wire.entities
            ],
        )
    except ValidationError:
        logger.warning("dropping one claim: unrecognized category %r", wire.category)
        return None


# Code-level backstop for extract_v2.md rule 2: never trust the prompt alone
# for a mandatory rule, the same principle policy.py applies to the retention
# category. Bare third-person pronouns/referents with no named person entity
# in the same claim have nothing to resolve to and are not self-contained.
_UNRESOLVED_PRONOUNS = frozenset({
    # English third-person / demonstrative
    "it", "its", "they", "them", "their", "theirs", "he", "him", "his", "she",
    "her", "hers", "this", "that", "these", "those",
    # Hinglish (Latin-script)
    "voh", "woh", "unka", "unki", "unke", "uska", "uski", "uske", "unhone",
    "iska", "iski", "iske", "usne", "usko", "usse",
    # Devanagari
    "वह", "उनकी", "उनका", "उनके", "उसने", "उसे", "इसका", "इसकी", "इसके",
})

_WORD_RE = re.compile(r"[\wऀ-ॿ]+", re.UNICODE)


def _has_unresolved_pronoun(claim: ExtractedClaim) -> bool:
    if any(e.entity_type == "person" for e in claim.entities):
        return False  # a named person is present — the pronoun likely resolves to them
    tokens = {t.lower() for t in _WORD_RE.findall(claim.text)}
    return not tokens.isdisjoint(_UNRESOLVED_PRONOUNS)


def _ground_claim(claim: ExtractedClaim, source_text: str) -> ExtractedClaim | None:
    """Never trust an LLM's char offsets blindly. Re-locate them against the real
    text if they drifted; drop the claim if the span can't be found at all — an
    ungrounded claim has no real provenance to cite."""
    if (
        0 <= claim.char_start < claim.char_end <= len(source_text)
        and source_text[claim.char_start:claim.char_end] == claim.source_span
    ):
        return claim
    idx = source_text.find(claim.source_span)
    if idx == -1:
        return None
    return claim.model_copy(update={"char_start": idx, "char_end": idx + len(claim.source_span)})


async def run_extraction(
    provider: ModelProvider, dictation_text: str
) -> tuple[ExtractionResult, Completion]:
    messages = [
        {"role": "system", "content": _PROMPT},
        {"role": "user", "content": dictation_text},
    ]
    completion = await provider.complete(messages, schema=_WireExtractionResult, tier="extraction")
    wire = _WireExtractionResult.model_validate_json(completion.content)

    claims: list[ExtractedClaim] = []
    for wc in wire.claims:
        claim = _parse_claim(wc)
        if claim is None:
            continue
        if _has_unresolved_pronoun(claim):
            logger.warning("dropping one claim: unresolved pronoun with no named referent")
            continue
        grounded = _ground_claim(claim, dictation_text)
        if grounded is not None:
            claims.append(grounded)

    result = ExtractionResult(claims=claims, reason=wire.reason)
    return result, completion
