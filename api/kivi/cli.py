import argparse
import asyncio
import subprocess
import sys
import time
from pathlib import Path

from sqlalchemy import func, select, text

from kivi.config import settings
from kivi.db.enums import ExtractionOutcome, MemoryStatus
from kivi.db.models import Dictation, Entity, ExtractionRun, Memory, MemoryEntity, MemorySource, QueryTrace
from kivi.db.session import async_session_factory
from kivi.ingest.common import ingest_records, validate_records
from kivi.ingest.csv_reader import read_csv
from kivi.ingest.jsonl_reader import read_jsonl
from kivi.memory.pipeline import process_dictation
from kivi.models.provider import GeminiProvider, ModelProvider, ProviderError, SarvamProvider, get_provider
from kivi.retrieval.ask import answer_question

# Every application table, in an order TRUNCATE ... CASCADE can safely ignore
# (CASCADE handles the FK ordering) — deliberately not alembic_version, so a
# reset never touches schema/migration state, only rows.
_APP_TABLES = [
    "dictation", "entity", "entity_alias", "extraction_run", "memory",
    "memory_source", "memory_entity", "query_trace", "user_action",
]


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


async def reset_database() -> None:
    """Wipes every application row while leaving the schema (and Alembic's
    version table) untouched — used to get a clean, reproducible base for a
    full evaluation run without dropping/recreating the database itself."""
    async with async_session_factory() as session:
        await session.execute(text(f"TRUNCATE {', '.join(_APP_TABLES)} RESTART IDENTITY CASCADE"))
        await session.commit()
    print(f"Truncated {len(_APP_TABLES)} table(s): {', '.join(_APP_TABLES)}")


def _load_records(path: Path, fmt: str | None, column_map: dict[str, str]) -> list[dict]:
    resolved_fmt = fmt or ("csv" if path.suffix.lower() == ".csv" else "jsonl")
    if resolved_fmt == "csv":
        records, resolved_mapping = read_csv(path, column_map or None)
        print(f"CSV column mapping used: {resolved_mapping}")
        return records
    return read_jsonl(path)


async def import_corpus(
    path: Path, limit: int | None, do_extract: bool, fmt: str | None, column_map: dict[str, str]
) -> None:
    records = _load_records(path, fmt, column_map)
    if limit is not None:
        records = records[:limit]
    print(f"Read {len(records)} record(s) from {path}")

    # Validate the whole batch before anything touches the database. Missing
    # optional fields are a warning, never a failure; missing required fields
    # drop just that record, reported by id, and the rest of the batch still
    # proceeds — one bad row must never sink an entire corpus.
    validation = validate_records(records)
    for record_id, reason in validation.rejected:
        print(f"  REJECTED {record_id}: {reason}")
    for record_id, warnings in validation.warnings_by_id.items():
        for warning in warnings:
            print(f"  WARNING {record_id}: {warning}")
    print(
        f"Validated: {len(validation.valid)} usable, {len(validation.rejected)} rejected, "
        f"{len(validation.warnings_by_id)} with warnings"
    )

    async with async_session_factory() as session:
        inserted, skipped = await ingest_records(session, validation.valid)
    print(f"Ingested {len(inserted)} new dictation(s), skipped {skipped} already-imported")

    if not do_extract or not inserted:
        return

    provider = get_provider()
    print(f"Running extraction pipeline over {len(inserted)} dictation(s) via {provider.name}...")

    start = time.monotonic()
    processed = 0
    remembered = 0
    nothing_kept = 0
    errored = 0
    total_memories = 0
    total_new_entities = 0
    policy_rejections: dict[str, int] = {}
    total_cost = 0.0

    for dictation in inserted:
        result = await process_dictation(async_session_factory, provider, dictation)
        processed += 1
        if result.outcome == "memories_extracted":
            remembered += 1
        elif result.outcome == "no_memories_found":
            nothing_kept += 1
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
    total_rejected_claims = sum(policy_rejections.values())
    print()
    print("=== Import summary ===")
    print(f"records:      {processed}")
    print(f"remembered:   {remembered}  (produced at least one memory)")
    print(f"nothing-kept: {nothing_kept}  (no durable content found)")
    print(f"rejected:     {total_rejected_claims} claim(s) by policy {policy_rejections}")
    print(f"entities:     {total_new_entities} new")
    print(f"errored:      {errored}  (transient failures — see extraction_run for reasons)")
    print(f"elapsed:      {elapsed:.1f}s")
    print(f"cost:         ${total_cost:.4f}")
    print(f"memories:     {total_memories} created")


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


