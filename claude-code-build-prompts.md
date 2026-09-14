# Claude Code — Build Prompts

Paste these one at a time. Do not paste the whole file. After each phase, run the
verification step and fix problems before moving on.

`CLAUDE.md` is in the repo root, so Claude Code reads the position, the stack and the
non-negotiables on every turn. These prompts only carry what changes per phase.

**Start a fresh Claude Code session at each phase boundary** (or run `/clear`). Long
sessions drift and start ignoring `CLAUDE.md`.

---

## Phase 0 — Scaffold

> Read CLAUDE.md fully before doing anything.
>
> Scaffold the repository exactly as laid out in §5. I want a running skeleton, not
> features yet.
>
> Deliver:
> - `docker-compose.yml` with three services: `db` (image `pgvector/pgvector:pg16`),
>   `api` (FastAPI, hot reload), `web` (Vite dev server). Named volume for Postgres.
>   Healthcheck on `db` so `api` waits for it.
> - FastAPI app with `/health` returning `{status, db, pgvector, extensions}` — it must
>   actually query the DB and confirm `vector`, `pg_trgm` and `unaccent` are installed.
> - Alembic initialised against the async engine, with one migration that does nothing
>   but `CREATE EXTENSION IF NOT EXISTS` for those three.
> - React + TypeScript + Vite + Tailwind + shadcn/ui, with one page that calls `/health`
>   and shows the result. Vite proxies `/api` to the api service.
> - `.env.example` with every variable named exactly, no values.
> - `api/kivi/config.py` — pydantic-settings, fails loudly on startup if a required
>   variable is missing.
>
> Pin every version. Then bring it up and show me the `/health` output.

**Verify:** `docker compose up --build`, open the web page, see all three extensions
reported present. Then `docker compose down -v && docker compose up` and confirm it
still works from nothing.

---

## Phase 1 — Schema and migrations

> Build the complete database schema as Alembic migrations. Schema first — no business
> logic in this phase.
>
> Tables: `dictation`, `entity`, `entity_alias`, `memory`, `memory_source`,
> `memory_entity`, `extraction_run`, `query_trace`, `user_action`.
>
> Requirements:
> - `dictation` is immutable — no update or delete paths anywhere in the codebase.
>   `content_hash` unique for idempotent re-import.
> - `memory.embedding` is `vector(1024)` with an HNSW index on cosine distance.
> - Generated `tsvector` columns with GIN indexes on `memory.claim` and
>   `dictation.formatted_output`.
> - GIN trigram index on `entity_alias.normalized`.
> - `memory.status` enum: active | superseded | forgotten | rejected.
>   `superseded_by_id` self-FK.
> - Partial unique index on `memory.claim_hash` where status in ('active','forgotten')
>   — this is what makes forget-tombstones work.
> - `memory_source` enforces provenance: add a check or a documented invariant that no
>   memory exists without at least one source row.
> - Two views: `entity_knowledge` (everything active about an entity, with source
>   counts) and `memory_history` (what was believed, what replaced it).
>
> Write matching SQLAlchemy 2.0 async models with full type annotations.
>
> Then: run the migration up, down, and up again, and show me `\d+` for `memory` and
> `entity_alias`.

**Verify:** migration is reversible. Read the generated SQL yourself — this schema is
the thing you will be asked about in an interview.

---

## Phase 2 — Model provider

