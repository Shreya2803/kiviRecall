"""Phase 7 evaluation harness 

By default this does the whole pipeline in one command: truncates the database,
re-imports data/corpus.jsonl from scratch while sampling table sizes every 100
records, runs eval/questions.jsonl end to end through the real Hey Kivi answer
pipeline, judges each answer, and writes summary.json / per_question.jsonl /
run_meta.json / report.html into eval/results/.

Run (inside the api container, where `kivi` and the Sarvam/Gemini key live):

    docker compose exec api python eval/run_eval.py

Use --no-reset to evaluate against whatever is already in the database instead
(faster, but the growth-sampling section of the report will say so and skip
the curve). See the bottom of this file for all flags.

A note on "expected memory ids" : extraction is an LLM call, so the exact integer
memory ids a fresh import produces are not stable across runs even against
this same corpus — a hardcoded id in questions.jsonl would silently stop
meaning anything the day the corpus is re-extracted. questions.jsonl instead
records the stable thing: which *dictations* (fixed ids from the committed
corpus.jsonl) ground each gold answer. This script resolves those to whatever
memory ids currently exist via memory_source at evaluation time, which is the
same join the retrieval pipeline itself depends on — so the metric measures
the real capability (did the entity join recover the right evidence) instead
of an accident of row numbering.
"""
import argparse
import asyncio
import datetime
import json
import re
import subprocess
import sys
import time
from pathlib import Path

# --- make `kivi` importable whether this runs on the host (repo checkout) or
# inside the api container (where docker-compose mounts this file at
# /app/eval/run_eval.py and the package lives directly at /app/kivi). ---
try:
    import kivi  # noqa: F401
except ImportError:
    for _candidate in (Path(__file__).resolve().parents[1] / "api", Path("/app")):
        if (_candidate / "kivi").is_dir():
            sys.path.insert(0, str(_candidate))
            break

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kivi.cli import _APP_TABLES  # noqa: E402
from kivi.config import settings  # noqa: E402
from kivi.db.session import async_session_factory  # noqa: E402
from kivi.ingest.jsonl_reader import ingest_records, read_jsonl  # noqa: E402
from kivi.memory import extract as extract_module  # noqa: E402
from kivi.memory import gate as gate_module  # noqa: E402
from kivi.memory.pipeline import process_dictation  # noqa: E402
from kivi.models.provider import ModelProvider, get_provider  # noqa: E402
from kivi.retrieval import answer as answer_module  # noqa: E402
from kivi.retrieval import parse as parse_module  # noqa: E402
from kivi.retrieval import sufficiency as sufficiency_module  # noqa: E402
from kivi.retrieval.ask import answer_question  # noqa: E402
from kivi.tools import draft_with_context as draft_module  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
# Inside the api container, ROOT (parents[1] of /app/eval/run_eval.py) is just
# /app — the api package's own root, not the repo root — and data/ is mounted
# separately at /data. On a host checkout ROOT really is the repo root.
DATA_DIR = Path("/data") if Path("/data").is_dir() else ROOT / "data"
RESULTS_DIR = Path(__file__).parent / "results"
JUDGE_PROMPT_VERSION = "judge_v1"
JUDGE_PROMPT = (Path(__file__).parent / "prompts" / f"{JUDGE_PROMPT_VERSION}.md").read_text(encoding="utf-8")

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
DEFAULT_NOW = datetime.datetime(2026, 3, 28, 12, 0, tzinfo=IST)


# ---------------------------------------------------------------------------
# Judge
# ---------------------------------------------------------------------------

class _JudgeVerdict(BaseModel):
    verdict: str
    reason: str


async def judge_answer(
    provider: ModelProvider, question: str, gold_answer: str, actual_answer: str,
) -> tuple[str, str, dict]:
    messages = [
        {"role": "system", "content": JUDGE_PROMPT},
        {
            "role": "user",
            "content": (
                f"Question: {question}\n\nGold reference: {gold_answer}\n\n"
                f"Kivi's actual answer: {actual_answer}"
            ),
        },
    ]
    completion = await provider.complete(messages, schema=_JudgeVerdict, tier="extraction")
    parsed = _JudgeVerdict.model_validate_json(completion.content)
    verdict = parsed.verdict if parsed.verdict in ("correct", "partial", "incorrect") else "partial"
    cost = {"tokens_in": completion.tokens_in, "tokens_out": completion.tokens_out, "cost_usd": completion.cost_usd}
    return verdict, parsed.reason, cost


# ---------------------------------------------------------------------------
# Reset + instrumented import (growth sampling every 100 records)
# ---------------------------------------------------------------------------