def _find_eval_script() -> Path:
    here = Path(__file__).resolve()
    # Container layout: docker-compose mounts ./eval at /app/eval, and this
    # file lives at /app/kivi/cli.py — /app is here.parents[1].
    # Host checkout layout: this file is at <repo>/api/kivi/cli.py and eval/
    # is a sibling of api/ — <repo> is here.parents[2].
    for candidate in (here.parents[1] / "eval" / "run_eval.py", here.parents[2] / "eval" / "run_eval.py"):
        if candidate.exists():
            return candidate
    raise FileNotFoundError("could not locate eval/run_eval.py from either container or host layout")


def run_evaluate(extra_args: list[str]) -> None:
    """Thin wrapper: `kivi evaluate` and `python eval/run_eval.py` are the same
    program. This just saves typing the direct path and staying in one CLI."""
    script = _find_eval_script()
    result = subprocess.run([sys.executable, str(script), *extra_args])
    sys.exit(result.returncode)


async def inspect_database(dictation_id: str | None, memory_id: int | None, trace_id: int | None) -> None:
    async with async_session_factory() as session:
        if dictation_id:
            await _inspect_dictation(session, dictation_id)
        elif memory_id:
            await _inspect_memory(session, memory_id)
        elif trace_id:
            await _inspect_trace(session, trace_id)
        else:
            await _inspect_overview(session)


async def _inspect_overview(session) -> None:
    n_dictations = await session.scalar(select(func.count()).select_from(Dictation))
    n_entities = await session.scalar(select(func.count()).select_from(Entity))
    n_traces = await session.scalar(select(func.count()).select_from(QueryTrace))

    outcome_rows = (
        await session.execute(select(ExtractionRun.outcome, func.count()).group_by(ExtractionRun.outcome))
    ).all()
    status_rows = (
        await session.execute(select(Memory.status, func.count()).group_by(Memory.status))
    ).all()
    type_rows = (
        await session.execute(
            select(Memory.memory_type, func.count())
            .where(Memory.status == MemoryStatus.ACTIVE)
            .group_by(Memory.memory_type)
        )
    ).all()
    cost_row = await session.execute(
        select(func.coalesce(func.sum(ExtractionRun.estimated_cost_usd), 0))
    )
    total_cost = cost_row.scalar_one()

    print("=== Dictations ===")
    print(f"total: {n_dictations}")
    print()
    print("=== Extraction runs ===")
    print(f"total: {sum(c for _, c in outcome_rows)}  " + ", ".join(f"{o.value}={c}" for o, c in outcome_rows))
    print(f"total extraction cost so far: ${float(total_cost):.4f}")
    print()
    print("=== Memories ===")
    print("by status: " + ", ".join(f"{s.value}={c}" for s, c in status_rows))
    print("active by type: " + ", ".join(f"{t.value}={c}" for t, c in type_rows))
    print()
    print("=== Entities ===")
    print(f"total: {n_entities}")
    print()
    print("=== Query traces ===")
    print(f"total: {n_traces}")
    if n_traces:
        recent = (
            await session.execute(select(QueryTrace).order_by(QueryTrace.id.desc()).limit(5))
        ).scalars().all()
        for t in recent:
            print(f"  #{t.id} [{t.route}] {t.question!r} -> verdict={t.sufficiency_verdict.value}")
    print()
    print("Drill down with: kivi inspect --dictation <id> | --memory <id> | --trace <id>")