> Build `api/kivi/models/provider.py`: one interface, two adapters.
>
> Interface: `complete(messages, *, schema=None, tier) -> Completion` where tier is
> `extraction` or `answer`, and `Completion` carries content, tokens in/out, model name,
> latency_ms and cost_usd.
>
> Adapters:
> - **Sarvam** — `https://api.sarvam.ai/v1/chat/completions`, header
>   `api-subscription-key`. CRITICAL: pass `reasoning_effort=None` for the extraction
>   tier, or reasoning tokens eat `max_tokens` and content comes back empty with
>   `finish_reason: "length"`. Handle `reasoning_content` being present.
> - **OpenAI-compatible fallback** — used when `SARVAM_API_KEY` is absent, so a reviewer
>   without a Sarvam key can still run everything.
>
> Also: `api/kivi/models/embeddings.py` loading BAAI/bge-m3 via sentence-transformers,
> cached at module level, batching, returning 1024-dim vectors. No API key required.
>
> When `schema=` is passed, validate the response against the Pydantic model and retry
> once with the validation error appended before failing.
>
> Per-token costs in config, not hardcoded. Retry with backoff on 429 and 5xx.
>
> Write a `kivi.cli check-models` command that calls each provider once and prints what
> worked, including token counts and cost. Run it and show me.

**Verify:** `check-models` succeeds with a real key. Deliberately unset `SARVAM_API_KEY`
and confirm the fallback engages.

---

## Phase 3 — The world and the corpus

> Read `docs/product-vision.md` first.
>
> **Write `data/world.md` before generating anything.** A two-page description of one
> persona's working life over ~12 weeks: a knowledge worker in Bengaluru. Projects,
> colleagues, vendors, a timeline, and these deliberately planted structures:
> - at least 4 facts each split across 3+ separate dictations, where no single record
>   contains the answer
> - at least 3 contradictions that resolve over time (a date moving, an owner changing)
> - one person who hands over their work to another mid-timeline
> - ~15 records containing health / emotional / personal content that MUST be rejected
> - ~10 records with ambiguous referents the extractor should drop rather than guess
>
> Then `data/generate_corpus.py` produces `corpus.jsonl` and `answer_key.jsonl`
> **together** from that world, so the answer key is a by-product and not a guess.
>
> Composition: ~30% durable work content, ~50% transient noise, ~8% distributed facts,
> ~5% contradictions, ~4% excluded-category, ~3% ambiguous.
>
> Language mix: ~55% English, ~30% Hindi-English code-mixed, ~10% Tamil or Kannada mixed
> with English, ~5% Devanagari script. **At least one distributed fact must cross a
> language boundary** — the entity for that thing must appear in two scripts.
>
> `raw_asr` must be meaningfully worse than `formatted_output`: missing punctuation,
> dropped short words, and above all **mangled proper nouns**, since that is what entity
> resolution has to survive. Use a seeded RNG so generation is reproducible.
>
> Generate it, then print me 10 random records and the answer-key summary counts.

**Verify:** read 20 records yourself. Do they sound like a person? Does the noise sound
genuinely disposable? If everything looks important, the gate has nothing to test.

---

## Phase 4 — The write path

> Build the extraction pipeline in `api/kivi/memory/`, in this order.
>
> **1. Gate** (`gate.py`) — one cheap call answering: does this dictation contain
> anything that will still matter next month? Returns yes/no plus a specific reason.
> Reject-reasons must be varied and concrete: "acknowledgement only", "transient
> scheduling", "restates a known fact", "ambiguous referent". Expect ~70% no.
>
> **2. Extraction** (`extract.py`) — strict Pydantic schema per CLAUDE.md §3. The schema
> must allow an **empty claims list with a reason**, and that is a success, not a
> failure. Every claim carries its verbatim source span with char offsets. Claims must
> be self-contained — no pronouns; resolve them against the dictation or drop the claim.
> The prompt must say: record what was said, do not infer beyond it.
>
> **3. Policy filter** (`policy.py`) — enforces CLAUDE.md §2 in **code**, after parsing,
> against the category field. Never rely on the prompt alone. When rejecting, log the
> **category only, never the claim text**.
>
> **4. Entity resolution** (`resolve.py`) — the four passes from CLAUDE.md §7. Use
> `indic-transliteration` for pass 2, `pg_trgm` + a phonetic key for pass 3. Bias toward
> under-merging. New entities start at low confidence and are flagged.
>
> **5. Consolidation** (`consolidate.py`) — classify each new claim against existing
> memories for the same entity as duplicate / refinement / contradiction / unrelated,
> and apply the four different actions. Check the forget-tombstone table before writing.
>
> **6. Orchestrator** (`pipeline.py`) — runs the above, writes memory + memory_source +
> memory_entity + embedding, and always writes an `extraction_run` row with tokens,
> cost, latency and decision.
>
> Unit tests for consolidation (all four cases) and for the policy filter (every
> excluded category). These two are the ones worth testing.
>
> Then run the full corpus and show me: records processed, abstained, rejected-by-policy,
> memories created, entities resolved, total cost, elapsed time.

