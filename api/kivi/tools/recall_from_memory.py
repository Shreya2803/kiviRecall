import datetime
import time
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from kivi.config import settings
from kivi.db.enums import EntityType
from kivi.memory.extract import EntityMention
from kivi.memory.resolve import resolve_entity
from kivi.models.embeddings import embed_async
from kivi.models.provider import Completion, ModelProvider
from kivi.retrieval.answer import run_answer
from kivi.retrieval.candidates import entity_candidates, lexical_candidates, semantic_candidates
from kivi.retrieval.fusion import fuse
from kivi.retrieval.sufficiency import run_sufficiency


@dataclass
class RecallResult:
    answer: str
    cited_memory_ids: list[int] = field(default_factory=list)
    selected_memory_ids: list[int] = field(default_factory=list)
    resolved_entity_ids: list[int] = field(default_factory=list)
    unresolved_entities: list[str] = field(default_factory=list)
    candidates_trace: list[dict] = field(default_factory=list)
    sufficiency_verdict: str = ""
    sufficiency_reason: str = ""
    completions: list[Completion] = field(default_factory=list)
    retrieval_latency_ms: int = 0
    generation_latency_ms: int = 0


async def recall_from_memory(
    session: AsyncSession,
    provider: ModelProvider,
    question: str,
    entities: list[EntityMention],
    *,
    time_before: datetime.datetime | None = None,
    time_after: datetime.datetime | None = None,
    types: list[str] | None = None,
) -> RecallResult:
    """Answer a question about durable knowledge Kivi has learned. Resolves the
    named entities in the question (read-path — never mints a new entity),
    joins on them to recover distributed facts, fuses with lexical/semantic
    recall, runs the sufficiency gate, and only then asks the model to answer.
    """
    start = time.monotonic()
    completions: list[Completion] = []

    resolved_entity_ids: list[int] = []
    unresolved: list[str] = []
    for mention in entities:
        resolved, resolve_completions = await resolve_entity(
            session, provider, mention.surface_form, EntityType(mention.entity_type),
            context=question, create_if_missing=False,
        )
        completions.extend(resolve_completions)
        if resolved is not None:
            resolved_entity_ids.append(resolved.entity_id)
        else:
            unresolved.append(mention.surface_form)
    await session.commit()  # persist any confidence graduation resolve_entity applied

    embedding = (await embed_async([question]))[0]
    limit = settings.candidate_limit_per_generator
    entity_cands = await entity_candidates(session, resolved_entity_ids, limit)
    lexical_cands = await lexical_candidates(session, question, limit)
    semantic_cands = await semantic_candidates(session, embedding, limit)

    selected, candidates_trace = await fuse(
        session, entity_cands, lexical_cands, semantic_cands,
        k=settings.rrf_k, top_n=settings.fused_top_n,
        time_before=time_before, time_after=time_after, types=types,
    )
    retrieval_ms = int((time.monotonic() - start) * 1000)

    suff_result, suff_completions = await run_sufficiency(provider, question, selected, unresolved)
    completions.extend(suff_completions)

    t_gen = time.monotonic()
    answer_result, answer_completion = await run_answer(
        session, provider, question, suff_result.verdict, suff_result.reason, selected,
    )
    completions.append(answer_completion)
    generation_ms = int((time.monotonic() - t_gen) * 1000)

    return RecallResult(
        answer=answer_result.text,
        cited_memory_ids=answer_result.cited_memory_ids,
        selected_memory_ids=[fc.memory_id for fc in selected],
        resolved_entity_ids=resolved_entity_ids,
        unresolved_entities=unresolved,
        candidates_trace=candidates_trace,
        sufficiency_verdict=suff_result.verdict.value,
        sufficiency_reason=suff_result.reason,
        completions=completions,
        retrieval_latency_ms=retrieval_ms,
        generation_latency_ms=generation_ms,
    )