async def _inspect_dictation(session, dictation_id: str) -> None:
    dictation = await session.get(Dictation, dictation_id)
    if dictation is None:
        print(f"No dictation with id {dictation_id!r}")
        return
    print(f"=== Dictation {dictation.id} ===")
    print(f"spoken_at:        {dictation.spoken_at}")
    print(f"app_context:      {dictation.app_context}")
    print(f"formatted_output: {dictation.formatted_output}")
    print(f"raw_asr:          {dictation.raw_asr}")

    runs = (
        await session.execute(select(ExtractionRun).where(ExtractionRun.dictation_id == dictation_id))
    ).scalars().all()
    print(f"\nextraction_run(s): {len(runs)}")
    for r in runs:
        print(f"  #{r.id} outcome={r.outcome.value} memories={r.memories_created_count} reason={r.reason!r}")

    memory_ids = (
        await session.execute(select(MemorySource.memory_id).where(MemorySource.dictation_id == dictation_id))
    ).scalars().all()
    print(f"\nmemories sourced from this dictation: {len(memory_ids)}")
    if memory_ids:
        memories = (await session.execute(select(Memory).where(Memory.id.in_(memory_ids)))).scalars().all()
        for m in memories:
            print(f"  #{m.id} [{m.status.value}] {m.claim}")


async def _inspect_memory(session, memory_id: int) -> None:
    memory = await session.get(Memory, memory_id)
    if memory is None:
        print(f"No memory with id {memory_id}")
        return
    print(f"=== Memory #{memory.id} ===")
    print(f"type:        {memory.memory_type.value}")
    print(f"claim:       {memory.claim}")
    print(f"status:      {memory.status.value}")
    print(f"origin:      {memory.origin.value}")
    print(f"confidence:  {memory.confidence}")
    print(f"pinned:      {memory.pinned}")
    print(f"valid_from:  {memory.valid_from}")
    print(f"valid_to:    {memory.valid_to}")
    print(f"superseded_by_id: {memory.superseded_by_id}")

    sources = (
        await session.execute(select(MemorySource.dictation_id).where(MemorySource.memory_id == memory_id))
    ).scalars().all()
    print(f"\nsource dictation(s): {sources}")

    entity_ids = (
        await session.execute(select(MemoryEntity.entity_id).where(MemoryEntity.memory_id == memory_id))
    ).scalars().all()
    if entity_ids:
        entities = (await session.execute(select(Entity).where(Entity.id.in_(entity_ids)))).scalars().all()
        print(f"linked entities: {[(e.id, e.canonical_name, e.entity_type.value) for e in entities]}")
    else:
        print("linked entities: none")


async def _inspect_trace(session, trace_id: int) -> None:
    trace = await session.get(QueryTrace, trace_id)
    if trace is None:
        print(f"No query_trace with id {trace_id}")
        return
    print(f"=== Query trace #{trace.id} ===")
    print(f"question:    {trace.question}")
    print(f"route:       {trace.route}")
    print(f"filters:     {trace.filters}")
    print(f"verdict:     {trace.sufficiency_verdict.value}  ({trace.sufficiency_reason})")
    print(f"selected:    {trace.selected_memory_ids}")
    print(f"cited:       {trace.cited_memory_ids}")
    print(f"answer:      {trace.answer}")
    print(f"model:       {trace.model}  prompt_version={trace.prompt_version}")
    print(
        f"tokens_in={trace.input_tokens} tokens_out={trace.output_tokens} "
        f"cost_usd=${float(trace.estimated_cost_usd or 0):.6f}"
    )
    print(
        f"retrieval_latency_ms={trace.retrieval_latency_ms} "
        f"generation_latency_ms={trace.generation_latency_ms} total_latency_ms={trace.latency_ms}"
    )
    print(f"\ncandidates ({len(trace.candidates)} total):")
    for c in sorted(trace.candidates, key=lambda c: -c["boosted_score"]):
        flag = "SELECTED" if c["selected"] else ("filtered" if not c["passed_hard_filters"] else "")
        print(
            f"  memory={c['memory_id']:<5} components={c['component_scores']} "
            f"rrf={c['rrf_score']:.4f} conf={c['confidence']:.2f} "
            f"boosted={c['boosted_score']:.4f} pinned={c['pinned']} {flag}"
        )