**Verify the numbers before continuing.** ~500 records should yield roughly 100–200
remembered and 250–400 kept-nothing. If you get 2,000 memories the gate is broken. If
entity count is above ~150 for one persona, resolution is fragmenting. Check both.

---

## Phase 5 — The read path

> Build `api/kivi/retrieval/`.
>
> **1. Parse** (`parse.py`) — one structured call turning a question into
> `{intent, entities[], time_before, time_after, types[], app_context}`. Intent is
> recall | find | act | chitchat.
>
> **2. Route** — intent `find` goes straight to the `dictation` table with time and app
> filters. **No memory retrieval, no embedding.** "Find what I dictated into Slack around
> 5pm yesterday" should take ~40ms, not 1.5s.
>
> **3. Resolve** — reuse the write-path resolver. If a named thing in the question
> resolves to nothing, that is a strong abstention signal, not a reason to fuzzy-match
> to the nearest topic.
>
> **4. Candidates** (`candidates.py`) — three generators in parallel:
> entity-anchored (join `memory_entity`, recall-oriented, no scoring), lexical
> (tsvector), semantic (pgvector cosine). The entity join is what recovers distributed
> facts — it is not optional.
>
> **5. Fuse** (`fusion.py`) — reciprocal rank fusion, k=60. Then hard filters (time,
> type, status='active'), boost by confidence and log(occurrence_count), force pinned to
> top, take top 10. **No LLM reranker** — documented scope decision.
>
> **6. Sufficiency gate** (`sufficiency.py`) — a separate stage running BEFORE
> generation. One cheap call returning sufficient | partial | insufficient, plus three
> deterministic rules that can independently force abstention: no resolved entity for a
> named thing; best fused score below a configurable floor; single supporting memory
> below a confidence threshold. Thresholds in config, not hardcoded.
>
> **7. Answer** (`answer.py`) — receives selected memories with ids and source quotes;
> required to cite the ids it used. On abstention, the response must name what Kivi does
> have instead of just saying "I don't know".
>
> **8. Trace** — every turn writes one `query_trace` row with parsed filters, resolved
> entities, ALL candidates with their three component scores and fused score, selected
> ids, sufficiency verdict, tools called, answer, cited ids, tokens, cost, and both
> latency figures.
>
> Then ask these four and show me the full trace for each:
> - "Who do I need to chase to unblock <the blocked thing>?"  (distributed fact)
> - "When does <project> launch?"  (supersession — must give current value and note it
>   changed)
> - "What did I decide about the Hyderabad office move?"  (must abstain; no such thing)
> - "How have I been feeling lately?"  (must decline — outside the boundary)

**Verify:** the distributed-fact answer must cite memories from 3+ different dictations.
If it cites one, the entity join isn't firing.

---

## Phase 6 — Tools and interface

> **Four tools only** in `api/kivi/tools/`:
> `recall_from_memory`, `find_dictation`, `draft_with_context`, `manage_memory`.
> Do not add a fifth.
>
> `draft_with_context` is the demo — it drafts using remembered project state the user
> never mentioned in the request. Make it good.
>
> Then the React client, three screens:
>
> **Dictation feed** — chronological records with app context and timestamp. Each shows a
> badge: "remembered · N" or "nothing kept", with the reason on hover. That badge is the
> product position made visible.
>
> **Hey Kivi** — streamed answers over SSE, inline source chips, and a "Why this answer"
> expander showing memories used, their confidence, and links to the source dictations.
> Abstentions render in a calm, visually distinct style — not as an error.
>
> **What Kivi knows** — memory review grouped by entity. Plain-language claim, first
> learned, last confirmed, supporting dictation count, and three actions: confirm,
> correct, forget. Superseded memories collapse under their replacement. A permanent,
> prominent statement of what Kivi does not remember lives on this page.
>
> Correct → supersede old, create new with origin='user', confidence 1.0, pinned.
> Forget → status='forgotten' plus a `claim_hash` tombstone.
>
> The product must be intelligible without a developer console. No raw JSON on screen.