async def _table_sizes(session: AsyncSession) -> dict[str, int]:
    sizes = {}
    for table in _APP_TABLES:
        size = await session.scalar(text(f"SELECT pg_total_relation_size('{table}')"))
        sizes[table] = int(size or 0)
    return sizes


async def reset_and_import(corpus_path: Path, provider: ModelProvider, sample_every: int = 100) -> dict:
    async with async_session_factory() as session:
        await session.execute(text(f"TRUNCATE {', '.join(_APP_TABLES)} RESTART IDENTITY CASCADE"))
        await session.commit()
    print(f"[import] reset {len(_APP_TABLES)} tables")

    records = read_jsonl(corpus_path)
    async with async_session_factory() as session:
        inserted, skipped = await ingest_records(session, records)
    print(f"[import] ingested {len(inserted)} dictation(s), skipped {skipped} duplicate(s)")

    growth_samples: list[dict] = []
    async with async_session_factory() as session:
        growth_samples.append({"processed": 0, "sizes": await _table_sizes(session)})

    processed = 0
    abstained = 0
    errored = 0
    total_memories = 0
    total_cost = 0.0
    total_tokens_in = 0
    total_tokens_out = 0
    start = time.monotonic()

    for dictation in inserted:
        result = await process_dictation(async_session_factory, provider, dictation)
        processed += 1
        if result.outcome == "no_memories_found":
            abstained += 1
        elif result.outcome == "error":
            errored += 1
            print(f"[import]   [{result.dictation_id}] ERROR: {result.reason}")
        total_memories += result.memories_created
        total_cost += result.cost_usd
        total_tokens_in += result.tokens_in
        total_tokens_out += result.tokens_out

        if processed % sample_every == 0:
            async with async_session_factory() as session:
                growth_samples.append({"processed": processed, "sizes": await _table_sizes(session)})
            print(f"[import]   ...{processed}/{len(inserted)} processed (${total_cost:.4f} so far)")

    if processed % sample_every != 0:
        async with async_session_factory() as session:
            growth_samples.append({"processed": processed, "sizes": await _table_sizes(session)})

    elapsed = time.monotonic() - start
    return {
        "dictations_processed": processed,
        "dictations_skipped_duplicate": skipped,
        "abstained_no_memories": abstained,
        "errored": errored,
        "memories_created": total_memories,
        "ingestion_cost_usd": total_cost,
        "ingestion_tokens_in": total_tokens_in,
        "ingestion_tokens_out": total_tokens_out,
        "ingestion_elapsed_s": elapsed,
        "growth_samples": growth_samples,
    }


# ---------------------------------------------------------------------------
# Corpus-level checks: policy compliance, rejection precision/recall,
# memory precision/recall vs. the answer key
# ---------------------------------------------------------------------------

DURABLE_PREFIXES = ("fact:", "contradiction:", "durable:", "handover:")
REJECTABLE_PREFIXES = ("excluded:", "ambiguous:")


def _load_answer_key(path: Path) -> dict[str, list[str]]:
    out = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            out[row["id"]] = row["tags"]
    return out


async def corpus_level_metrics(session: AsyncSession, answer_key: dict[str, list[str]]) -> dict:
    rows = (
        await session.execute(
            text(
                "SELECT dictation_id, outcome, reason, memories_created_count, policy_rejections "
                "FROM extraction_run"
            )
        )
    ).all()
    by_dictation = {r.dictation_id: r for r in rows}

    excluded_ids = {i for i, tags in answer_key.items() if any(t.startswith("excluded:") for t in tags)}
    ambiguous_ids = {i for i, tags in answer_key.items() if any(t.startswith("ambiguous:") for t in tags)}
    should_reject_ids = excluded_ids | ambiguous_ids
    durable_ids = {i for i, tags in answer_key.items() if any(t.startswith(DURABLE_PREFIXES) for t in tags)}

    produced_memory_ids = {i for i, r in by_dictation.items() if r.memories_created_count > 0}

    def _gate_reason_prefix(reason: str) -> str:
        return reason.split(":", 1)[0].strip()

    actually_rejected_ids = {
        i for i, r in by_dictation.items()
        if _gate_reason_prefix(r.reason) in ("personal_or_health_content", "opinion_about_colleague", "ambiguous_referent")
        or (r.policy_rejections not in (None, {}))
    }

    excluded_compliant = {i for i in excluded_ids if by_dictation.get(i) and by_dictation[i].memories_created_count == 0}
    policy_compliance = len(excluded_compliant) / len(excluded_ids) if excluded_ids else None

    rejection_tp = actually_rejected_ids & should_reject_ids
    rejection_precision = len(rejection_tp) / len(actually_rejected_ids) if actually_rejected_ids else None
    rejection_recall = len(rejection_tp) / len(should_reject_ids) if should_reject_ids else None

    memory_precision = len(produced_memory_ids & durable_ids) / len(produced_memory_ids) if produced_memory_ids else None
    memory_recall = len(produced_memory_ids & durable_ids) / len(durable_ids) if durable_ids else None

    non_compliant_examples = sorted(excluded_ids - excluded_compliant)

    return {
        "policy_compliance": policy_compliance,
        "policy_compliance_note": "fraction of excluded-category dictations that produced zero memories — must be 1.00",
        "policy_non_compliant_dictation_ids": non_compliant_examples,
        "rejection_precision": rejection_precision,
        "rejection_recall": rejection_recall,
        "memory_precision_vs_answer_key": memory_precision,
        "memory_recall_vs_answer_key": memory_recall,
        "n_excluded_dictations": len(excluded_ids),
        "n_ambiguous_dictations": len(ambiguous_ids),
        "n_durable_dictations": len(durable_ids),
        "n_dictations_with_memories": len(produced_memory_ids),
    }


