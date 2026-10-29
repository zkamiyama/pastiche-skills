# Current interface: Python-owned references

The current live style and lexical path uses prose-first task binding; see [Python-owned style](../../../docs/PYTHON_OWNED_STYLE.md). Observations use exact temporary quote matching, clustering is a bounded Python-owned comparison process, and review/summary tasks are individually scoped. Old valid observations remain reusable; clustering and dependent stages use new keys. The historical algorithm below documents legacy cache provenance, NOT the current wire protocol.

# LLM-first Style Induction — method and limits

This describes the current `run.py --mode llm` implementation (`llm_style.py` + `llm_style_core.py`). Retired string-similarity/full-ontology execution paths have been removed in v3.

## Objective and scope

The system attempts to identify reusable writing **operations**, not imitable stories. The goal is to offer separately:
- deterministic measurements from the full corpus, without semantic claims;
- short-context LLM observations covering lexicon, emotion, voice, dialogue, sensory detail, pacing and narrative mechanics;
- repeated, source-screened hypotheses for use on new, unrelated story premises.

The output must not be interpreted as proof of author identity, unique authorial signatures, actual population-frequency differences, or human-validated psychology.

## Phase A: Sampling and observations

`corpus.enumerated_corpus` assigns anonymous work IDs. `corpus.chunk_segments` creates bounded chunks with segment IDs. `corpus.stratified_indices` distributes sampling over the *sorted* eligible works; `select_chunks` samples different portions of each selected work.

- Default: **all eligible works × up to 2 passages per work** (`--analysis-works 0`). For example, 91 eligible works yield up to 182 scene requests, rather than analyzing all 630 source chunks. This intentionally samples *every work* but not *every passage*.
- To reduce LLM requests, specify a positive `--analysis-works N` limit. This caps the number of works sampled, not the number of passages in each selected work.
- Every request sends only the selected passage plus a constrained, schema-specific analysis prompt to the configured model endpoint.
- Prompt returns at most seven records with `dimension`, `trigger`, `technique`, `effect`, at least two actual `evidence_ids`, and zero to two optional short `anchors` only for lexicon.
- Python rejects unsupported dimensions, fabricated IDs, prose with long source-matching fragments, malformed JSON, ungrounded lexical anchors and nonsensical field lengths.
- Cache key combines model identity, endpoint, prompt/code version and **hash of full input chunk**. Only accepted abstract observations go to SQLite; the input text is not stored in the checkpoint.
- The output `qualitative_observations.json` includes only anonymized IDs, abstract descriptions and short matched anchor strings. It never includes passages or paths.

Observation transport and validation now share a bounded corrective retry budget
(`1 + --retries` per chunk). Fixed rejection codes and counts are stored under
`style_observe_diagnostics/`, without raw replies or source text. The first-seven
filter and partial-acceptance contract are unchanged. An initially empty array
is allowed; an empty repair after validation failure in that invocation is not.
Existing valid observation cache keys remain compatible with this recovery change;
clarified prompts apply only to cache misses. See
[observation recovery](../../../docs/STYLE_OBSERVE_RECOVERY.md).

This is *qualitative sampling*; drawing no observations from a scene is not evidence of the absence of a technique in the full work.

## Phase B: Meaning-based candidate grouping

Unlike legacy `SequenceMatcher`, grouping is decided by the same configured LLM. It compares the **technique (operation)** first, and the triggering context and effect second. Mere word, theme or emotion similarity is insufficient.

- Group independently by `dimension`; this is a conservative boundary, not a claim that cross-dimensional relations do not exist.
- Compare small deterministic panels of up to `--cluster-batch-size` observations (default 30).
- A second round interleaves prior panels to expose cross-panel matches. `--cluster-rounds` caps this iterative process (default 3).
- LLM returns groups with known candidate IDs and a short abstract group label; omitted IDs remain singletons. Python rejects fabricated IDs, duplicate memberships and malformed labels.
- The algorithm is approximate and panels may miss a transitive or cross-panel match. Do not treat an empty grouping as a measured absence.

No embeddings or extra ML runtime are required.

## Phase C: Group validity and source checks

