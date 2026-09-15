import datetime
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from starlette.responses import StreamingResponse

from kivi.db.enums import MemoryStatus
from kivi.db.models import (
    Dictation,
    Entity,
    ExtractionRun,
    Memory,
    MemoryEntity,
    MemorySource,
)
from kivi.db.session import async_session_factory
from kivi.models.provider import get_provider
from kivi.retrieval.ask import answer_question
from kivi.tools.draft_with_context import draft_with_context
from kivi.tools.manage_memory import ClaimTombstonedError, MemoryNotFoundError, confirm_memory, correct_memory, forget_memory

# No "/api" prefix here: the Vite dev server proxies "/api/*" to this backend
# with that prefix already stripped (see web/vite.config.ts), matching /health.
router = APIRouter()


# ---------------------------------------------------------------------------
# Dictation feed
# ---------------------------------------------------------------------------

@router.get("/dictations")
async def list_dictations(limit: int = 50, offset: int = 0) -> list[dict]:
    """Chronological feed. Each row carries the badge the product position
    makes visible: how many memories this dictation produced, and — for the
    ~70% that produce nothing — the plain-language reason why."""
    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(Dictation, ExtractionRun)
                .outerjoin(ExtractionRun, ExtractionRun.dictation_id == Dictation.id)
                .order_by(Dictation.spoken_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()

    return [
        {
            "id": d.id,
            "spoken_at": d.spoken_at.isoformat(),
            "formatted_output": d.formatted_output,
            "app_context": d.app_context,
            "window_title": d.window_title,
            "language": d.language,
            "memories_created": run.memories_created_count if run else 0,
            "outcome": run.outcome.value if run else None,
            "reason": run.reason if run else None,
        }
        for d, run in rows
    ]


# ---------------------------------------------------------------------------
# What Kivi knows
# ---------------------------------------------------------------------------

async def _supersession_history(session, memory_id: int) -> list[dict]:
    history: list[dict] = []
    current_id = memory_id
    for _ in range(20):  # hard cap — a chain this long would be a data bug, not a real case
        predecessor = await session.scalar(
            select(Memory).where(Memory.superseded_by_id == current_id)
        )
        if predecessor is None:
            break
        history.append({
            "id": predecessor.id,
            "claim": predecessor.claim,
            "valid_from": predecessor.valid_from.isoformat(),
            "valid_to": predecessor.valid_to.isoformat() if predecessor.valid_to else None,
        })
        current_id = predecessor.id
    return history


def _memory_dict(memory: Memory, supporting_count: int, history: list[dict]) -> dict:
    return {
        "id": memory.id,
        "memory_type": memory.memory_type.value,
        "claim": memory.claim,
        "confidence": memory.confidence,
        "origin": memory.origin.value,
        "pinned": memory.pinned,
        "occurrence_count": memory.occurrence_count,
        "first_learned": memory.valid_from.isoformat(),
        "last_confirmed_at": memory.last_confirmed_at.isoformat() if memory.last_confirmed_at else None,
        "supporting_dictation_count": supporting_count,
        "history": history,
    }


@router.get("/memories")
async def list_memories() -> dict:
    """Memory review grouped by entity — plain-language claims, never raw
    JSON dumps of the underlying schema. Superseded memories are omitted at
    the top level; they appear nested under whatever replaced them."""
    async with async_session_factory() as session:
        entities = (await session.execute(select(Entity).order_by(Entity.canonical_name))).scalars().all()

        active_memories = (
            await session.execute(select(Memory).where(Memory.status == MemoryStatus.ACTIVE))
        ).scalars().all()

        support_counts = dict(
            (await session.execute(
                select(MemorySource.memory_id, func.count()).group_by(MemorySource.memory_id)
            )).all()
        )

        memory_entity_rows = (await session.execute(select(MemoryEntity))).scalars().all()
        entity_to_memory_ids: dict[int, list[int]] = {}
        linked_memory_ids: set[int] = set()
        for row in memory_entity_rows:
            entity_to_memory_ids.setdefault(row.entity_id, []).append(row.memory_id)
            linked_memory_ids.add(row.memory_id)

        memory_by_id = {m.id: m for m in active_memories}

        groups = []
        for entity in entities:
            memory_ids = [mid for mid in entity_to_memory_ids.get(entity.id, []) if mid in memory_by_id]
            if not memory_ids:
                continue
            memories = []
            for mid in memory_ids:
                memory = memory_by_id[mid]
                history = await _supersession_history(session, mid)
                memories.append(_memory_dict(memory, support_counts.get(mid, 0), history))
            groups.append({
                "entity_id": entity.id,
                "canonical_name": entity.canonical_name,
                "entity_type": entity.entity_type.value,
                "needs_review": entity.needs_review,
                "memories": memories,
            })

        unlinked_ids = [m.id for m in active_memories if m.id not in linked_memory_ids]
        general_memories = []
        for mid in unlinked_ids:
            memory = memory_by_id[mid]
            history = await _supersession_history(session, mid)
            general_memories.append(_memory_dict(memory, support_counts.get(mid, 0), history))

    return {"groups": groups, "general": general_memories}


class ActionBody(BaseModel):
    reason: str | None = None


class CorrectBody(BaseModel):
    claim: str
    reason: str | None = None


@router.post("/memories/{memory_id}/confirm")
async def confirm_memory_route(memory_id: int, body: ActionBody) -> dict:
    async with async_session_factory() as session:
        try:
            await confirm_memory(session, memory_id, body.reason)
        except MemoryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/memories/{memory_id}/forget")
async def forget_memory_route(memory_id: int, body: ActionBody) -> dict:
    async with async_session_factory() as session:
        try:
            await forget_memory(session, memory_id, body.reason)
        except MemoryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/memories/{memory_id}/correct")
async def correct_memory_route(memory_id: int, body: CorrectBody) -> dict:
    async with async_session_factory() as session:
        try:
            new_id = await correct_memory(session, memory_id, body.claim, body.reason)
        except MemoryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ClaimTombstonedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "new_memory_id": new_id}


