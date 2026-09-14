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

---

*This file currently covers Phase 0 (running skeleton) only. Ingestion, evaluation and
CLI commands will be appended here as those phases land.*