def _parse_column_map(pairs: list[str]) -> dict[str, str]:
    mapping = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise argparse.ArgumentTypeError(f"--map expects field=CsvColumnName, got {pair!r}")
        field, col = pair.split("=", 1)
        mapping[field.strip()] = col.strip()
    return mapping


def main() -> None:
    parser = argparse.ArgumentParser(prog="kivi")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("check-models", help="Call each configured provider once and report cost")
    subparsers.add_parser("reset", help="Truncate all application tables (schema untouched)")

    import_parser = subparsers.add_parser(
        "import", help="Ingest a corpus (JSONL or CSV) and run extraction over it"
    )
    import_parser.add_argument("path", type=Path, help="Path to a .jsonl or .csv corpus file")
    import_parser.add_argument(
        "--format", choices=["jsonl", "csv"], default=None, help="Defaults to the file extension"
    )
    import_parser.add_argument(
        "--map", action="append", metavar="FIELD=CsvColumn",
        help="CSV column mapping, e.g. --map raw_asr=RawText --map spoken_at=Timestamp. "
        "Repeatable. Unmapped fields are guessed from the header (case/spacing-insensitive).",
    )
    import_parser.add_argument("--limit", type=int, default=None, help="Only process the first N records")
    import_parser.add_argument(
        "--no-extract", action="store_true", help="Ingest dictations only, skip the extraction pipeline"
    )

    ask_parser = subparsers.add_parser("ask", help="Ask Hey Kivi a question and print the full query trace")
    ask_parser.add_argument("question", type=str, help="The question to ask")

    subparsers.add_parser(
        "evaluate", help="Run the full eval/run_eval.py pipeline (same program, one entry point). "
        "Accepts eval/run_eval.py's own flags, e.g. `kivi evaluate --no-reset`."
    )

    inspect_parser = subparsers.add_parser("inspect", help="Inspect the current database: memories, traces, dictations")
    inspect_group = inspect_parser.add_mutually_exclusive_group()
    inspect_group.add_argument("--dictation", type=str, default=None, metavar="ID")
    inspect_group.add_argument("--memory", type=int, default=None, metavar="ID")
    inspect_group.add_argument("--trace", type=int, default=None, metavar="ID")

    # `evaluate`'s own flags belong to eval/run_eval.py, not to this parser —
    # parse_known_args so `kivi evaluate --no-reset --limit-questions 5` etc.
    # pass straight through instead of tripping "unrecognized arguments".
    args, extra_args = parser.parse_known_args()
    if extra_args and args.command != "evaluate":
        parser.error(f"unrecognized arguments: {' '.join(extra_args)}")

    if args.command == "check-models":
        asyncio.run(check_models())
    elif args.command == "reset":
        asyncio.run(reset_database())
    elif args.command == "import":
        column_map = _parse_column_map(args.map)
        asyncio.run(import_corpus(args.path, args.limit, not args.no_extract, args.format, column_map))
    elif args.command == "ask":
        asyncio.run(ask(args.question))
    elif args.command == "evaluate":
        run_evaluate(extra_args)
    elif args.command == "inspect":
        asyncio.run(inspect_database(args.dictation, args.memory, args.trace))


if __name__ == "__main__":
    main()
