# Kivi Semantic Memory

> This README is a work in progress. It currently covers **Results** and
> **Limitations**. The remaining required sections — product, architecture, use
> cases, AI use — are still pending a dedicated pass.

## Results

Full numbers, per-question inspection chains, and a standalone chart of database
growth during ingestion: `eval/results/report.html` (open directly in a browser, no
server needed) — the exact model, prompt versions and commit it ran against are in
`eval/results/run_meta.json`. Reproduce with `docker compose exec api python
eval/run_eval.py` — see `RUN.md`. The numbers below are from a fresh reset + full
500-record re-import + all 80 questions in `eval/questions.jsonl`.

| Metric | Result |
|---|---|
| Policy compliance (must be 1.00) | **1.00** — 0 of 20 excluded-category dictations produced a memory |
| Privacy leaks (should-not-know questions) | **0** |
| Correct abstention rate | 0.91–0.96 |
| False abstention rate | **0.55** |
| Retrieval recall@10 / nDCG@10 (by category) | 0.36–0.67 / 0.14–0.30 |
| Citation accuracy | 0.25–0.58 |
| Dictation-lookup precision / recall | 0.45 / 0.30–0.33 |
| Memory precision / recall vs. answer key | 0.93 / 0.62 |
| Rejection precision / recall | 0.65 / 0.75 |
| End-to-end latency p50 / p95 | 1.5s / 4.5s |
| Retrieval-only latency p50 / p95 | 0.25s / 1.9s |
| Ingestion cost (500 records) | $0.26 |
| Query cost (80 questions, incl. judging) | $0.05 |

The two hard requirements — policy compliance and zero privacy leaks on direct
should-not-know probes — both hold. The weak numbers are retrieval quality and
false abstention, and building the eval is what surfaced *why*, not just *that*:

- **The sufficiency gate's unresolved-entity rule fires on bad parses, not just
  missing knowledge.** The query parser sometimes extracts an over-literal surface
  form from the question ("Tara QA cycle", "Tara backend handover") instead of the
  real entity name ("Tara"). That surface form fails to resolve, and Rule 1 forces
  abstention before the lexical/semantic candidates — which often already found the
  right memory — ever reach the sufficiency judgment. This is the single largest
  contributor to the false-abstention rate.
- **Retrieval ranking is diluted by the corpus's own randomly-templated filler.**
  `data/generate_corpus.py`'s "organic" clusters generate generic decision/ownership
  sentences ("Tara's next phase will be done with X") reusing the same real project
  and vendor names as the hand-authored narrative facts. These compete in
  lexical/semantic search against the specific planted fact a question is actually
  asking about, and sometimes win. `answer.py` then correctly reports it can't
  reconcile them rather than guessing — the honest failure mode, but a failure.
- **Relative time phrases resolve unreliably without corpus-specific anchoring.**
  "the very first week" and "week 2" have no meaning to the parser beyond the
  question text itself; in one run it placed "the very first week" a full year off.
  Absolute or app-context-qualified phrasing resolves correctly; bare relative
  phrasing does not.
- **`find_dictation`'s 10-result cap bounds dictation-lookup recall by construction**
  once more than 10 dictations match a filter — it now says so explicitly in the
  answer rather than silently truncating, but the missing dictations are still
  missing from what's returned.

Two bugs the eval caught were fixed during this same pass (both were shipping before
this evaluation, not introduced by it): `run_answer` was shown retrieved memory
content even under an insufficient verdict and would sometimes paraphrase it anyway
while claiming to have nothing (`api/kivi/retrieval/answer.py`); `find_dictation`
truncated silently rather than saying how many matches it was withholding
(`api/kivi/tools/find_dictation.py`). Both are now fixed and reflected in the numbers
above.

## Limitations

- **Entity-extraction completeness.** A single dictated claim can name more than
  one entity ("CloudEdge API rate limit is blocking Tara's load testing" names
  both CloudEdge and Tara), but extraction does not always tag every named
  entity mentioned in a claim — sometimes only one makes it into
  `memory_entity`. Since retrieval's entity join only expands from entities
  named *in the question being asked*, a distributed fact filed under an entity
  other than the one the question names can be missed. When this happens, the
  sufficiency gate correctly abstains rather than guessing — the failure mode
  is "Kivi says it doesn't know" rather than a confidently wrong answer — but
  it means the entity join's recall is bounded by extraction's tagging
  completeness, not just by whether the fact exists.

- **Single-hop entity join.** Retrieval resolves and joins on entities named
  directly in the question. It does not expand transitively (e.g. Tara →
  CloudEdge → Ramesh) to pull in facts about entities that only appear inside
  already-retrieved memories, not in the question itself. This is a deliberate
  scope boundary, not an oversight — multi-hop graph traversal was judged out
  of scope for this build.

- **Entity-type labels are advisory, not load-bearing.** Parse and extraction
  occasionally mislabel an entity's `entity_type` (e.g. tagging a project name
  as `"person"`). This does not corrupt resolution — `resolve_entity`'s alias
  lookup matches on the normalised name regardless of the type label passed in
  — but the type label itself should not be trusted as authoritative elsewhere.

- **Transient provider failures surface as a per-record `error` outcome**, not
  an automatic retry-on-next-run. CLAUDE.md's audit trail requirement is still
  met (the `extraction_run` row records the failure with a reason), but a
  dictation that failed due to a transient network issue stays unprocessed
  until the corpus is re-imported.