**Verify:** hand it to someone who has not seen the code. Can they work out what it does
without being told?

---

## Phase 7 — Evaluation

> Build `eval/`.
>
> `questions.jsonl` — 60–80 questions with expected memory ids and expected answers, or
> an explicit expectation of abstention. Distribution: 15 single-fact, 12 distributed,
> 10 temporal, 8 knowledge-update, **15 unanswerable**, **8 should-not-know**,
> 6 preference-application, 6 dictation-lookup.
>
> `run_eval.py` runs the full pipeline and reports:
> memory precision and recall vs the answer key; rejection precision; **policy
> compliance (must be 1.00)**; retrieval recall@10 and nDCG@10; answer accuracy
> (model-judged with a fixed, disclosed prompt); citation accuracy; **correct abstention
> rate AND false abstention rate — report both**; retrieval and end-to-end latency at p50
> and p95; database growth per 100 records by table; model calls and tokens per record
> and per query; total ingestion cost and mean cost per query.
>
> Output: `summary.json`, `per_question.jsonl` with the full inspection chain
> (input → memory created/rejected/changed → provenance → retrieved → behaviour →
> reason), a standalone `report.html` that opens with no server, and `run_meta.json` with
> models, prompt versions, commit SHA and timestamp.
>
> Sample `pg_total_relation_size` every 100 records during ingestion and chart the curve
> in the report — growth should be visibly sublinear if consolidation works.
>
> Run it and commit the results.

**Verify:** open `report.html`. Would you believe it if someone else produced it?

---

## Phase 8 — Hardening

> Four things, in order.
>
> **1. Second corpus.** Generate a deliberately different persona — different domain,
> different language mix — and run the full evaluation **without changing a single
> threshold**. Report what breaks. This is what the reviewers' corpus will do to us.
>
> **2. CLI completeness.** `import` (JSONL and CSV, with column mapping), `evaluate`,
> `reset`, `inspect`. Import must validate before ingesting, **warn** on missing optional
> fields rather than failing, and print a summary: records, remembered, nothing-kept,
> rejected, entities, elapsed, cost.
>
> **3. RUN.md.** Declare Docker Compose as the primary review method in the first line.
> Exact versions, every environment variable and what happens without it, literal
> copy-pasteable commands, the URL and what the reviewer should see, **five specific demo
> interactions including one that must abstain**, the evaluation command, the
> corpus-import procedure, where to inspect memory and traces, the reset command, and the
> three failures we actually hit while building.
>
> **4. README.** Product, architecture (with the diagram), three concrete use cases with
> real input and output, limitations **including the phase-8.1 second-corpus result even
> if unflattering**, results with a link to report.html, and an AI use section stating
> what was AI-assisted.

**Then do the real test:** clone the repo into a fresh container with no cached images
and no `.env`, and follow `RUN.md` literally without using anything you know that is not
written down. Every improvisation is a bug in the document. Do this twice — the second
time after you have stopped changing code.

---

## Useful mid-build prompts

> Show me the last 20 `extraction_run` rows where decision='abstained', with reasons.
> Are these good judgement calls or is the gate just broken?

> Print every entity with its alias count and mention count, ordered by alias count.
> Is anything obviously fragmented or wrongly merged?

> For this question, show me the full `query_trace`: every candidate with its three
> component scores, what the fusion did, and why the sufficiency gate decided what it
> decided.

> Review the last phase against CLAUDE.md §2 and §3. Did we add anything the position
> forbids, or skip a non-negotiable?
