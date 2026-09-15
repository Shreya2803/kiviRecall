# RUN.md — Primary Review Method

**Docker Compose is the primary and only supported way to run and review this
project.** It requires no local Python or Node install — everything runs in
containers.

## Versions

| Component | Version | Where it's pinned |
|---|---|---|
| PostgreSQL + pgvector | `pgvector/pgvector:pg16` (Postgres 16) | `docker-compose.yml` |
| Python | 3.11.10 | `api/Dockerfile` |
| Node | 20.18.1 (Alpine) | `web/Dockerfile` |
| React | 18.3.1 | `web/package.json` |
| Vite | 5.4.11 | `web/package.json` |
| TypeScript | 5.6.3 | `web/package.json` |

## Prerequisites

- Docker Desktop, running.
- A `.env` file in the repo root — copy `.env.example` and fill in the values.

### Environment variables

| Variable | Required | Without it |
|---|---|---|
| `DATABASE_URL`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | **Yes, always** | The API fails to start — `Settings()` raises a `pydantic.ValidationError` immediately (see `api/kivi/config.py`), before serving anything. |
| `SARVAM_API_KEY` | For extraction/answering | Falls back to `GEMINI_API_KEY` if set (an OpenAI-compatible fallback, so a reviewer without a Sarvam key can still run everything). If neither is set, any command that calls a model (`import`, `ask`, `evaluate`, Hey Kivi) fails immediately with `ProviderError: No provider configured`. The health check, schema, and static UI all still work with neither key set. |
| `SARVAM_BASE_URL` | No | Defaults to `https://api.sarvam.ai`. |
| `EXTRACTION_MODEL`, `ANSWER_MODEL` | No | Default to `sarvam-105b` for both tiers. |
| `GEMINI_API_KEY`, `GEMINI_BASE_URL`, `GEMINI_EXTRACTION_MODEL`, `GEMINI_ANSWER_MODEL` | No | Only read when `SARVAM_API_KEY` is absent. Defaults: `gemini-3.5-flash-lite` (extraction), `gemini-3.5-flash` (answer). |
| `EMBEDDING_MODEL`, `USE_LOCAL_EMBEDDINGS` | No | Default to `BAAI/bge-m3`, run locally via `sentence-transformers` — no API key needed for embeddings regardless of which chat provider is active. |

## Bring everything up

```bash
docker compose up --build
```

This starts three services:

- `db` — Postgres 16 with pgvector, on `localhost:5432`. The API waits for its
  healthcheck before starting.
- `api` — FastAPI on `http://localhost:8000`. Applies Alembic migrations on startup,
  then serves with hot reload.
- `web` — Vite dev server on `http://localhost:5173`. Proxies `/api/*` to the `api`
  service.

## Verify

Open **`http://localhost:5173`**. You should see the Kivi UI with three tabs
(Dictation feed, Hey Kivi, What Kivi knows) and, before any corpus is imported, an
empty dictation feed and a "Nothing learned yet" state on the memory tab — this is
expected, not an error.

Or directly: `curl http://localhost:8000/health` — expect
`{"status":"ok","db":true,"pgvector":true,"extensions":{"vector":true,"pg_trgm":true,"unaccent":true}}`.

## Import a corpus

```bash
docker compose exec api python -m kivi.cli import /data/corpus.jsonl
```

`/data` is the repo's `data/` directory, mounted read-only into the `api` container.
This validates the whole file before ingesting anything: a record missing a required
field (`id`, `spoken_at`, `raw_asr`, `formatted_output`) is dropped and reported by id,
never silently skipped and never aborting the rest of the batch; a record missing an
optional field is kept, with a warning. It prints a summary — records, remembered,
nothing-kept, rejected, entities, elapsed, cost.

Re-running this command is idempotent (dictations are deduplicated on content hash).
Import a CSV the same way, with explicit column mapping if your headers don't already
match `id`/`spoken_at`/`raw_asr`/`formatted_output`/`app_context`/`window_title`/
`language`/`duration_ms`/`style_id`:

```bash
docker compose exec api python -m kivi.cli import /data/your_corpus.csv \
  --map id=RecordId --map spoken_at=Timestamp \
  --map raw_asr=RawText --map formatted_output=CleanText
```

Unmapped fields are guessed from the header (case/spacing-insensitive); the resolved
mapping actually used is printed before anything is read, so a wrong guess is visible
immediately rather than silently importing the wrong column.

This does real model calls (extraction), so importing the full 500-record
`data/corpus.jsonl` takes on the order of 20-30 minutes and a small real cost (a few
tens of cents on Sarvam pricing — the exact figure is printed at the end and recorded
per-record in `extraction_run`).

## Five demo interactions

Ask these through the Hey Kivi tab at `http://localhost:5173`, or from the command line:

```bash
docker compose exec api python -m kivi.cli ask "<question>"
```

Extraction and answering both make live model calls, so exact wording — and for
questions that depend on retrieval ranking, sometimes which facts surface first —
varies import to import (see Results/Limitations: false abstention is the largest
known gap right now). The two decline demos (4 and 5) and the lookup demo (3) don't
depend on retrieval quality and are reliable regardless.

