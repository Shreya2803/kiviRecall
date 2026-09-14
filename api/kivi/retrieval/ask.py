import datetime
import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from kivi.config import settings
from kivi.db.enums import EntityType, SufficiencyVerdict
from kivi.db.models import Dictation, QueryTrace
from kivi.models.embeddings import embed_async
from kivi.models.provider import Completion, ModelProvider
from kivi.retrieval import answer as answer_module
from kivi.retrieval import parse as parse_module
from kivi.retrieval import sufficiency as sufficiency_module
from kivi.retrieval.answer import run_answer
from kivi.retrieval.candidates import entity_candidates, lexical_candidates, semantic_candidates
from kivi.retrieval.fusion import fuse
from kivi.retrieval.parse import ParsedQuery, run_parse
from kivi.memory.resolve import resolve_entity
from kivi.retrieval.sufficiency import run_sufficiency


@dataclass
class AskResult:
    question: str
    route: str
    parsed: ParsedQuery
    resolved_entity_ids: list[int] = field(default_factory=list)
    unresolved_entities: list[str] = field(default_factory=list)
    candidates_trace: list[dict] = field(default_factory=list)
    selected_memory_ids: list[int] = field(default_factory=list)
    sufficiency_verdict: str = ""
    sufficiency_reason: str = ""
    answer: str = ""
    cited_memory_ids: list[int] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    retrieval_latency_ms: int = 0
    generation_latency_ms: int = 0
    latency_ms: int = 0
    trace_id: int | None = None


def _answer_model_name() -> str:
    return settings.answer_model if settings.sarvam_api_key else settings.gemini_answer_model


async def _persist_trace(
    session_factory: async_sessionmaker,
    *,
    question: str,
    route: str,
    filters: dict,
    candidates: list[dict],
    selected_memory_ids: list[int] | None,
    cited_memory_ids: list[int] | None,
    verdict: SufficiencyVerdict,
    verdict_reason: str,
    answer_text: str,
    completions: list[Completion],
    retrieval_latency_ms: int,
    generation_latency_ms: int,
) -> int:
    async with session_factory() as session:
        trace = QueryTrace(
            question=question,
            route=route,
            filters=filters,
            candidates=candidates,
            selected_memory_ids=selected_memory_ids or None,
            cited_memory_ids=cited_memory_ids or None,
            sufficiency_verdict=verdict,
            sufficiency_reason=verdict_reason,
            answer=answer_text,
            model=_answer_model_name(),
            prompt_version=answer_module.PROMPT_VERSION,
            input_tokens=sum(c.tokens_in for c in completions),
            output_tokens=sum(c.tokens_out for c in completions),
            estimated_cost_usd=sum(c.cost_usd for c in completions),
            retrieval_latency_ms=retrieval_latency_ms,
            generation_latency_ms=generation_latency_ms,
            latency_ms=retrieval_latency_ms + generation_latency_ms,
        )
        session.add(trace)
        await session.commit()
        return trace.id


async def _handle_find(
    session_factory: async_sessionmaker, parsed: ParsedQuery, question: str
) -> tuple[str, SufficiencyVerdict, str, dict]:
    """Intent 'find' never touches memory or an embedding — a direct dictation
    lookup, fast by construction (CLAUDE.md: ~40ms, not 1.5s)."""
    async with session_factory() as session:
        stmt = select(Dictation).order_by(Dictation.spoken_at.desc()).limit(10)
        if parsed.app_context:
            stmt = stmt.where(Dictation.app_context.ilike(f"%{parsed.app_context}%"))
        if parsed.time_after:
            stmt = stmt.where(Dictation.spoken_at >= parsed.time_after)
        if parsed.time_before:
            stmt = stmt.where(Dictation.spoken_at <= parsed.time_before)
        rows = (await session.execute(stmt)).scalars().all()

    if not rows:
        return "I didn't find anything you dictated matching that.", SufficiencyVerdict.INSUFFICIENT, "no matching dictations", {"found_dictation_ids": []}

    lines = [f"Found {len(rows)} dictation(s):"]
    for d in rows:
        lines.append(f"- [{d.spoken_at.isoformat()}] ({d.app_context or 'unknown app'}) {d.formatted_output}")
    return "\n".join(lines), SufficiencyVerdict.SUFFICIENT, "direct dictation lookup", {
        "found_dictation_ids": [d.id for d in rows]
    }