# ---------------------------------------------------------------------------
# Retrieval-quality helpers
# ---------------------------------------------------------------------------

async def relevant_memory_ids(session: AsyncSession, dictation_ids: list[str]) -> set[int]:
    if not dictation_ids:
        return set()
    rows = (
        await session.execute(
            text(
                "SELECT DISTINCT ms.memory_id FROM memory_source ms "
                "JOIN memory m ON m.id = ms.memory_id AND m.status = 'active' "
                "WHERE ms.dictation_id = ANY(:ids)"
            ),
            {"ids": dictation_ids},
        )
    ).all()
    return {r.memory_id for r in rows}


def recall_at_k(relevant: set[int], ranked_ids: list[int], k: int = 10) -> float | None:
    if not relevant:
        return None
    top_k = set(ranked_ids[:k])
    return len(relevant & top_k) / len(relevant)


def ndcg_at_k(relevant: set[int], ranked_ids: list[int], k: int = 10) -> float | None:
    if not relevant:
        return None
    import math

    dcg = 0.0
    for i, mid in enumerate(ranked_ids[:k]):
        if mid in relevant:
            dcg += 1.0 / math.log2(i + 2)
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    return dcg / idcg if idcg > 0 else 0.0


def ranked_from_trace(candidates_trace: list[dict]) -> list[int]:
    return [c["memory_id"] for c in sorted(candidates_trace, key=lambda c: -c["boosted_score"])]


