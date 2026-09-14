from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from kivi.config import settings
from kivi.db.enums import SufficiencyVerdict
from kivi.models.provider import Completion, ModelProvider
from kivi.retrieval.fusion import FusedCandidate

PROMPT_VERSION = "sufficiency_v1"
_PROMPT = (Path(__file__).parent.parent / "memory" / "prompts" / f"{PROMPT_VERSION}.md").read_text(
    encoding="utf-8"
)


class _LLMVerdict(BaseModel):
    verdict: Literal["sufficient", "partial", "insufficient"]
    reason: str


@dataclass
class SufficiencyResult:
    verdict: SufficiencyVerdict
    reason: str


async def run_sufficiency(
    provider: ModelProvider,
    question: str,
    selected: list[FusedCandidate],
    unresolved_entity_surface_forms: list[str],
) -> tuple[SufficiencyResult, list[Completion]]:
    """A separate stage that runs BEFORE answer generation. Three deterministic
    rules can each independently force abstention without ever calling the
    model — never trust a single LLM judgment call for the abstention boundary,
    the same principle policy.py applies on the write path. Only when none of
    them fire does a cheap model call render the qualitative verdict."""

    # Rule 1: a named thing in the question resolved to nothing. Per CLAUDE.md
    # §7, that's a strong abstention signal, not license to fuzzy-match.
    if unresolved_entity_surface_forms:
        names = ", ".join(unresolved_entity_surface_forms)
        return (
            SufficiencyResult(
                SufficiencyVerdict.INSUFFICIENT,
                f"no known entity matches {names!r} — nothing to retrieve against",
            ),
            [],
        )

    if not selected:
        return SufficiencyResult(SufficiencyVerdict.INSUFFICIENT, "no candidate memories were found"), []

    # Rule 2: best fused score below a configurable floor.
    best_rrf_score = max(fc.rrf_score for fc in selected)
    if best_rrf_score < settings.sufficiency_min_fused_score:
        return (
            SufficiencyResult(
                SufficiencyVerdict.INSUFFICIENT,
                f"best fused score {best_rrf_score:.4f} is below the floor "
                f"{settings.sufficiency_min_fused_score}",
            ),
            [],
        )

    # Rule 3: exactly one supporting memory, and it's a low-confidence one.
    if len(selected) == 1 and selected[0].confidence < settings.sufficiency_min_memory_confidence:
        return (
            SufficiencyResult(
                SufficiencyVerdict.INSUFFICIENT,
                f"only one supporting memory, at confidence {selected[0].confidence:.2f} "
                f"(floor {settings.sufficiency_min_memory_confidence})",
            ),
            [],
        )

    memories_desc = "\n".join(f"- id={fc.memory_id}: {fc.memory.claim}" for fc in selected)
    messages = [
        {"role": "system", "content": _PROMPT},
        {"role": "user", "content": f"Question: {question}\n\nRetrieved memories:\n{memories_desc}"},
    ]
    completion = await provider.complete(messages, schema=_LLMVerdict, tier="extraction")
    llm_verdict = _LLMVerdict.model_validate_json(completion.content)
    return (
        SufficiencyResult(SufficiencyVerdict(llm_verdict.verdict), llm_verdict.reason),
        [completion],
    )
