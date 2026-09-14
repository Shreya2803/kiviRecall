# Your Setup — Everything You Do Yourself

Do all of this **before** the first Claude Code prompt. It takes about 45 minutes, most
of which is Docker downloading things.

---

## 1. Install

| Tool | Version | Notes |
|---|---|---|
| **VS Code** | latest | |
| **Node.js** | 20 LTS or newer | Required by Claude Code *and* the React client |
| **Docker Desktop** | latest | Must be **running** before anything works. On Windows, enable the WSL 2 backend. |
| **Git** | any recent | |
| **Python** | 3.11 | Only needed if you ever run the API outside Docker. Optional. |

Then install Claude Code:

```bash
npm install -g @anthropic-ai/claude-code
claude --version
```

In VS Code, install the **Claude Code** extension from the marketplace, then open the
command palette and sign in. You need a Claude Pro/Max subscription or an Anthropic API
key with credit.

**Check Docker has enough room.** Settings → Resources: give it at least 4 GB RAM and
20 GB disk. Postgres plus the bge-m3 embedding model (~2 GB) plus node_modules will use
more than the default allocation on some machines.

---

## 2. The vector database — you do not need one

This is the most common wasted afternoon on this kind of project, so to be explicit:

**pgvector is a Postgres extension, not a separate product.** You do not need Pinecone,
Qdrant, Weaviate, Chroma or Milvus. You do not need an account anywhere. You pull one
Docker image that is Postgres with the extension already compiled in:

```yaml
# docker-compose.yml — Claude Code will write this, but this is the line that matters
db:
  image: pgvector/pgvector:pg16
```

The extension is then switched on with one SQL statement inside your first migration:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- fuzzy matching for ASR-mangled names
CREATE EXTENSION IF NOT EXISTS unaccent;  -- normalising accented text
```

That is the entire vector database setup. Your embeddings become a `vector(1024)` column
on the `memory` table, sitting next to the relational columns — which is the whole point,
because retrieval needs to filter by entity and date in the same query as the similarity
search.

---

## 3. API keys

### Sarvam (required)

1. Sign up at **https://dashboard.sarvam.ai**
2. Generate an API key. It looks like `sk_xxxxxxxx`.
3. Check which models your key can reach. **`sarvam-m` is deprecated** — the current
   chat models are `sarvam-105b` and `sarvam-105b-conversations`.

Two API details that will cost you an hour if you don't know them:

- The auth header is **`api-subscription-key`**, not `Authorization`. (It also accepts
  `Authorization: Bearer <key>` for OpenAI-compatible tooling, but the native header is
  the documented one.)
- **Thinking mode is on by default.** Reasoning tokens come back in a separate
  `reasoning_content` field, count against `max_tokens`, and are billed. If you set a
  small `max_tokens` for JSON extraction, reasoning will consume the entire budget and
  you get `finish_reason: "length"` with **empty content** — which looks exactly like a
  broken prompt. For extraction, pass `reasoning_effort=None`.

Quick test before you build anything:

```bash
curl https://api.sarvam.ai/v1/chat/completions \
  -H "api-subscription-key: $SARVAM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"sarvam-105b","messages":[{"role":"user","content":"Say OK"}],"max_tokens":50,"reasoning_effort":null}'
```

If that returns text, you are good. If it 403s, the key is wrong. If `content` is empty
but `reasoning_content` has text, you forgot `reasoning_effort`.

### OpenAI or any OpenAI-compatible key (optional but recommended)

Add one as a fallback so a reviewer without Sarvam credentials can still run your system.
Groq's free tier works fine for this. It costs you ten minutes and removes a dependency
between your submission and someone else's account.

### Embeddings — no key needed

Use **BAAI/bge-m3** locally through `sentence-transformers`. It is multilingual, handles
Devanagari and code-mixed text properly, and produces 1024-dim vectors. First run
downloads ~2 GB into the container; cache it in a named Docker volume so it doesn't
re-download on every rebuild.

An English-only embedding model will quietly fail on half your corpus. Do not use
`all-MiniLM-L6-v2` here.

---

## 4. Your `.env`

Create `.env` in the repo root. **Add `.env` to `.gitignore` immediately** — before you
paste a key into it.

```bash
SARVAM_API_KEY=sk_your_key_here
SARVAM_BASE_URL=https://api.sarvam.ai

EXTRACTION_MODEL=sarvam-105b
ANSWER_MODEL=sarvam-105b

# Optional fallback, used when SARVAM_API_KEY is absent
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1

EMBEDDING_MODEL=BAAI/bge-m3
USE_LOCAL_EMBEDDINGS=1