BULLET_RE = re.compile(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+")


def looks_like_bullets(text_: str) -> bool:
    lines = [l for l in text_.splitlines() if l.strip()]
    if len(lines) < 2:
        return False
    bulleted = sum(1 for l in lines if BULLET_RE.match(l))
    return bulleted >= max(2, len(lines) // 2)


# ---------------------------------------------------------------------------
# Per-question evaluation
# ---------------------------------------------------------------------------

async def eval_dictation_lookup(q: dict, provider: ModelProvider, now: datetime.datetime, corpus: list[dict]) -> dict:
    result = await answer_question(async_session_factory, provider, q["question"], now=now)

    gold_ids = set()
    time_after = datetime.datetime.fromisoformat(q["time_after"]) if q.get("time_after") else None
    time_before = datetime.datetime.fromisoformat(q["time_before"]) if q.get("time_before") else None
    app_filter = q.get("app_context_filter")
    for rec in corpus:
        if app_filter and app_filter.lower() not in (rec.get("app_context") or "").lower():
            continue
        spoken_at = datetime.datetime.fromisoformat(rec["spoken_at"])
        if time_after and spoken_at < time_after:
            continue
        if time_before and spoken_at > time_before:
            continue
        gold_ids.add(rec["id"])

    found_ids: set[str] = set()
    if result.trace_id:
        async with async_session_factory() as session:
            row = await session.execute(
                text("SELECT filters FROM query_trace WHERE id = :id"), {"id": result.trace_id}
            )
            filters = row.scalar_one_or_none()
            if filters:
                found_ids = set(filters.get("found_dictation_ids") or [])

    precision = len(found_ids & gold_ids) / len(found_ids) if found_ids else (1.0 if not gold_ids else 0.0)
    recall = len(found_ids & gold_ids) / len(gold_ids) if gold_ids else None

    return {
        "route": result.route,
        "answer": result.answer,
        "found_dictation_ids": sorted(found_ids),
        "gold_dictation_ids": sorted(gold_ids),
        "lookup_precision": precision,
        "lookup_recall": recall,
        "sufficiency_verdict": result.sufficiency_verdict,
        "tokens_in": result.tokens_in, "tokens_out": result.tokens_out, "cost_usd": result.cost_usd,
        "retrieval_latency_ms": result.retrieval_latency_ms, "generation_latency_ms": result.generation_latency_ms,
        "latency_ms": result.latency_ms, "trace_id": result.trace_id,
    }


async def eval_draft(q: dict, provider: ModelProvider, now: datetime.datetime) -> dict:
    start = time.monotonic()
    async with async_session_factory() as session:
        draft_result = await draft_module.draft_with_context(session, provider, q["question"], now)
    latency_ms = int((time.monotonic() - start) * 1000)

    async with async_session_factory() as session:
        relevant = await relevant_memory_ids(session, q.get("expected_dictation_ids", []))

    verdict, reason, judge_cost = await judge_answer(
        provider, q["question"], q.get("gold_answer", ""), draft_result.draft,
    )
    bullets = looks_like_bullets(draft_result.draft)
    tokens_in = sum(c.tokens_in for c in draft_result.completions)
    tokens_out = sum(c.tokens_out for c in draft_result.completions)
    cost = sum(c.cost_usd for c in draft_result.completions)

    return {
        "route": "draft",
        "answer": draft_result.draft,
        "used_memory_ids": draft_result.used_memory_ids,
        "cited_memory_ids": draft_result.cited_memory_ids,
        "relevant_memory_ids": sorted(relevant),
        "used_relevant_memory": bool(relevant & set(draft_result.used_memory_ids)),
        "uses_bullets": bullets,
        "judge_verdict": verdict, "judge_reason": reason,
        "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost,
        "latency_ms": latency_ms,
        "judge_cost_usd": judge_cost["cost_usd"],
    }


async def eval_qa(q: dict, provider: ModelProvider, now: datetime.datetime) -> dict:
    result = await answer_question(async_session_factory, provider, q["question"], now=now)

    expect_abstention = bool(q.get("expect_abstention"))
    out = {
        "route": result.route,
        "answer": result.answer,
        "sufficiency_verdict": result.sufficiency_verdict,
        "sufficiency_reason": result.sufficiency_reason,
        "cited_memory_ids": result.cited_memory_ids,
        "selected_memory_ids": result.selected_memory_ids,
        "resolved_entity_ids": result.resolved_entity_ids,
        "unresolved_entities": result.unresolved_entities,
        "tokens_in": result.tokens_in, "tokens_out": result.tokens_out, "cost_usd": result.cost_usd,
        "retrieval_latency_ms": result.retrieval_latency_ms, "generation_latency_ms": result.generation_latency_ms,
        "latency_ms": result.latency_ms, "trace_id": result.trace_id,
    }

    abstained = result.sufficiency_verdict in ("insufficient",) or result.route.startswith("out_of_bounds")

    if expect_abstention:
        out["correct_abstention"] = abstained
        excluded_ids = q.get("excluded_dictation_ids", [])
        if excluded_ids:
            async with async_session_factory() as session:
                forbidden = await relevant_memory_ids(session, excluded_ids)
            leaked = bool(forbidden & set(result.cited_memory_ids))
            out["privacy_leak"] = leaked
        verdict, reason, judge_cost = await judge_answer(
            provider, q["question"], q.get("gold_answer", ""), result.answer,
        )
        out["judge_verdict"] = verdict
        out["judge_reason"] = reason
        out["judge_cost_usd"] = judge_cost["cost_usd"]
        return out

    out["false_abstention"] = abstained

    async with async_session_factory() as session:
        relevant = await relevant_memory_ids(session, q.get("expected_dictation_ids", []))
    ranked = ranked_from_trace(result.candidates_trace)
    out["relevant_memory_ids"] = sorted(relevant)
    out["recall_at_10"] = recall_at_k(relevant, ranked, 10)
    out["ndcg_at_10"] = ndcg_at_k(relevant, ranked, 10)

    cited = set(result.cited_memory_ids)
    out["citation_precision"] = (len(cited & relevant) / len(cited)) if cited else None

    verdict, reason, judge_cost = await judge_answer(
        provider, q["question"], q.get("gold_answer", ""), result.answer,
    )
    out["judge_verdict"] = verdict
    out["judge_reason"] = reason
    out["judge_cost_usd"] = judge_cost["cost_usd"]
    return out


async def eval_question(q: dict, provider: ModelProvider, now: datetime.datetime, corpus: list[dict]) -> dict:
    try:
        if q.get("route_hint") == "draft":
            detail = await eval_draft(q, provider, now)
        elif q.get("route_hint") == "find":
            detail = await eval_dictation_lookup(q, provider, now, corpus)
        else:
            detail = await eval_qa(q, provider, now)
        return {**q, **detail, "eval_error": None}
    except Exception as exc:  # noqa: BLE001 — one bad question must not sink the run
        return {**q, "eval_error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))
    return s[idx]


def aggregate(per_question: list[dict]) -> dict:
    ok = [r for r in per_question if not r.get("eval_error")]
    by_cat: dict[str, list[dict]] = {}
    for r in ok:
        by_cat.setdefault(r["category"], []).append(r)

    def cat_summary(rows: list[dict]) -> dict:
        judged = [r for r in rows if "judge_verdict" in r]
        correct = sum(1 for r in judged if r["judge_verdict"] == "correct")
        partial = sum(1 for r in judged if r["judge_verdict"] == "partial")
        incorrect = sum(1 for r in judged if r["judge_verdict"] == "incorrect")
        recalls = [r["recall_at_10"] for r in rows if r.get("recall_at_10") is not None]
        ndcgs = [r["ndcg_at_10"] for r in rows if r.get("ndcg_at_10") is not None]
        precisions = [r["citation_precision"] for r in rows if r.get("citation_precision") is not None]
        return {
            "n": len(rows),
            "answer_accuracy": (correct + 0.5 * partial) / len(judged) if judged else None,
            "n_correct": correct, "n_partial": partial, "n_incorrect": incorrect,
            "retrieval_recall_at_10": sum(recalls) / len(recalls) if recalls else None,
            "retrieval_ndcg_at_10": sum(ndcgs) / len(ndcgs) if ndcgs else None,
            "citation_accuracy": sum(precisions) / len(precisions) if precisions else None,
            "false_abstention_rate": (
                sum(1 for r in rows if r.get("false_abstention")) / len(rows) if rows else None
            ),
        }

    latencies_total = [r["latency_ms"] for r in ok if r.get("latency_ms") is not None]
    latencies_retrieval = [r["retrieval_latency_ms"] for r in ok if r.get("retrieval_latency_ms") is not None]
    total_cost = sum(r.get("cost_usd", 0) or 0 for r in ok)
    judge_cost = sum(r.get("judge_cost_usd", 0) or 0 for r in ok)
    total_tokens = sum((r.get("tokens_in", 0) or 0) + (r.get("tokens_out", 0) or 0) for r in ok)

    abstention_rows = [r for r in ok if r["category"] in ("unanswerable", "should_not_know")]
    correct_abstentions = sum(1 for r in abstention_rows if r.get("correct_abstention"))
    privacy_leaks = [r["id"] for r in abstention_rows if r.get("privacy_leak")]

    answerable_rows = [
        r for r in ok
        if r["category"] not in ("unanswerable", "should_not_know", "dictation_lookup")
    ]
    false_abstentions = sum(1 for r in answerable_rows if r.get("false_abstention"))

    lookup_rows = by_cat.get("dictation_lookup", [])
    lookup_precisions = [r["lookup_precision"] for r in lookup_rows if r.get("lookup_precision") is not None]
    lookup_recalls = [r["lookup_recall"] for r in lookup_rows if r.get("lookup_recall") is not None]

    return {
        "n_questions": len(per_question),
        "n_evaluated": len(ok),
        "n_errors": len(per_question) - len(ok),
        "by_category": {cat: cat_summary(rows) for cat, rows in by_cat.items()},
        "correct_abstention_rate": correct_abstentions / len(abstention_rows) if abstention_rows else None,
        "false_abstention_rate": false_abstentions / len(answerable_rows) if answerable_rows else None,
        "privacy_leaks": privacy_leaks,
        "dictation_lookup_precision": sum(lookup_precisions) / len(lookup_precisions) if lookup_precisions else None,
        "dictation_lookup_recall": sum(lookup_recalls) / len(lookup_recalls) if lookup_recalls else None,
        "latency_ms_p50": _percentile(latencies_total, 50),
        "latency_ms_p95": _percentile(latencies_total, 95),
        "retrieval_latency_ms_p50": _percentile(latencies_retrieval, 50),
        "retrieval_latency_ms_p95": _percentile(latencies_retrieval, 95),
        "total_query_cost_usd": total_cost,
        "judge_cost_usd": judge_cost,
        "mean_cost_per_query_usd": total_cost / len(ok) if ok else None,
        "mean_tokens_per_query": total_tokens / len(ok) if ok else None,
    }


# ---------------------------------------------------------------------------
# report.html — standalone, no server, no external dependencies
# ---------------------------------------------------------------------------

def render_report(summary: dict, corpus_metrics: dict, import_stats: dict | None, run_meta: dict) -> str:
    data = json.dumps(
        {"summary": summary, "corpus_metrics": corpus_metrics, "import_stats": import_stats, "run_meta": run_meta},
        default=str,
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Kivi eval report</title>
<style>
:root {{ color-scheme: light dark; }}
body {{ font-family: -apple-system, Segoe UI, sans-serif; margin: 0; padding: 24px 16px; background: #fafafa; color: #1a1a1a; }}
@media (prefers-color-scheme: dark) {{ body {{ background: #16181d; color: #e8e8e8; }} .card {{ background: #1e2128 !important; border-color: #333 !important; }} table {{ border-color: #333 !important; }} td, th {{ border-color: #333 !important; }} }}
.wrap {{ max-width: 980px; margin: 0 auto; }}
h1 {{ font-size: 1.4rem; }}
h2 {{ font-size: 1.1rem; margin-top: 2rem; }}
.card {{ background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 16px; margin-bottom: 16px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }}
.stat {{ font-size: 1.4rem; font-weight: 600; }}
.label {{ font-size: 0.75rem; opacity: 0.7; text-transform: uppercase; letter-spacing: 0.03em; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
td, th {{ border: 1px solid #ddd; padding: 6px 8px; text-align: left; }}
.pill {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 0.75rem; }}
.ok {{ background: #d4edda; color: #155724; }}
.warn {{ background: #fff3cd; color: #856404; }}
.bad {{ background: #f8d7da; color: #721c24; }}
svg {{ max-width: 100%; }}
code {{ font-size: 0.8rem; }}
</style></head>
<body><div class="wrap">
<h1>Kivi Semantic Memory — Evaluation Report</h1>
<div id="meta" class="card"></div>
<h2>Headline metrics</h2>
<div id="headline" class="grid card"></div>
<h2>By category</h2>
<div id="categories"></div>
<h2>Corpus-level checks</h2>
<div id="corpus" class="card"></div>
<h2>Database growth during ingestion</h2>
<div id="growth" class="card"></div>
<script>
const DATA = {data};

function pct(x) {{ return x === null || x === undefined ? "—" : (x * 100).toFixed(1) + "%"; }}
function ms(x) {{ return x === null || x === undefined ? "—" : Math.round(x) + " ms"; }}
function usd(x) {{ return x === null || x === undefined ? "—" : "$" + x.toFixed(4); }}
function pill(ok) {{ return ok ? '<span class="pill ok">pass</span>' : '<span class="pill bad">fail</span>'; }}

const s = DATA.summary, cm = DATA.corpus_metrics, meta = DATA.run_meta;

document.getElementById("meta").innerHTML = `
  <div class="grid">
    <div><div class="label">Commit</div><div>${{meta.commit_sha || "unknown"}}</div></div>
    <div><div class="label">Run at</div><div>${{meta.run_at}}</div></div>
    <div><div class="label">Extraction model</div><div>${{meta.extraction_model}}</div></div>
    <div><div class="label">Answer model</div><div>${{meta.answer_model}}</div></div>
    <div><div class="label">Prompt versions</div><div>${{Object.entries(meta.prompt_versions).map(([k,v])=>k+"="+v).join(", ")}}</div></div>
  </div>`;

document.getElementById("headline").innerHTML = `
  <div><div class="stat">${{s.n_evaluated}}/${{s.n_questions}}</div><div class="label">Questions evaluated</div></div>
  <div><div class="stat">${{pct(s.correct_abstention_rate)}}</div><div class="label">Correct abstention rate</div></div>
  <div><div class="stat">${{pct(s.false_abstention_rate)}}</div><div class="label">False abstention rate</div></div>
  <div><div class="stat">${{pct(cm.policy_compliance)}} ${{pill(cm.policy_compliance === 1)}}</div><div class="label">Policy compliance (must be 100%)</div></div>
  <div><div class="stat">${{ms(s.latency_ms_p50)}} / ${{ms(s.latency_ms_p95)}}</div><div class="label">End-to-end latency p50 / p95</div></div>
  <div><div class="stat">${{ms(s.retrieval_latency_ms_p50)}} / ${{ms(s.retrieval_latency_ms_p95)}}</div><div class="label">Retrieval latency p50 / p95</div></div>
  <div><div class="stat">${{usd(s.total_query_cost_usd)}}</div><div class="label">Total query cost</div></div>
  <div><div class="stat">${{usd(s.mean_cost_per_query_usd)}}</div><div class="label">Mean cost / query</div></div>
  <div><div class="stat">${{s.privacy_leaks.length}}</div><div class="label">Privacy leaks detected</div></div>
`;

let catRows = "";
for (const [cat, c] of Object.entries(s.by_category)) {{
  catRows += `<tr><td>${{cat}}</td><td>${{c.n}}</td><td>${{pct(c.answer_accuracy)}}</td>`
    + `<td>${{c.n_correct}}/${{c.n_partial}}/${{c.n_incorrect}}</td>`
    + `<td>${{pct(c.retrieval_recall_at_10)}}</td><td>${{pct(c.retrieval_ndcg_at_10)}}</td>`
    + `<td>${{pct(c.citation_accuracy)}}</td><td>${{pct(c.false_abstention_rate)}}</td></tr>`;
}}
document.getElementById("categories").innerHTML = `
  <table><thead><tr><th>Category</th><th>n</th><th>Answer accuracy</th><th>correct/partial/incorrect</th>
  <th>Recall@10</th><th>nDCG@10</th><th>Citation acc.</th><th>False abstention</th></tr></thead>
  <tbody>${{catRows}}</tbody></table>
  <p style="font-size:0.8rem;opacity:0.7">Recall@10/nDCG@10/citation accuracy only apply to categories with source-dictation ground truth (not dictation_lookup, which has its own precision/recall below: ${{pct(s.dictation_lookup_precision)}} / ${{pct(s.dictation_lookup_recall)}}).</p>
`;

document.getElementById("corpus").innerHTML = `
  <table>
    <tr><td>Policy compliance</td><td>${{pct(cm.policy_compliance)}} ${{pill(cm.policy_compliance === 1)}} (${{cm.n_excluded_dictations}} excluded-category dictations checked)</td></tr>
    <tr><td>Rejection precision</td><td>${{pct(cm.rejection_precision)}}</td></tr>
    <tr><td>Rejection recall</td><td>${{pct(cm.rejection_recall)}}</td></tr>
    <tr><td>Memory precision vs. answer key</td><td>${{pct(cm.memory_precision_vs_answer_key)}}</td></tr>
    <tr><td>Memory recall vs. answer key</td><td>${{pct(cm.memory_recall_vs_answer_key)}}</td></tr>
  </table>
  ${{cm.policy_non_compliant_dictation_ids.length ? '<p style="color:#a33">Non-compliant dictation ids: ' + cm.policy_non_compliant_dictation_ids.join(", ") + '</p>' : ''}}
`;

const growth = (DATA.import_stats && DATA.import_stats.growth_samples) || [];
if (growth.length > 1) {{
  const tables = Object.keys(growth[0].sizes);
  const w = 880, h = 260, pad = 40;
  const maxX = Math.max(...growth.map(g => g.processed));
  const maxY = Math.max(...growth.flatMap(g => Object.values(g.sizes)));
  const colors = ["#4c78a8","#f58518","#54a24b","#e45756","#72b7b2","#eeca3b","#b279a2","#9c755f","#bab0ac"];
  const px = p => pad + (p / maxX) * (w - 2*pad);
  const py = v => h - pad - (v / (maxY || 1)) * (h - 2*pad);
  let paths = "";
  tables.forEach((t, i) => {{
    const d = growth.map((g,j) => (j===0?"M":"L") + px(g.processed).toFixed(1) + "," + py(g.sizes[t]).toFixed(1)).join(" ");
    paths += `<path d="${{d}}" fill="none" stroke="${{colors[i % colors.length]}}" stroke-width="2"/>`;
  }});
  let legend = tables.map((t,i) => `<span style="color:${{colors[i%colors.length]}}">■</span> ${{t}}`).join(" &nbsp; ");
  document.getElementById("growth").innerHTML = `
    <svg viewBox="0 0 ${{w}} ${{h}}" width="100%">
      <line x1="${{pad}}" y1="${{h-pad}}" x2="${{w-pad}}" y2="${{h-pad}}" stroke="#888"/>
      <line x1="${{pad}}" y1="${{pad}}" x2="${{pad}}" y2="${{h-pad}}" stroke="#888"/>
      ${{paths}}
      <text x="${{pad}}" y="${{h-10}}" font-size="11">0</text>
      <text x="${{w-pad-40}}" y="${{h-10}}" font-size="11">${{maxX}} records</text>
      <text x="4" y="${{pad}}" font-size="11">${{(maxY/1024/1024).toFixed(1)}} MB</text>
    </svg>
    <div style="font-size:0.8rem">${{legend}}</div>
    <p style="font-size:0.8rem;opacity:0.7">Sampled via pg_total_relation_size every 100 records during a fresh import. A sublinear (flattening) curve on the memory table indicates consolidation is deduplicating repeated claims instead of growing linearly with dictation count.</p>
  `;
}} else {{
  document.getElementById("growth").innerHTML = "<p>No growth samples this run — pass without <code>--no-reset</code> to sample table sizes during a fresh import.</p>";
}}
</script>
</div></body></html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def git_sha() -> str:
    # Inside the api container the repo's .git is mounted read-only at
    # /repo-git (ROOT itself, /app, is just the api package's own root there).
    for args in (["git", "--git-dir=/repo-git", "rev-parse", "HEAD"], ["git", "rev-parse", "HEAD"]):
        try:
            return subprocess.check_output(args, cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        except Exception:  # noqa: BLE001
            continue
    return "unknown"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DATA_DIR / "corpus.jsonl")
    parser.add_argument("--answer-key", type=Path, default=DATA_DIR / "answer_key.jsonl")
    parser.add_argument("--questions", type=Path, default=Path(__file__).parent / "questions.jsonl")
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--no-reset", action="store_true", help="Skip reset+reimport; evaluate against the current DB")
    parser.add_argument("--limit-questions", type=int, default=None)
    parser.add_argument("--now", type=str, default=DEFAULT_NOW.isoformat())
    args = parser.parse_args()

    now = datetime.datetime.fromisoformat(args.now)
    provider = get_provider()
    answer_key = _load_answer_key(args.answer_key)
    corpus = read_jsonl(args.corpus)
    questions = [json.loads(l) for l in args.questions.open(encoding="utf-8")]
    if args.limit_questions:
        questions = questions[: args.limit_questions]

    import_stats = None
    if not args.no_reset:
        print(f"=== Reset + import ({args.corpus}) via {provider.name} ===")
        import_stats = await reset_and_import(args.corpus, provider)
        print(
            f"[import] done: {import_stats['dictations_processed']} processed, "
            f"{import_stats['memories_created']} memories, "
            f"${import_stats['ingestion_cost_usd']:.4f}, "
            f"{import_stats['ingestion_elapsed_s']:.1f}s"
        )
    else:
        print("=== Skipping reset/import (--no-reset): evaluating current DB state ===")
        # Keep showing the growth curve from the last real import instead of the
        # report silently losing it just because this particular run didn't
        # re-import — the file on disk still reflects the database's real history.
        existing = args.out_dir / "import_stats.json"
        if existing.exists():
            import_stats = json.loads(existing.read_text(encoding="utf-8"))
            print(f"[import] reusing growth samples from {existing} (not from this run)")

    async with async_session_factory() as session:
        corpus_metrics = await corpus_level_metrics(session, answer_key)
    print(f"[corpus] policy_compliance={corpus_metrics['policy_compliance']}")

    print(f"=== Running {len(questions)} eval question(s) ===")
    per_question = []
    for i, q in enumerate(questions, 1):
        row = await eval_question(q, provider, now, corpus)
        per_question.append(row)
        flag = row.get("eval_error") or row.get("judge_verdict") or row.get("lookup_precision") or ""
        print(f"[eval] {i}/{len(questions)} {q['id']} ({q['category']}): {flag}")

    summary = aggregate(per_question)

    run_meta = {
        "run_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "commit_sha": git_sha(),
        "extraction_model": settings.extraction_model if settings.sarvam_api_key else settings.gemini_extraction_model,
        "answer_model": settings.answer_model if settings.sarvam_api_key else settings.gemini_answer_model,
        "provider": provider.name,
        "prompt_versions": {
            "gate": gate_module.PROMPT_VERSION,
            "extract": extract_module.PROMPT_VERSION,
            "parse": parse_module.PROMPT_VERSION,
            "sufficiency": sufficiency_module.PROMPT_VERSION,
            "answer": answer_module.PROMPT_VERSION,
            "draft": draft_module.PROMPT_VERSION,
            "judge": JUDGE_PROMPT_VERSION,
        },
        "eval_now": now.isoformat(),
        "n_questions": len(questions),
        "reset_performed": not args.no_reset,
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "summary.json").write_text(
        json.dumps({"summary": summary, "corpus_metrics": corpus_metrics}, indent=2, default=str), encoding="utf-8"
    )
    with (args.out_dir / "per_question.jsonl").open("w", encoding="utf-8") as f:
        for row in per_question:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    (args.out_dir / "run_meta.json").write_text(json.dumps(run_meta, indent=2, default=str), encoding="utf-8")
    if import_stats is not None:
        (args.out_dir / "import_stats.json").write_text(json.dumps(import_stats, indent=2, default=str), encoding="utf-8")
    (args.out_dir / "report.html").write_text(
        render_report(summary, corpus_metrics, import_stats, run_meta), encoding="utf-8"
    )

    print()
    print("=== Summary ===")
    print(json.dumps(summary, indent=2, default=str))
    print(f"\nWrote results to {args.out_dir}")


if __name__ == "__main__":
    asyncio.run(main())
