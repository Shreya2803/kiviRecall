import argparse
import asyncio
import time
from pathlib import Path

from kivi.config import settings
from kivi.db.session import async_session_factory
from kivi.ingest.jsonl_reader import ingest_records, read_jsonl
from kivi.memory.pipeline import process_dictation
from kivi.models.provider import GeminiProvider, ModelProvider, ProviderError, SarvamProvider, get_provider
from kivi.retrieval.ask import answer_question


async def _check_one(label: str, provider: ModelProvider) -> None:
    try:
        completion = await provider.complete(
            [{"role": "user", "content": "Say OK"}],
            tier="extraction",
        )
        print(
            f"[{label}] OK  model={completion.model} "
            f"tokens_in={completion.tokens_in} tokens_out={completion.tokens_out} "
            f"latency_ms={completion.latency_ms} cost_usd=${completion.cost_usd:.6f}"
        )
        print(f"    content: {completion.content!r}")
    except Exception as exc:  # noqa: BLE001 — this is a diagnostic command, report and continue
        print(f"[{label}] FAILED  {exc}")


async def check_models() -> None:
    if settings.sarvam_api_key:
        await _check_one("sarvam", SarvamProvider(settings.sarvam_api_key, settings.sarvam_base_url))
    else:
        print("[sarvam] SKIPPED  SARVAM_API_KEY not set")

    if settings.gemini_api_key:
        await _check_one("gemini", GeminiProvider(settings.gemini_api_key, settings.gemini_base_url))
    else:
        print("[gemini] SKIPPED  GEMINI_API_KEY not set")

    print()
    try:
        active = get_provider()
        print(f"Active provider (fallback selection): {active.name}")
    except ProviderError as exc:
        print(f"No provider available: {exc}")


async def import_corpus(path: Path, limit: int | None, do_extract: bool) -> None:
    records = read_jsonl(path)
    if limit is not None:
        records = records[:limit]
    print(f"Read {len(records)} record(s) from {path}")

    async with async_session_factory() as session:
        inserted, skipped = await ingest_records(session, records)
    print(f"Ingested {len(inserted)} new dictation(s), skipped {skipped} already-imported")

    if not do_extract or not inserted:
        return

    provider = get_provider()
    print(f"Running extraction pipeline over {len(inserted)} dictation(s) via {provider.name}...")

    start = time.monotonic()
    processed = 0
    abstained = 0
    errored = 0
    total_memories = 0
    total_new_entities = 0
    entities_resolved: set[int] = set()
    policy_rejections: dict[str, int] = {}
    total_cost = 0.0

    for dictation in inserted:
        result = await process_dictation(async_session_factory, provider, dictation)
        processed += 1
        if result.outcome == "no_memories_found":
            abstained += 1
        elif result.outcome == "error":
            errored += 1
            print(f"  [{result.dictation_id}] ERROR: {result.reason}")
        total_memories += result.memories_created
        total_new_entities += result.new_entities
        total_cost += result.cost_usd
        for category, count in result.policy_rejections.items():
            policy_rejections[category] = policy_rejections.get(category, 0) + count

        if processed % 25 == 0:
            print(f"  ...{processed}/{len(inserted)} processed")

    elapsed = time.monotonic() - start
    print()
    print("=== Extraction run summary ===")
    print(f"records processed:     {processed}")
    print(f"abstained (no memory): {abstained}")
    print(f"errored:               {errored}")
    print(f"rejected by policy:    {sum(policy_rejections.values())} ({policy_rejections})")
    print(f"memories created:      {total_memories}")
    print(f"new entities created:  {total_new_entities}")
    print(f"total cost:            ${total_cost:.4f}")
    print(f"elapsed time:          {elapsed:.1f}s")


async def ask(question: str) -> None:
    provider = get_provider()
    result = await answer_question(async_session_factory, provider, question)

    print(f"QUESTION: {result.question}")
    print(f"route:              {result.route}")
    print(f"parsed intent:      {result.parsed.intent}")
    print(f"parsed entities:    {[(e.surface_form, e.entity_type) for e in result.parsed.entities]}")
    print(f"time_before/after:  {result.parsed.time_before} / {result.parsed.time_after}")
    print(f"types filter:       {result.parsed.types}")
    print(f"app_context filter: {result.parsed.app_context}")
    print(f"out_of_bounds:      {result.parsed.out_of_bounds_category}")
    print(f"resolved entities:  {result.resolved_entity_ids}")
    print(f"unresolved entities:{result.unresolved_entities}")
    print()
    if result.candidates_trace:
        print(f"candidates ({len(result.candidates_trace)} total, across entity/lexical/semantic):")
        for c in sorted(result.candidates_trace, key=lambda c: -c["boosted_score"]):
            flag = "SELECTED" if c["selected"] else ("filtered" if not c["passed_hard_filters"] else "")
            print(
                f"  memory={c['memory_id']:<5} components={c['component_scores']} "
                f"rrf={c['rrf_score']:.4f} conf={c['confidence']:.2f} "
                f"boosted={c['boosted_score']:.4f} pinned={c['pinned']} {flag}"
            )
        print()
    print(f"selected memory ids: {result.selected_memory_ids}")
    print(f"sufficiency verdict: {result.sufficiency_verdict}  ({result.sufficiency_reason})")
    print()
    print(f"ANSWER: {result.answer}")
    print(f"cited memory ids:    {result.cited_memory_ids}")
    print()
    print(
        f"tokens_in={result.tokens_in} tokens_out={result.tokens_out} "
        f"cost_usd=${result.cost_usd:.6f}"
    )
    print(
        f"retrieval_latency_ms={result.retrieval_latency_ms} "
        f"generation_latency_ms={result.generation_latency_ms} "
        f"total_latency_ms={result.latency_ms}"
    )
    print(f"query_trace id:      {result.trace_id}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="kivi")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("check-models", help="Call each configured provider once and report cost")

    import_parser = subparsers.add_parser("import", help="Ingest a corpus.jsonl and run extraction over it")
    import_parser.add_argument("path", type=Path, help="Path to corpus.jsonl")
    import_parser.add_argument("--limit", type=int, default=None, help="Only process the first N records")
    import_parser.add_argument(
        "--no-extract", action="store_true", help="Ingest dictations only, skip the extraction pipeline"
    )

    ask_parser = subparsers.add_parser("ask", help="Ask Hey Kivi a question and print the full query trace")
    ask_parser.add_argument("question", type=str, help="The question to ask")

    args = parser.parse_args()

    if args.command == "check-models":
        asyncio.run(check_models())
    elif args.command == "import":
        asyncio.run(import_corpus(args.path, args.limit, not args.no_extract))
    elif args.command == "ask":
        asyncio.run(ask(args.question))


if __name__ == "__main__":
    main()
