# CLAUDE.md — Kivi Semantic Memory

This file is read on every turn. It is the constitution of this project.
When any instruction conflicts with this file, stop and ask.

---

## 1. What we are building

A semantic memory system for **Kivi**, Sarvam's voice-first interface for the PC.

Kivi has two modes today:
- **Regular dictation** — the person speaks, Kivi writes. Styles and phonetic memory
  already make this personal and correct.
- **Hey Kivi** — the person addresses Kivi directly and it uses tools to help.

We are adding **semantic memory**: durable understanding learned from everything the
person has dictated, used to answer questions through Hey Kivi.

This is a hiring assignment. It will be reviewed by a coding agent that clones the repo,
follows `RUN.md`, and repairs nothing. Then a human will import a corpus of ~500
dictations from a real user that we have never seen, and ask Hey Kivi questions about it.

---

## 2. The product position — this constrains the code

> **Kivi remembers your work, not you.**

Kivi hears everything a person authors, in every application. That makes it the only
part of the system that can remember their work as a whole, and the last part that
should be trusted to profile the person doing it.

### What we remember
- **Projects** and their state
- **People** and their roles in the work (who owns what)
- **Decisions** and when they changed
- **Commitments** and deadlines
- **Terminology** — project names, colleagues, vendors, acronyms
- **Stated output preferences** — "I like status updates as bullets"

### What we NEVER remember — these are hard rules, not guidelines
- Mood, stress, emotional state, confidence, personality
- Health, medical or therapy content
- Opinions or characterisations of named colleagues
  (we store "Priya owns the design system"; never "Priya is slow")
- Financial, family or personal-life detail
- Anything inferred from *how* the person speaks, as opposed to what they said
- Anything derived from a single ambiguous mention

**There is deliberately no `trait` memory type in the schema.** If a task seems to
require one, the task is wrong — raise it instead of adding the column.

### The dictation boundary
Semantic memory **never silently alters regular dictation output.** Dictation has one
contract: what you said is what appears. Memory may feed learned proper nouns into
phonetic correction or offer a visible, dismissible suggestion. It never rewrites.
All memory-driven answering happens inside Hey Kivi.

---

## 3. Non-negotiable mechanisms

These map directly to grading criteria. Do not simplify them away.

| Mechanism | Rule |
|---|---|
| **Audit trail** | Every dictation processed writes an `extraction_run` row — **including the ones that produce no memories**, with a plain-language reason. Expect ~70% of records to produce nothing. |
| **Rejection privacy** | When the policy filter rejects a candidate, log its **category only**, never its content. Logging the rejected claim recreates the store the position forbids. |
| **Entity join** | Distributed facts are recovered by joining `memory_entity` on resolved entities — not by hoping vector search returns them. This is the hardest graded capability. |
| **Sufficiency gate** | A separate, testable stage that runs **before** answer generation and can force abstention. Never a line in the answering prompt. |
| **Provenance** | No memory may exist without ≥1 `memory_source` row. Every answer cites memory ids that resolve to source dictations. |
| **Supersession** | Contradictions never overwrite. Old row → `status='superseded'`, `valid_to` set, `superseded_by_id` linked. |
| **Forget survives re-import** | Forgetting writes a tombstone keyed on `claim_hash`. Re-running ingestion must not resurrect it. |
| **Query trace** | Every Hey Kivi turn writes a `query_trace` row: filters, all candidates with scores, what was selected, sufficiency verdict, tokens, cost, latency. |

---

## 4. Stack

```
Frontend    React 18 + TypeScript + Vite + Tailwind + shadcn/ui + lucide-react
Backend     FastAPI, Python 3.11, async SQLAlchemy 2.0, Pydantic v2, Alembic
Database    PostgreSQL 16 + pgvector + tsvector (FTS) + pg_trgm (fuzzy)
LLM         Sarvam chat completions, behind a provider interface
Embeddings  BAAI/bge-m3 locally via sentence-transformers (multilingual, no API key)
Runtime     Docker Compose — this is the declared primary review method
```

### Sarvam API — read this before writing any model code

- Base URL `https://api.sarvam.ai`. Sarvam chat models are on `/v1/chat/completions`.
- Auth header is `api-subscription-key: sk_...`. It also accepts
  `Authorization: Bearer <key>` for OpenAI-compatible tooling.
- **`sarvam-m` is deprecated.** Use `sarvam-105b` (128K context, reasoning/agentic work)
  or `sarvam-105b-conversations`. Check what the key actually has access to before
  hardcoding anything.