# ---------------------------------------------------------------------------
# Hey Kivi
# ---------------------------------------------------------------------------

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _ask_stream(question: str):
    """Stages are genuinely sequential pipeline steps, not a simulated delay —
    but the final answer text itself is not token-streamed from the provider
    (that would need a materially different streaming call per provider); it
    is computed, then revealed progressively for a readable typing effect."""
    provider = get_provider()
    now = datetime.datetime.now(datetime.timezone.utc)

    yield _sse("stage", {"stage": "parsing"})
    result = await answer_question(async_session_factory, provider, question, now)

    yield _sse("stage", {
        "stage": "retrieved",
        "route": result.route,
        "resolved_entities": result.resolved_entity_ids,
        "unresolved_entities": result.unresolved_entities,
        "candidate_count": len(result.candidates_trace),
    })
    yield _sse("stage", {
        "stage": "sufficiency",
        "verdict": result.sufficiency_verdict,
        "reason": result.sufficiency_reason,
    })

    words = result.answer.split(" ")
    chunk_size = 4
    revealed = ""
    for i in range(0, len(words), chunk_size):
        revealed = (revealed + " " + " ".join(words[i:i + chunk_size])).strip()
        yield _sse("answer_chunk", {"text": revealed})

    async with async_session_factory() as session:
        cited_memories = []
        if result.cited_memory_ids:
            rows = (
                await session.execute(select(Memory).where(Memory.id.in_(result.cited_memory_ids)))
            ).scalars().all()
            for m in rows:
                dictation_ids = (
                    await session.execute(
                        select(MemorySource.dictation_id).where(MemorySource.memory_id == m.id)
                    )
                ).scalars().all()
                cited_memories.append({
                    "id": m.id, "claim": m.claim, "confidence": m.confidence,
                    "memory_type": m.memory_type.value, "source_dictation_ids": list(dictation_ids),
                })

    yield _sse("done", {
        "answer": result.answer,
        "route": result.route,
        "sufficiency_verdict": result.sufficiency_verdict,
        "sufficiency_reason": result.sufficiency_reason,
        "cited_memories": cited_memories,
        "trace_id": result.trace_id,
        "cost_usd": result.cost_usd,
        "latency_ms": result.latency_ms,
    })


class AskBody(BaseModel):
    question: str


@router.post("/ask")
async def ask_route(body: AskBody) -> StreamingResponse:
    return StreamingResponse(_ask_stream(body.question), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Draft with context
# ---------------------------------------------------------------------------

class DraftBody(BaseModel):
    request: str


@router.post("/draft")
async def draft_route(body: DraftBody) -> dict:
    provider = get_provider()
    now = datetime.datetime.now(datetime.timezone.utc)
    async with async_session_factory() as session:
        result = await draft_with_context(session, provider, body.request, now)
        cited = []
        if result.cited_memory_ids:
            rows = (
                await session.execute(select(Memory).where(Memory.id.in_(result.cited_memory_ids)))
            ).scalars().all()
            cited = [{"id": m.id, "claim": m.claim} for m in rows]

    return {
        "draft": result.draft,
        "cited_memories": cited,
        "cost_usd": sum(c.cost_usd for c in result.completions),
    }
