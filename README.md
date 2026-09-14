# Kivi Semantic Memory

> This README is a work in progress. It currently covers **Limitations**, written
> while validating Phase 5 (retrieval) against the real corpus. The remaining
> required sections — product, architecture, use cases, results, AI use — are
> still pending a dedicated pass.

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