- **Thinking mode is ON by default.** Reasoning tokens are returned in a separate
  `reasoning_content` field, count against `max_tokens`, and are billed as completion
  tokens. A small `max_tokens` can be entirely consumed by reasoning, returning
  `finish_reason: "length"` with **empty content**.
  → For extraction, always pass `reasoning_effort=None` and a generous `max_tokens`.
- Official SDK: `pip install sarvamai`, `SarvamAI(api_subscription_key=...)`.
- Docs index for agents: `https://docs.sarvam.ai/llms.txt`. Append `.md` to any docs
  URL for markdown.

**Always route model calls through `api/kivi/models/provider.py`.** Never call the
Sarvam SDK or `httpx` directly from business logic. The provider must support an
OpenAI-compatible fallback so a reviewer without a Sarvam key can still run the system.

Use **two model tiers**: a small/cheap model for extraction (runs 500×) and a larger one
for answering (runs on demand). Record the model on every `extraction_run` and
`query_trace` row so the cost report is measured, not estimated.

---

## 5. Repository layout

```
kivi-semantic-memory/
├── README.md              product, architecture, use cases, limitations,
│                          results, AI use
├── RUN.md                 primary review method + exact commands
├── docker-compose.yml
├── .env.example           exact variable names, no values
├── docs/
│   ├── product-positioning.md    ≤100 words
│   ├── product-vision.md         ≤600 words
│   └── architecture.md
├── api/
│   ├── kivi/
│   │   ├── ingest/        normalisation, JSONL + CSV readers
│   │   ├── memory/        gate, extract, policy, resolve, consolidate
│   │   ├── retrieval/     parse, candidates, fusion, sufficiency
│   │   ├── tools/         the four Hey Kivi tools
│   │   ├── models/        provider interface + adapters
│   │   ├── db/            SQLAlchemy models
│   │   └── cli.py         import · evaluate · reset · inspect
│   ├── alembic/versions/  migrations — a required deliverable
│   └── tests/
├── web/                   React client
├── data/
│   ├── world.md           the persona world, written BEFORE the corpus
│   ├── corpus.jsonl       ~500 records
│   ├── answer_key.jsonl   gold memories per record
│   └── generate_corpus.py
└── eval/
    ├── questions.jsonl
    ├── run_eval.py
    └── results/           committed output
```

---

## 6. Data contract

The reviewers' corpus will only reliably have these fields. **Only these four are
required.** Everything else is optional and must degrade gracefully with a warning,
never an error.

```json
{
  "id":               "dict_00187",
  "spoken_at":        "2026-02-10T14:22:00+05:30",
  "raw_asr":          "reminder to rohit ki tara ka payment gateway ...",
  "formatted_output": "Reminder to Rohit: the Tara payment gateway ...",

  "app_context":      "Slack",
  "window_title":     "#tara-build",
  "language":         ["hi", "en"],
  "duration_ms":      8400,
  "style_id":         "chat_casual"
}
```

---

## 7. Multilingual is not optional

Kivi supports 22+ Indian languages **with code-switching**. A single dictation may mix
Devanagari, Latin-script Hindi and English in one sentence.

Entity resolution runs four passes, cheapest first:
1. Exact match on normalised alias
2. Transliteration-normalised match (`प्रिया` → `priya`)
3. Phonetic + trigram similarity (`Preeya`, `Prea` — ordinary ASR errors on proper nouns)
4. Model-assisted disambiguation, only when 2+ candidates tie

**Bias toward under-merging.** Wrongly fusing two colleagues produces confidently wrong
answers; leaving them separate produces incomplete ones the sufficiency gate will catch.

---

## 8. Conventions

- Async everywhere in the API. No sync DB calls inside request handlers.
- Pydantic models for every LLM input and output. Validate the policy category **in
  code** after parsing — never rely on the prompt alone for the retention boundary.
- Every schema change is an Alembic migration. Never `create_all()` in application code.
- Type hints on all Python. Strict TypeScript, no `any`.
- Prompts live in versioned files under `api/kivi/memory/prompts/`, with the version
  string recorded on every audit row.
- Secrets only from environment. Never commit `.env`.
- Conventional commits.

---

## 9. Working style for this project

- **Do not write code before the relevant migration exists.** Schema first.
- When a phase is done, run it end to end and show me real output before moving on.
- If a requirement here seems to conflict with what I just asked for, say so rather than
  picking one silently.
- Prefer simple mechanisms I can explain in an interview over clever ones I cannot.
- Do not add dependencies without telling me what it is for.
- Do not build extra Hey Kivi tools. Four, no more. The brief explicitly prefers a narrow
  set used convincingly.

---

## 10. Explicitly out of scope

Speech recognition. Production Kivi integration. Real microphone input. Authentication /
multi-user. Deployment. A developer console (the product must be intelligible without
one). Any feature listed in §2 under "what we never remember".
