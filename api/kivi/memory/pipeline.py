import hashlib
import time
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import async_sessionmaker

from kivi.config import settings
from kivi.db.enums import EntityType, ExtractionOutcome
from kivi.db.models import Dictation, ExtractionRun
from kivi.memory import extract as extract_module
from kivi.memory.consolidate import consolidate_claim
from kivi.memory.extract import run_extraction
from kivi.memory.gate import run_gate
from kivi.memory.policy import apply_policy
from kivi.memory.resolve import resolve_entity
from kivi.models.embeddings import embed_async
from kivi.models.provider import Completion, ModelProvider


def compute_claim_hash(claim_text: str) -> str:
    normalized = " ".join(claim_text.strip().lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass
class PipelineResult:
    dictation_id: str
    outcome: str
    reason: str
    memories_created: int = 0
    entities_resolved: int = 0
    new_entities: int = 0
    policy_rejections: dict[str, int] = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0


async def process_dictation(
    session_factory: async_sessionmaker, provider: ModelProvider, dictation: Dictation
) -> PipelineResult:
    start = time.monotonic()

    # Phase 1: open the audit row in its own transaction so it survives even if
    # everything after this fails outright.
    async with session_factory() as session:
        run = ExtractionRun(
            dictation_id=dictation.id,
            outcome=ExtractionOutcome.NO_MEMORIES_FOUND,
            reason="processing",
            model=settings.extraction_model if settings.sarvam_api_key else settings.gemini_extraction_model,
            prompt_version=f"{extract_module.PROMPT_VERSION}",
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    completions: list[Completion] = []
    policy_rejections: dict[str, int] = {}
    memories_created = 0
    entities_touched: set[int] = set()
    new_entities = 0
    action_counts: dict[str, int] = {}
    final_outcome = ExtractionOutcome.NO_MEMORIES_FOUND
    final_reason = "no durable content"

    # Phase 2: the actual work, in its own transaction — an exception here
    # rolls back only this transaction, never the audit row from phase 1.
    try:
        async with session_factory() as session:
            gate_decision, gate_completion = await run_gate(
                provider, dictation.formatted_output, dictation.app_context
            )
            completions.append(gate_completion)

            if not gate_decision.proceed:
                final_reason = f"{gate_decision.reason}: {gate_decision.detail}"
            else:
                extraction, extract_completion = await run_extraction(provider, dictation.formatted_output)
                completions.append(extract_completion)

                if not extraction.claims:
                    final_reason = extraction.reason
                else:
                    for claim in extraction.claims:
                        verdict = apply_policy(claim)
                        if not verdict.keep:
                            policy_rejections[verdict.rejected_category] = (
                                policy_rejections.get(verdict.rejected_category, 0) + 1
                            )
                            continue

                        entity_ids: list[int] = []
                        for mention in claim.entities:
                            resolved, resolve_completions = await resolve_entity(
                                session, provider, mention.surface_form,
                                EntityType(mention.entity_type), context=claim.text,
                                create_if_missing=True,
                            )
                            completions.extend(resolve_completions)
                            if resolved is not None:
                                entity_ids.append(resolved.entity_id)
                                entities_touched.add(resolved.entity_id)
                                if resolved.is_new:
                                    new_entities += 1

                        claim_hash = compute_claim_hash(claim.text)
                        embedding = (await embed_async([claim.text]))[0]

                        outcome, consolidate_completions = await consolidate_claim(
                            session, provider, claim, claim_hash, embedding, entity_ids,
                            dictation.id, dictation.spoken_at, run_id,
                        )
                        completions.extend(consolidate_completions)
                        action_counts[outcome.action] = action_counts.get(outcome.action, 0) + 1
                        if outcome.action in ("unrelated", "refinement", "contradiction"):
                            memories_created += 1

                    if memories_created or action_counts.get("duplicate", 0):
                        final_outcome = ExtractionOutcome.MEMORIES_EXTRACTED
                        final_reason = (
                            f"extracted {len(extraction.claims)} claim(s): "
                            + ", ".join(f"{k}={v}" for k, v in action_counts.items())
                        )
                    else:
                        final_reason = "all claims rejected by policy or already tombstoned"

            await session.commit()
    except Exception as exc:  # noqa: BLE001 — must still write the audit row below
        final_outcome = ExtractionOutcome.ERROR
        final_reason = f"{type(exc).__name__}: {exc}"

    latency_ms = int((time.monotonic() - start) * 1000)

    # Phase 3: close out the audit row with the real numbers, in its own transaction.
    async with session_factory() as session:
        run = await session.get(ExtractionRun, run_id)
        run.outcome = final_outcome
        run.reason = final_reason
        run.memories_created_count = memories_created
        run.policy_rejections = policy_rejections or None
        run.input_tokens = sum(c.tokens_in for c in completions)
        run.output_tokens = sum(c.tokens_out for c in completions)
        run.estimated_cost_usd = sum(c.cost_usd for c in completions)
        run.latency_ms = latency_ms
        await session.commit()

    return PipelineResult(
        dictation_id=dictation.id,
        outcome=final_outcome.value,
        reason=final_reason,
        memories_created=memories_created,
        entities_resolved=len(entities_touched),
        new_entities=new_entities,
        policy_rejections=policy_rejections,
        tokens_in=sum(c.tokens_in for c in completions),
        tokens_out=sum(c.tokens_out for c in completions),
        cost_usd=sum(c.cost_usd for c in completions),
        latency_ms=latency_ms,
    )