async def answer_question(
    session_factory: async_sessionmaker,
    provider: ModelProvider,
    question: str,
    now: datetime.datetime | None = None,
) -> AskResult:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    start = time.monotonic()
    completions: list[Completion] = []

    parsed, parse_completion = await run_parse(provider, question, now)
    completions.append(parse_completion)

    base_filters = {
        "intent": parsed.intent,
        "entities": [{"surface_form": e.surface_form, "entity_type": e.entity_type} for e in parsed.entities],
        "time_before": parsed.time_before.isoformat() if parsed.time_before else None,
        "time_after": parsed.time_after.isoformat() if parsed.time_after else None,
        "types": parsed.types,
        "app_context": parsed.app_context,
    }

    # The retention boundary applies to answering too: never even attempt
    # retrieval for a question that is itself asking for excluded content.
    if parsed.out_of_bounds_category:
        retrieval_ms = int((time.monotonic() - start) * 1000)
        t_gen = time.monotonic()
        async with session_factory() as session:
            answer_result, answer_completion = await run_answer(
                session, provider, question, SufficiencyVerdict.INSUFFICIENT,
                f"out_of_bounds: {parsed.out_of_bounds_category} — Kivi does not track this.",
                [],
            )
        completions.append(answer_completion)
        generation_ms = int((time.monotonic() - t_gen) * 1000)
        route = f"out_of_bounds:{parsed.out_of_bounds_category}"
        trace_id = await _persist_trace(
            session_factory, question=question, route=route, filters=base_filters, candidates=[],
            selected_memory_ids=[], cited_memory_ids=answer_result.cited_memory_ids,
            verdict=SufficiencyVerdict.INSUFFICIENT,
            verdict_reason=f"out_of_bounds: {parsed.out_of_bounds_category}",
            answer_text=answer_result.text, completions=completions,
            retrieval_latency_ms=retrieval_ms, generation_latency_ms=generation_ms,
        )
        return AskResult(
            question=question, route=route, parsed=parsed,
            sufficiency_verdict=SufficiencyVerdict.INSUFFICIENT.value,
            sufficiency_reason=f"out_of_bounds: {parsed.out_of_bounds_category}",
            answer=answer_result.text, cited_memory_ids=answer_result.cited_memory_ids,
            tokens_in=sum(c.tokens_in for c in completions), tokens_out=sum(c.tokens_out for c in completions),
            cost_usd=sum(c.cost_usd for c in completions),
            retrieval_latency_ms=retrieval_ms, generation_latency_ms=generation_ms,
            latency_ms=retrieval_ms + generation_ms, trace_id=trace_id,
        )

    if parsed.intent == "find":
        answer_text, verdict, reason, extra = await _handle_find(session_factory, parsed, question)
        retrieval_ms = int((time.monotonic() - start) * 1000)
        trace_id = await _persist_trace(
            session_factory, question=question, route="find", filters={**base_filters, **extra},
            candidates=[], selected_memory_ids=[], cited_memory_ids=[],
            verdict=verdict, verdict_reason=reason, answer_text=answer_text, completions=completions,
            retrieval_latency_ms=retrieval_ms, generation_latency_ms=0,
        )
        return AskResult(
            question=question, route="find", parsed=parsed,
            sufficiency_verdict=verdict.value, sufficiency_reason=reason, answer=answer_text,
            tokens_in=sum(c.tokens_in for c in completions), tokens_out=sum(c.tokens_out for c in completions),
            cost_usd=sum(c.cost_usd for c in completions),
            retrieval_latency_ms=retrieval_ms, generation_latency_ms=0,
            latency_ms=retrieval_ms, trace_id=trace_id,
        )

    if parsed.intent == "chitchat":
        answer_text = "Hi! Ask me about a project's state, who owns what, a decision, a commitment, or a preference I've heard you dictate."
        retrieval_ms = int((time.monotonic() - start) * 1000)
        trace_id = await _persist_trace(
            session_factory, question=question, route="chitchat", filters=base_filters,
            candidates=[], selected_memory_ids=[], cited_memory_ids=[],
            verdict=SufficiencyVerdict.SUFFICIENT, verdict_reason="chitchat — no retrieval needed",
            answer_text=answer_text, completions=completions,
            retrieval_latency_ms=retrieval_ms, generation_latency_ms=0,
        )
        return AskResult(
            question=question, route="chitchat", parsed=parsed,
            sufficiency_verdict=SufficiencyVerdict.SUFFICIENT.value,
            sufficiency_reason="chitchat — no retrieval needed", answer=answer_text,
            tokens_in=sum(c.tokens_in for c in completions), tokens_out=sum(c.tokens_out for c in completions),
            cost_usd=sum(c.cost_usd for c in completions),
            retrieval_latency_ms=retrieval_ms, generation_latency_ms=0,
            latency_ms=retrieval_ms, trace_id=trace_id,
        )

    # "recall" and "act" (no dedicated tool framework exists yet — act falls
    # back to the same retrieval+answer path) both run the full read pipeline.
    async with session_factory() as session:
        resolved_entity_ids: list[int] = []
        unresolved: list[str] = []
        for mention in parsed.entities:
            resolved, resolve_completions = await resolve_entity(
                session, provider, mention.surface_form, EntityType(mention.entity_type),
                context=question, create_if_missing=False,
            )
            completions.extend(resolve_completions)
            if resolved is not None:
                resolved_entity_ids.append(resolved.entity_id)
            else:
                unresolved.append(mention.surface_form)
        # Persist any confidence graduation resolve_entity applied above — an
        # unambiguous re-match is true regardless of whether it happened on the
        # write path or here, and fusion's boost reads the stored value.
        await session.commit()

        embedding = (await embed_async([question]))[0]
        limit = settings.candidate_limit_per_generator
        entity_cands = await entity_candidates(session, resolved_entity_ids, limit)
        lexical_cands = await lexical_candidates(session, question, limit)
        semantic_cands = await semantic_candidates(session, embedding, limit)

        selected, candidates_trace = await fuse(
            session, entity_cands, lexical_cands, semantic_cands,
            k=settings.rrf_k, top_n=settings.fused_top_n,
            time_before=parsed.time_before, time_after=parsed.time_after, types=parsed.types,
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

    filters = {**base_filters, "resolved_entity_ids": resolved_entity_ids, "unresolved_entities": unresolved}
    trace_id = await _persist_trace(
        session_factory, question=question, route=parsed.intent, filters=filters,
        candidates=candidates_trace, selected_memory_ids=[fc.memory_id for fc in selected],
        cited_memory_ids=answer_result.cited_memory_ids, verdict=suff_result.verdict,
        verdict_reason=suff_result.reason, answer_text=answer_result.text, completions=completions,
        retrieval_latency_ms=retrieval_ms, generation_latency_ms=generation_ms,
    )

    return AskResult(
        question=question, route=parsed.intent, parsed=parsed,
        resolved_entity_ids=resolved_entity_ids, unresolved_entities=unresolved,
        candidates_trace=candidates_trace, selected_memory_ids=[fc.memory_id for fc in selected],
        sufficiency_verdict=suff_result.verdict.value, sufficiency_reason=suff_result.reason,
        answer=answer_result.text, cited_memory_ids=answer_result.cited_memory_ids,
        tokens_in=sum(c.tokens_in for c in completions), tokens_out=sum(c.tokens_out for c in completions),
        cost_usd=sum(c.cost_usd for c in completions),
        retrieval_latency_ms=retrieval_ms, generation_latency_ms=generation_ms,
        latency_ms=retrieval_ms + generation_ms, trace_id=trace_id,
    )
