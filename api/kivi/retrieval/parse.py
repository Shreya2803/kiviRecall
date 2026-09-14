import datetime
import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from kivi.memory.extract import EntityMention
from kivi.models.provider import Completion, ModelProvider

logger = logging.getLogger(__name__)

PROMPT_VERSION = "parse_v1"
_PROMPT = (Path(__file__).parent.parent / "memory" / "prompts" / f"{PROMPT_VERSION}.md").read_text(
    encoding="utf-8"
)

Intent = Literal["find", "recall", "act", "chitchat"]
MemoryTypeLabel = Literal[
    "project_state", "role", "decision", "commitment", "terminology", "preference"
]
OutOfBoundsCategory = Literal[
    "mood_emotional", "health_personal", "opinion_about_colleague", "financial_or_family"
]

_VALID_INTENTS = {"find", "recall", "act", "chitchat"}
_VALID_TYPES = {"project_state", "role", "decision", "commitment", "terminology", "preference"}
_VALID_OOB = {"mood_emotional", "health_personal", "opinion_about_colleague", "financial_or_family"}


class ParsedQuery(BaseModel):
    intent: Intent
    entities: list[EntityMention]
    time_before: datetime.datetime | None
    time_after: datetime.datetime | None
    types: list[MemoryTypeLabel] | None
    app_context: str | None
    out_of_bounds_category: OutOfBoundsCategory | None


# Wire-level twin, lenient on every enum-ish field — same reasoning as
# extract.py's wire model: never let one near-miss value fail the whole parse.
class _WireEntityMention(BaseModel):
    surface_form: str
    entity_type: str


class _WireParsedQuery(BaseModel):
    intent: str
    entities: list[_WireEntityMention] = []
    time_before: str | None = None
    time_after: str | None = None
    types: list[str] | None = None
    app_context: str | None = None
    out_of_bounds_category: str | None = None


def _coerce(wire: _WireParsedQuery) -> ParsedQuery:
    intent: Intent = wire.intent if wire.intent in _VALID_INTENTS else "recall"
    if wire.intent not in _VALID_INTENTS:
        logger.warning("parse: unrecognized intent %r, defaulting to recall", wire.intent)

    entities: list[EntityMention] = []
    for e in wire.entities:
        try:
            entities.append(EntityMention(surface_form=e.surface_form, entity_type=e.entity_type))
        except ValidationError:
            logger.warning("parse: dropping entity with unrecognized entity_type %r", e.entity_type)

    def _parse_time(raw: str | None) -> datetime.datetime | None:
        if not raw:
            return None
        try:
            return datetime.datetime.fromisoformat(raw)
        except ValueError:
            logger.warning("parse: unparseable datetime %r, ignoring", raw)
            return None

    types: list[MemoryTypeLabel] | None = None
    if wire.types:
        valid = [t for t in wire.types if t in _VALID_TYPES]
        types = valid or None

    oob = wire.out_of_bounds_category if wire.out_of_bounds_category in _VALID_OOB else None

    return ParsedQuery(
        intent=intent,
        entities=entities,
        time_before=_parse_time(wire.time_before),
        time_after=_parse_time(wire.time_after),
        types=types,
        app_context=wire.app_context,
        out_of_bounds_category=oob,
    )


async def run_parse(
    provider: ModelProvider, question: str, now: datetime.datetime
) -> tuple[ParsedQuery, Completion]:
    messages = [
        {"role": "system", "content": _PROMPT},
        {"role": "user", "content": f"Reference timestamp (now): {now.isoformat()}\nQuestion: {question}"},
    ]
    completion = await provider.complete(messages, schema=_WireParsedQuery, tier="extraction")
    wire = _WireParsedQuery.model_validate_json(completion.content)
    return _coerce(wire), completion
