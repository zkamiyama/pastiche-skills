# Python-owned references; prose-first style analysis

## Scope

The default style/lexical path now uses `python-owned-style.v1`. This includes
lexical impressions, new observations, clustering, group review, source checking,
and writing-use notes. The active path does **not** ask the model to copy work,
segment, observation, group or pattern IDs, including short alias IDs. The final
JSON/Markdown artifacts retain machine IDs assigned and validated by Python.

This is not a claim that every subsystem in the repository is unstructured.
Full-story/character/world extraction, epistemic plans and the separate structured
workbench/refinement contracts remain unchanged. They still use their established
reference contracts. A `run.py --skip-story` invocation uses the new style path;
a normal full invocation also runs the unchanged story path. The write skill can
consume the unchanged style/pattern artifacts. `qualitative_summary.json` now uses
`pastiche.qualitative.summary.v2`: one note per pattern, so dimensions may repeat.
The generated Markdown guide embeds these notes; readers must not assume one
summary record per dimension.

## Division of responsibility

| Stage | Model returns | Python owns |
| --- | --- | --- |
| Lexical impression | One form's short explanation and category judgments | Input form and lexical ID; no positional output-list alignment |
| Observation | Dimension, trigger, technique, effect, a few short exact evidence quotations | Exact quote-to-segment matching; source/observation IDs and final provenance |
| Clustering | Compatibility of one candidate with one fixed group, a common mechanism and explanation | Candidate scheduling, shortlist, membership, IDs, disjointness and retention |
| Group review | One observation's compatibility and explanation | Which observation was reviewed and accepted |
| Source check | Support verdict, relation basis and explanation | Selected source passages and their references |
| Writing note | Summary and application prose for one supported pattern | Exactly that input pattern's reference and dimension |

JSON is retained as a small envelope for bounded free-text fields, booleans and
necessary category judgments. Arbitrary document formats, model-assigned indices,
parallel output arrays and fuzzy repairs of identifiers are **not** substitutes
for an explicit Python task binding. Semantics still depend on model judgments;
Python cannot prove that an interpretation or literary effect is true.

## Evidence binding

New observations receive an unnumbered list of source passages. The model returns
two to four exact substrings of 4–80 characters. Python requires each substring
to occur in exactly one supplied passage and the set to cover at least two
DIFFERENT passages. It rejects absent, ambiguous, normalized, or paraphrased
quotes; it never guesses a location. Repetition inside a single passage does not
change its segment identity; repetition in different passages is ambiguous.

The existing dimension, abstract-description, verbatim-copy and short lexical
anchor checks run after binding. Partially valid observations survive; a nonempty
all-invalid reply fails. Bounded corrective retries use fixed codes only. Empty
repair replies after validation failure are not fabricated success. Exact quotes
are transient and are discarded before observation checkpointing or export.

Source checks have one observation and its selected passages bound BEFORE the
request. They cannot switch to different evidence by returning other IDs. The
model still has to support at least two selected passages, not merely a plausible
topic in the context. Its free explanation is validated then discarded because it
may contain source wording. Only the verdict and basis enter the private task cache.

## Clustering method and cost

Python processes each bounded panel in a stable order. For each next node it
ranks existing groups using character-bigram overlap of abstract descriptions,
then compares at most three candidate groups. This similarity is ONLY a shortlist
heuristic, never evidence of semantic identity. Each model request compares ONE
candidate against ALL currently listed members of ONE group. Uncertainty stays
separate. An accepted node is attached by Python; no graph transitivity or
ID-string correction is used. Every input node and original member is checked
for loss, overlap and duplication; unmerged nodes remain singletons.

For a panel of n nodes there are at most `3 * (n - 1)` comparisons, each with at
most `1 + --retries` requests. Existing interleaved grouping rounds still operate.
This changes the clustering algorithm and may change groupings. It is not an
exhaustive pairwise method: shortlisting and greedy order can miss synonymous
techniques, especially where descriptions use different vocabulary. More calls
and formal success do not establish better literary similarity.

Panels use the existing `--llm-workers` ceiling. Inner comparisons are sequential;
there is no nested worker pool multiplying concurrency. Different model requests
remain separate even if their completions arrive out of order. Group review and
source checks still precede promotion to `model_supported`.

## Checkpoints, diagnostics and migration

Observation cache keys are unchanged. Existing validated observations can be
reused after being checked against their actual source segments; they are **not**
relabelled as newly quote-matched or regenerated. Existing valid lexical batches
are likewise reusable. Corpus and human review databases are not cleared.

Clustering and downstream stage keys have a NEW interface namespace because the
method and request scopes changed. Old successful clusters are retained on disk
but are not silently presented as products of this new algorithm. The first run
rebuilds clustering and its dependent reviews/notes; it does not need to redo
matching cached observations. Do not use `--restart` for this upgrade.

`style_binding_cache/` stores atomically written, validated per-request abstract
verdicts/notes, indexed by the full Python binding, contract, backend/model,
effective reasoning controls and output limit. A failed panel can reuse already
completed comparisons on resume. SQLite writes still belong to the main thread.
`--restart` bypasses this task cache as well as resetting stage caches; it does
not erase corpus or human reviews. Host subagent answers use the existing separate
handoff mechanism, so changing the host/model still requires an agent-profile
version as before.

`style_binding_diagnostics/` contains fixed error codes and states, not rejected
responses. Panel diagnostics remain in `style_cluster_diagnostics/` with schema
`pastiche.cluster.python-owned.v1`, comparison/request counts and declared bounds.
Observation diagnostics retain their location, with contract `observe-prose.v1`.
Audit artifacts declare `model_interface` and Python reference ownership.

The old ID-bearing prompt helpers and repair modules are retained solely for
legacy checkpoint/diagnostic inspection and regression tests. New live style
requests route through `prose_contracts`, `prose_observe_recovery`, `python_cluster`
and `prose_runtime`; they never fall back to asking the model for IDs.

## Validation

Synthetic tests cover exact/ambiguous Unicode evidence, rejected model-owned IDs,
source-text-free logs/caches, bounded shared retries, per-binding resumption,
no-transitive-union behavior, complete member retention, out-of-order completion,
host handoff, native HTTP and preserved OFF reasoning controls. Existing full
pipeline tests exercise the new interface as well as legacy artifact readers.
These are contract/invariant checks, not a human validation of prose interpretation.
