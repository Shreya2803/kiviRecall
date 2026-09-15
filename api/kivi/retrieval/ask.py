import datetime
import time
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import async_sessionmaker

from kivi.config import settings
from kivi.db.enums import SufficiencyVerdict
from kivi.db.models import QueryTrace
from kivi.models.provider import Completion, ModelProvider
from kivi.retrieval import answer as answer_module
from kivi.retrieval.answer import run_answer
from kivi.retrieval.parse import ParsedQuery, run_parse
from kivi.tools.find_dictation import find_dictation
from kivi.tools.recall_from_memory import recall_from_memory


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


async def answer_question(
    session_factory: async_sessionmaker,
    provider: ModelProvider,
    question: str,
    now: datetime.datetime | None = None,
) -> AskResult:
    """The router: parses intent, then dispatches to whichever tool that
    intent calls for. recall_from_memory and find_dictation live in
    kivi.tools — the same two of Hey Kivi's four tools a future agent loop
    would call directly; this function is what decides which one to use for
    one plain-language question."""
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
        async with session_factory() as session:
            find_result = await find_dictation(
                session, app_context=parsed.app_context,
                time_before=parsed.time_before, time_after=parsed.time_after,
            )
        retrieval_ms = int((time.monotonic() - start) * 1000)
        filters = {**base_filters, "found_dictation_ids": find_result.found_dictation_ids}
        trace_id = await _persist_trace(
            session_factory, question=question, route="find", filters=filters,
            candidates=[], selected_memory_ids=[], cited_memory_ids=[],
            verdict=SufficiencyVerdict(find_result.verdict), verdict_reason=find_result.reason,
            answer_text=find_result.answer, completions=completions,
            retrieval_latency_ms=retrieval_ms, generation_latency_ms=0,
        )
        return AskResult(
            question=question, route="find", parsed=parsed,
            sufficiency_verdict=find_result.verdict, sufficiency_reason=find_result.reason,
            answer=find_result.answer,
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
    # back to the same retrieval+answer path) both run recall_from_memory.
    async with session_factory() as session:
        recall_result = await recall_from_memory(
            session, provider, question, parsed.entities,
            time_before=parsed.time_before, time_after=parsed.time_after, types=parsed.types,
        )
    completions.extend(recall_result.completions)

    filters = {
        **base_filters,
        "resolved_entity_ids": recall_result.resolved_entity_ids,
        "unresolved_entities": recall_result.unresolved_entities,
    }
    trace_id = await _persist_trace(
        session_factory, question=question, route=parsed.intent, filters=filters,
        candidates=recall_result.candidates_trace, selected_memory_ids=recall_result.selected_memory_ids,
        cited_memory_ids=recall_result.cited_memory_ids, verdict=SufficiencyVerdict(recall_result.sufficiency_verdict),
        verdict_reason=recall_result.sufficiency_reason, answer_text=recall_result.answer, completions=completions,
        retrieval_latency_ms=recall_result.retrieval_latency_ms, generation_latency_ms=recall_result.generation_latency_ms,
    )

    return AskResult(
        question=question, route=parsed.intent, parsed=parsed,
        resolved_entity_ids=recall_result.resolved_entity_ids, unresolved_entities=recall_result.unresolved_entities,
        candidates_trace=recall_result.candidates_trace, selected_memory_ids=recall_result.selected_memory_ids,
        sufficiency_verdict=recall_result.sufficiency_verdict, sufficiency_reason=recall_result.sufficiency_reason,
        answer=recall_result.answer, cited_memory_ids=recall_result.cited_memory_ids,
        tokens_in=sum(c.tokens_in for c in completions), tokens_out=sum(c.tokens_out for c in completions),
        cost_usd=sum(c.cost_usd for c in completions),
        retrieval_latency_ms=recall_result.retrieval_latency_ms, generation_latency_ms=recall_result.generation_latency_ms,
        latency_ms=recall_result.retrieval_latency_ms + recall_result.generation_latency_ms, trace_id=trace_id,
    )