1. **Draft using remembered context** (the `draft_with_context` tool) —
   *"Write a quick update on the Kavach rollout for the team."* Expect bullet points (a
   stated preference from week 2 of the corpus) referencing real, dictated Kavach
   state rather than anything restated in the request itself.
2. **Correct a memory and watch it supersede** — open the "What Kivi knows" tab, find
   the preference memory ("I prefer status updates as bullet points..."), click
   **Correct**, and change it. The old row becomes `status=superseded` with a
   `superseded_by_id` link — never overwritten — and the new one shows up with
   `origin=user`, pinned. Same thing from the CLI: `kivi inspect --memory <id>` before
   and after.
3. **Direct dictation lookup** (the `find_dictation` tool, no memory involved) —
   *"What did I dictate in Slack between January 5th and January 18th?"* Returns the
   actual dictations from that window and app, verbatim, and says outright if it's
   showing fewer than actually matched — this path never touches memory or an
   embedding, so it's unaffected by retrieval nondeterminism.
4. **An honest decline — out of scope entirely** — *"What's the current status of
   Project Phoenix?"* No such project exists in the corpus. Kivi should say so and name
   something it does track instead of guessing.


## Evaluate

```bash
docker compose exec api python -m kivi.cli evaluate
```

(equivalently `docker compose exec api python eval/run_eval.py` — same program, `kivi
evaluate` just forwards its arguments to it.) This is one command that runs the entire
evaluation pipeline: truncates the database, re-imports `data/corpus.jsonl` from
scratch (sampling table sizes every 100 records for the growth chart), runs all 80
questions in `eval/questions.jsonl` through the real Hey Kivi answer pipeline, judges
each answer with a fixed model prompt, and writes `eval/results/summary.json`,
`per_question.jsonl`, `run_meta.json` and a standalone `report.html` (open it directly
in a browser — no server needed).

Expect this to take 20-30 minutes and a small real API cost, for the same reason
importing the corpus does. To iterate without paying that cost again, evaluate against
whatever is already in the database instead — this skips only the growth-sampling
section of the report, since there's no fresh import to sample during:

```bash
docker compose exec api python -m kivi.cli evaluate --no-reset
```

## Inspect memory and traces

```bash
docker compose exec api python -m kivi.cli inspect                    # database-wide summary
docker compose exec api python -m kivi.cli inspect --dictation dict_00105   # one dictation, its extraction_run, and any memories sourced from it
docker compose exec api python -m kivi.cli inspect --memory 41              # one memory: status, confidence, provenance, linked entities
docker compose exec api python -m kivi.cli inspect --trace 232              # one Hey Kivi turn: every candidate considered, what was selected, why
```

The same information is visible in the UI's "What Kivi knows" tab (memories, grouped
by entity, with confirm/correct/forget) and behind each answer's "Why this answer?"
expander (Hey Kivi tab) — `inspect` is the same data without a browser.

## Reset

```bash
docker compose exec api python -m kivi.cli reset
```

Truncates every application table (dictations, memories, entities, traces, everything)
while leaving the schema and Alembic's migration state untouched — a clean base to
import a different corpus into without rebuilding the database.

To also prove the schema and extension setup work on a genuinely cold database, not
just an emptied one:

```bash
docker compose down -v
docker compose up --build
```

`down -v` drops the named Postgres volume.

## Three failures we actually hit while building

1. **Sarvam's thinking mode silently eating the whole response.** Thinking mode is on
   by default; reasoning tokens count against `max_tokens` and are billed as completion
   tokens. With a modest `max_tokens`, extraction calls came back with
   `finish_reason: "length"` and **empty content** — no error, just nothing to parse.
   Fixed by always passing `reasoning_effort=None` whenever JSON-mode output is
   required (`api/kivi/models/provider.py`), and by having `_request` raise explicitly
   on empty content with `finish_reason=length` rather than let it fail confusingly
   downstream as a JSON parse error.
2. **Entity confidence that started low and never went back up.** New entities were
   created at `confidence=0.3`, but
   nothing ever raised it on repeated unambiguous matches — permanently discounting
   every entity-anchored memory in retrieval's ranking relative to generic semantic
   matches. Fixed by graduating an entity to `confidence=1.0` on its second
   exact or transliteration-normalised match (`api/kivi/memory/resolve.py`).
3. **Honest-sounding declines that leaked the very thing they claimed not to know.**
   Building the Phase 7 evaluation surfaced this twice, in two different places: (a)
   `run_answer` was shown the real retrieved memory content even when the sufficiency
   verdict was "insufficient," and the model would sometimes decline in one sentence
   and paraphrase what it had just been shown in the next; (b) even after fixing (a),
   the *sufficiency stage's own explanation* of why it declined — free text generated
   by an LLM reading the real memory claims — was still being passed into the
   answering model's prompt, and it could restate them just as easily. Both are fixed
   by withholding retrieved content and the LLM-authored verdict reason once the
   verdict is insufficient, offering only entity names as a hint (`api/kivi/retrieval/answer.py`).
   Neither the prompt nor a single layer of "don't repeat this" instruction was
   sufficient on its own — this took two passes to actually close.

---

*This file covers Phases 0 through 8. Prior phases (ingestion, memory extraction,
retrieval, the four Hey Kivi tools, the React client) are exercised implicitly by
importing a corpus and using the app.*
