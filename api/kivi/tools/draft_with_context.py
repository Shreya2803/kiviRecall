from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from kivi.config import settings
from kivi.db.enums import EntityType
from kivi.memory.resolve import resolve_entity
from kivi.models.embeddings import embed_async
from kivi.models.provider import Completion, ModelProvider
from kivi.retrieval.candidates import entity_candidates, lexical_candidates, semantic_candidates
from kivi.retrieval.fusion import fuse
from kivi.retrieval.parse import run_parse

PROMPT_VERSION = "draft_v2"
_PROMPT = (Path(__file__).parent.parent / "memory" / "prompts" / f"{PROMPT_VERSION}.md").read_text(
    encoding="utf-8"
)


class _LLMDraft(BaseModel):
    draft: str
    cited_memory_ids: list[int]


@dataclass
class DraftResult:
    draft: str
    cited_memory_ids: list[int] = field(default_factory=list)
    used_memory_ids: list[int] = field(default_factory=list)
    completions: list[Completion] = field(default_factory=list)


async def draft_with_context(
    session: AsyncSession, provider: ModelProvider, request: str, now,
) -> DraftResult:
    """The demo: drafts using remembered project state the person never
    restated in the request itself. Reuses the same resolve→candidates→fuse
    pipeline recall uses, but the final call asks for a draft, not an answer
    — no sufficiency gate, no abstention: a draft with fewer specifics is still
    a valid draft, unlike an answer with no support."""
    completions: list[Completion] = []

    parsed, parse_completion = await run_parse(provider, request, now)
    completions.append(parse_completion)

    resolved_entity_ids: list[int] = []
    for mention in parsed.entities:
        resolved, resolve_completions = await resolve_entity(
            session, provider, mention.surface_form, EntityType(mention.entity_type),
            context=request, create_if_missing=False,
        )
        completions.extend(resolve_completions)
        if resolved is not None:
            resolved_entity_ids.append(resolved.entity_id)
    await session.commit()

    embedding = (await embed_async([request]))[0]
    limit = settings.candidate_limit_per_generator
    entity_cands = await entity_candidates(session, resolved_entity_ids, limit)
    lexical_cands = await lexical_candidates(session, request, limit)
    semantic_cands = await semantic_candidates(session, embedding, limit)

    selected, _ = await fuse(
        session, entity_cands, lexical_cands, semantic_cands,
        k=settings.rrf_k, top_n=settings.fused_top_n,
    )

    if selected:
        context_lines = ["Remembered context (each dated by when it was recorded):"]
        for fc in selected:
            context_lines.append(f"- id={fc.memory_id} (recorded {fc.memory.valid_from.date()}): {fc.memory.claim}")
    else:
        context_lines = ["Remembered context: none relevant — draft generically."]

    messages = [
        {"role": "system", "content": _PROMPT},
        {"role": "user", "content": f"Request: {request}\n\n" + "\n".join(context_lines)},
    ]
    completion = await provider.complete(messages, schema=_LLMDraft, tier="answer")
    completions.append(completion)
    llm_draft = _LLMDraft.model_validate_json(completion.content)

    valid_ids = {fc.memory_id for fc in selected}
    cited = [mid for mid in llm_draft.cited_memory_ids if mid in valid_ids]

    return DraftResult(
        draft=llm_draft.draft, cited_memory_ids=cited,
        used_memory_ids=[fc.memory_id for fc in selected], completions=completions,
    )