DATABASE_URL=postgresql+asyncpg://kivi:kivi@db:5432/kivi
POSTGRES_USER=kivi
POSTGRES_PASSWORD=kivi
POSTGRES_DB=kivi
```

Commit `.env.example` with the same keys and **no values**. The brief requires it.

---

## 5. Repository, before the first prompt

```bash
mkdir kivi-semantic-memory && cd kivi-semantic-memory
git init
printf '.env\n__pycache__/\nnode_modules/\n*.pyc\n.venv/\n' > .gitignore

mkdir docs
# copy in product-positioning.md and product-vision.md
# copy in CLAUDE.md at the repo root

git add -A
git commit -m "docs: product positioning and vision"
```

**Commit Part One on its own, before any code exists.** The brief says to complete and
preserve both documents before beginning Part Two, and git history is the proof. It costs
nothing.

Then open the folder in VS Code and start Claude Code.

---

## 6. Getting the most out of Claude Code

### Point it at Sarvam's docs

Sarvam publishes an MCP server specifically for AI coding tools, plus a plain-text docs
index. Either of these stops Claude Code guessing at API shapes:

```bash
claude mcp add --transport http sarvam-docs https://docs.sarvam.ai/_mcp/server
```

If that syntax has changed, the fallback is just as good — tell Claude Code to fetch
`https://docs.sarvam.ai/llms.txt` (an index for agents) and append `.md` to any docs URL
for the markdown version of that page.

### Session hygiene

- **Start a fresh session at each phase boundary**, or run `/clear`. Long sessions drift
  and start quietly ignoring `CLAUDE.md`.
- Run `/init` once at the start so Claude Code indexes the repo structure.
- Use plan mode (Shift+Tab twice in the terminal client) for Phases 1, 4 and 5 — the
  schema and the two pipelines are the places where an agreed plan beats a surprise.
- When it goes wrong, `git checkout .` and re-prompt with a sharper instruction. Do not
  try to talk it out of a bad design over five turns; that burns context and rarely works.

### Permissions

Claude Code asks before running commands. For this project it is reasonable to allow
`docker compose`, `alembic`, `pytest`, `npm` and `git` without prompting each time, and
to keep confirmation on anything destructive. Set this via `/permissions`.

### Commit after every phase

Small commits give you somewhere to roll back to, and the reviewers can see how the
project developed. Do not build for three days and commit once.

---

## 7. What this will cost

Rough, and worth checking against your own dashboard after the first 50 records rather
than at the end.

| Item | Estimate |
|---|---|
| Corpus generation (500 records) | ₹150–400 |
| Full ingestion run | ₹100–300 per run |
| Re-runs while tuning prompts | budget for 5–8 full runs |
| Evaluation (~70 questions + judge) | ₹50–150 per run |
| **Total** | **roughly ₹1,500–3,000** |

Two ways to keep this down:

- **Use the small model for extraction.** It runs 500 times; the answering model runs
  seventy. Mixing them up is how this bill triples.
- **Test on 50 records first.** Get the gate and extraction behaving on a slice before
  running the full corpus. Add a `--limit` flag to the import command on day one.

Sarvam gives new accounts free credits, which may cover a meaningful share of this.

---

## 8. Verification checkpoints

Do not move past a checkpoint that fails.

| After | Check |
|---|---|
| Phase 0 | `docker compose down -v && docker compose up --build` works from nothing, and `/health` reports all three extensions present |
| Phase 1 | Migration runs up, down, and up again cleanly |
| Phase 2 | `check-models` works; unset `SARVAM_API_KEY` and confirm the fallback engages |
| Phase 3 | Read 20 generated records. Do they sound like a person, and is the noise genuinely disposable? |
| Phase 4 | ~100–200 records remembered, ~250–400 kept nothing, under ~150 entities. Wildly different numbers mean the gate or the resolver is broken. |
| Phase 5 | The distributed-fact question cites memories from **3+ different dictations**. One citation means the entity join is not firing. |
| Phase 6 | Someone who has not seen the code can work out what the product does |
| Phase 7 | `report.html` opens standalone and you would believe it from a stranger |
| Phase 8 | Clean-clone RUN.md test passes twice, the second time after the last code change |

---

## 9. Things that will go wrong

**Docker not running.** Every command fails with a confusing socket error. Check the
whale icon first, always.

**Port already in use.** Postgres on 5432 and Vite on 5173 are commonly taken. Remap in
`docker-compose.yml` and remember to update `RUN.md`.

**Empty LLM responses during extraction.** Almost certainly `reasoning_effort`. See §3.

**Embedding model re-downloading every rebuild.** You forgot the named volume for the
HuggingFace cache. Two gigabytes each time gets old fast.

**Windows line endings breaking shell scripts in containers.** Add a `.gitattributes`
with `* text=auto eol=lf`.

**Alembic not seeing the async engine.** It needs configuring for async explicitly; the
default template is sync. Claude Code handles this if you mention it, which Phase 0 does.

**Everything works on your machine and fails on a clean clone.** This is the one that
actually costs marks. Which is why §8's last checkpoint exists.