Groups are ranked by the number of **distinct sampled work IDs**, then number of observations. Candidate groups need at least `--min-works` (default two) independently identified works.

1. **Group review**: a new LLM prompt checks a compact, diverse sample of members; it may exclude outliers or reject the entire group. A compatible subset must still include more than one item in distinct works.
2. **Source check**: for each reviewed group, re-open up to `--verify-works` (default four) original sampled chunks, one observation from each different work. The model must confirm the exact prior evidence IDs and the claimed mechanism; for narrative mechanisms, merely chronological links are not accepted.
3. **Promotion**: only groups with confirmed evidence from at least `--min-works` distinct works become `model_supported`. Others remain `hypothesis` and are **not included as writing instructions**.

All cache writes and final aggregation occur on the main thread, even when LLM HTTP requests run concurrently in worker threads. Worker model clients are isolated to prevent stream state races. The code may use cached check results when the model/version/prompt/scene hash matches.

Both group audit and source check use the **same chosen model**, even though they are separate prompts. This is NOT independent verification by a human and can reproduce the model's biases. Evidence IDs merely reference positions and cannot prove the interpretation is correct.

## Phase D: Grounded cross-work summary

When any pattern is source-screened, one more LLM call summarizes only **supported abstract patterns**, never the original text. It returns at most one section per dimension, including an actionable writing-use description and exact `pattern_ids`. Python validates that each citation exists, belongs to the same dimension and is supported; hallucinated or mismatched IDs cause failure rather than becoming ungrounded style advice. The validated result is stored in `qualitative_summary.json` and appears in `qualitative_bible.md` above the detailed pattern list. If no pattern is supported, no summary request is sent.

## Outputs and validation

| File | Meaning |
| --- | --- |
| `qualitative_observations.json` | Every accepted local observation, no long source |
| `qualitative_patterns.json` | Grouped hypotheses and model-supported patterns, sample coverage and work-level evidence |
| `qualitative_bible.md` | Optional reusable qualitative methods from `model_supported` only |
| `qualitative_summary.json` | LLM-authored, cross-work guidance citing supported pattern IDs |
| `narrative_rules.json` | Model-supported narrative patterns + separated hypotheses (`pastiche.narrative.v2`) |
| `style_llm_audit.json` | Stage-specific calls/cache hits, number of groups and source-confirmed patterns |
| `llm_progress.json` | `running` / `failed` / `interrupted` / `completed` |
| `style_llm_checkpoint.sqlite3` | Resume state; does not contain passages |
| `style_bible.md` | Deterministic measurements + lexical preference + evidence-screened qualitative and narrative advice |

`sample_coverage` uses the sampled works as denominator. `corpus_observed_lower_bound` is an observed lower bound relative to all eligible works, NOT a population frequency or evidence that unobserved works lack the device.

`--restart` resets the model stage's checkpoint; without it, a repeated identical run reuses successful stage outputs. Changing grouping parameters invalidates affected grouping panels while preserving the independently fingerprinted source observations.

## Known limitations and future evaluation

- Observations are short-context: 3,800-character chunks cannot reliably establish multi-chapter foreshadowing or full story shape.
- The sampler is stratified by file order, not by genre, author, document length or thematic variation; do not infer sampling representativeness from number of files.
- The LLM may over-merge general genre conventions or split synonymous techniques, especially when group counts exceed one context window.
- An independently collected same-genre comparison corpus would be necessary to make relative-distinctiveness claims; a model's generic language intuition cannot substitute for that comparison.
- Source-screened hypotheses are not empirical evidence that they cause real reader emotions. The proposed effect is a model prediction.
- Better authorial imitation is **not** guaranteed by producing more observations. Evaluate guide variants on withheld prompts: statistics-only vs statistics+qualitative vs full model-supported narrative. Use blind human judgments, distribution metrics and human originality review.
- The corpus is untrusted data, never executable instruction. Never commit local source, full inventory, private artifacts or user text to a public repository.

Regression coverage: synthetic models simulate successful extraction, empty group output, fabricated IDs, source rejection, resume, configuration-sensitive invalidation, parallel requests, and absence of raw text in exports. Run `python3 -m unittest discover -s tests -v`.
