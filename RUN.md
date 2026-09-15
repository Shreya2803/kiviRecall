# RUN.md — Primary Review Method

Docker Compose is the primary and only supported way to run this project. It requires
no local Python or Node install.

## Prerequisites

- Docker Desktop, running.
- A `.env` file in the repo root — copy `.env.example` and fill in the values. Only
  `DATABASE_URL`, `POSTGRES_USER`, `POSTGRES_PASSWORD` and `POSTGRES_DB` are required to
  bring the stack up. `SARVAM_API_KEY` is required for later phases (extraction,
  answering) but not for this skeleton's health check.

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

- Open `http://localhost:5173` — the page calls `/api/health` and shows the result.
- Or directly: `curl http://localhost:8000/health` — expect
  `{"status":"ok","db":true,"pgvector":true,"extensions":{"vector":true,"pg_trgm":true,"unaccent":true}}`.

## Reset from nothing

```bash
docker compose down -v
docker compose up --build
```

`down -v` drops the named Postgres volume, so this proves the migration and extension
setup work on a cold database, not just a warm one.

## Import the corpus

Requires `SARVAM_API_KEY` (or `GEMINI_API_KEY` as the OpenAI-compatible fallback) set in
`.env` — extraction makes real model calls.

```bash
docker compose exec api python -m kivi.cli import /data/corpus.jsonl
```

`/data` is the repo's `data/` directory, mounted read-only into the `api` container.
Re-running this command is idempotent (dictations are deduplicated on content hash), so
it's safe to import the reviewer's own corpus the same way:

```bash
docker compose exec api python -m kivi.cli import /data/your_corpus.jsonl
```

To wipe everything and start clean (schema untouched, only rows):

```bash
docker compose exec api python -m kivi.cli reset
```

Ask Hey Kivi a question from the command line, with the full retrieval trace printed:

```bash
docker compose exec api python -m kivi.cli ask "Who owns Kavach's rollout now?"
```

## Evaluate

```bash
docker compose exec api python eval/run_eval.py
```

This is one command that runs the entire evaluation pipeline: it truncates the database,
re-imports `data/corpus.jsonl` from scratch (sampling table sizes every 100 records for
the growth chart), runs all 80 questions in `eval/questions.jsonl` through the real Hey
Kivi answer pipeline, judges each answer with a fixed model prompt, and writes
`eval/results/summary.json`, `per_question.jsonl`, `run_meta.json` and a standalone
`report.html` (open it directly in a browser — no server needed).

This does a full corpus re-extraction, so expect it to take on the order of 20-30 minutes
and a small real API cost (a few tens of cents on Sarvam pricing). To iterate on the
eval questions/scoring without paying that cost again, evaluate against whatever is
already in the database instead:

```bash
docker compose exec api python eval/run_eval.py --no-reset
```

(`--no-reset` skips the growth-sampling section of the report, since there's no fresh
import to sample during.)

---

*This file covers Phases 0 through 7. Prior phases (ingestion, memory extraction,
retrieval, the four Hey Kivi tools, the React client) are exercised implicitly by
importing a corpus and using the app at `http://localhost:5173`*
