# Full-story and character workbench (v2)

This is an original engineering implementation of full-text extraction,
hierarchical compression, cross-work pattern proposals and provenance-based
human review. It is **not** a reproduction of Storyline Trees, MAGIC, SCION, or
any claimed benchmark result. Its tests use synthetic text, not the user's books.

## Two complementary analysis paths

The default `run.py --mode llm` now runs both paths:

1. **Style**: existing whole-corpus deterministic measurements plus sampled,
   context-sensitive LLM observations of voice, lexicon and emotion.
2. **Whole story and characters**: every eligible selected work is segmented and
   **all** its chunks are submitted to the configured LLM. Nothing is skipped
   based on `--chunks-per-work`. `--analysis-works 0` includes all eligible works;
   an explicit positive work cap applies to both paths.

`--skip-story` retains the previous style-only workflow. The old `fast`, `full`
(legacy local-device extractor), and `none` modes are unchanged. In particular,
legacy `--mode full` is not the new hierarchical workbench.

The default full-story work proceeds as follows:

- Stable source selectors use offsets in newline-normalized **Unicode code
  points**, a source hash and original segment IDs. Source text is read from
  registered files on demand, not copied into claim rows.
- All chunks undergo bounded parallel extraction of character mentions, concise
  events, state-before/state-after, local causal/motivational links and
  evidence-backed character claims. Empty records are permitted for real absence;
  invalid IDs, lost records or invalid JSON are errors, not silent absences.
- Events are arranged in presentation order and recursively grouped into bounded
  hierarchical phases. Every child is retained exactly once; the complete leaf
  records remain addressable. The whole-work overview consumes all top-level
  phases. The method does not force a three-act or fixed-genre template.
- `story_time` is a separate, nullable field from `position` (presentation order).
  Unknown chronology is never filled from file order. Cross-phase links are
  explicitly `model_inferred`; a link's existence is not human validation.
- Corpus pattern proposals compare **ordered state-transition functions**, not
  vocabulary. Bounded, interleaved LLM panels merge candidate structures, followed
  by per-work alignment and source-window spot checks. `model_supported` requires
  at least two supporting works. Nonmembers are not exhaustively tested, so
  `support_count/corpus_work_count` is **not** a prevalence estimate.
- Completion requires all selected source segments to have been submitted and
  their source hashes to still match. This measures textual coverage, **not**
  perfect comprehension. Summaries and bounded extraction can omit important
  details or long-range connections. No real-model speed/fidelity claim is made.

## Character knowledge is a collection of claims

The SQLite schema contains `sources`, `evidence`, `entities`, `claims`, `reviews`,
`merges`, `merge_history`, `tasks`, `artifacts` and `layout`. It uses no graph DB,
embedding runtime, torch, Node.js or CDN at execution time.

Each claim has subject, predicate, object (or an attribute value), type, canon
scope, evidence IDs, epistemic label and optional time interval. Types include
appearance, conditional voice/register, values, goals, beliefs, knowledge and
directional relationships. `reported`, `explicit` and `inferred` must not be
conflated: a spoken assertion need not be true or believed.

Characters with the same name are **not** automatically merged across corpus
files. Within a work, exact canonical names are matched; uncertain pronouns stay
passage-local. In Web research, exact names are scoped to the explicitly supplied
universe and canon. A human can merge/unmerge identities with a recorded reason.
Different canon scopes cannot be merged. Original claims are never destroyed.

`observed_at` means the latest presentation position of supporting evidence,
not the beginning of a character trait's lifetime. Unknown duration is stored
as null, not `[0,1]`. A reviewer may set a justified validity interval. At a
restricted cutoff, claims with unknown observation time are excluded. This is a
conservative observation filter, not a model of all flashback/spoiler semantics.

## Review workflow and security

```bash
python3 skills/pastiche-profile/scripts/workbench.py serve \
  --out /path/to/.pastiche-private/profile
```

The command prints a per-session URL with a fragment token. Open it locally and
keep it private. Only `127.0.0.1` is bound; there is no public/listen-all switch.
API access requires the token, allowed Host and (when present) same-origin Origin.
POST accepts bounded JSON; source access is by registered evidence ID, not an
arbitrary filesystem path. A restrictive Content Security Policy and text-only
DOM rendering prevent raw source/model content from becoming executable HTML.
This is a single-user local app, not a hardened multi-user hosting service.

The three-pane SVG editor provides a claim queue, draggable graph with pan/zoom,
source highlighting, canon/position/status filters, attribute nodes, approval,
correction, rejection, deferral, history, manual evidence-backed claims, identity
merge/unmerge, saved layouts and export. The story tab shows work trajectories,
phases, events and their source evidence. Story structure review is currently
read-oriented; it is not a free-form graphical plot-authoring canvas.

- New source-grounded character claims start `auto_accepted` and can be used immediately by a writer. This is **not** human verification or established canon. Existing untouched `pending` claims migrate to `auto_accepted`; historical human decisions are preserved.
- `approve` marks a claim `human_approved` and checks that evidence source hashes still match. `revoke` marks a claim `revoked` and removes it from writing context, without deleting the original; `reject` records a false interpretation, and `defer` temporarily withholds it.
- `correct` creates a human-authored override and immediately marks it `human_approved` after checking current evidence. No second approval step is required.
- Reviews require the expected revision; stale tabs receive HTTP 409 instead of
  overwriting another edit. Every review keeps before/after versions and a note.
- Re-extraction does not erase human overrides, rejection or approval for an
  unchanged claim. Changed evidence is a new claim; old evidence is marked stale.
- `--restart` clears inference task caches, **never** human decisions.
- Export includes current `auto_accepted` and `human_approved` claims in the selected
  scope/cutoff, with their separate `status` and `epistemic` fields. `revoked`, `rejected`,
  `needs_review` and stale evidence are excluded. The selected model must treat
  `auto_accepted` as provisional, not as independently verified canon.
  Source changes exclude affected claims even if their historical approval is visible. Work structure patterns remain separately labelled
  model-supported, never human-approved.

The UI displays at most 160 character nodes and the first 500 matching queue
items, with an explicit notice. Search and filters expose the remaining records.
The database retains all records. State-changing or profile-generating operations
are never sent to the public Internet by the editor.

## Source-linked Web research is separate

Install `character-research` alongside `pastiche-profile`. The agent first uses
its available Web search tool to resolve character/universe/canon and follows
source links, references and creators in rounds. `map.md`, search results and
unresolved questions remain separate from the LLM's interpretation. Official
pages, creator statements and fan interpretations must not be collapsed.

The bundled helper is a bounded crawler/extractor, **not a search engine**:

```bash
python3 skills/character-research/scripts/research.py \
  --out /path/to/.pastiche-private/profile \
  --character "Character name" --universe "Work title" \
  --canon-scope "work:original" \
  --seed "https://official.example/characters/a" --domain official.example \
  --source-type official --max-pages 8 --depth 1 \
  --api-base http://model-server.example/llm/v1 --model el-local
```

The operator/agent must verify the `source-type`; it is not an automatically
established authority rating. Run separately for different source classes.
HTTPS is required by default. Public HTML/plain text only; no browser automation,
logins, cookie reuse, paywall bypass or PDF/JS challenge handling is attempted.
An explicit exact-domain allowlist and request/depth/link budgets are required.
Private/loopback/reserved destinations, credentials and nonstandard ports are
rejected. Connections pin a previously validated public IP while preserving
TLS hostname verification. Redirect destinations are revalidated and subject to
their own robots policy. The crawler does not use environment proxies.

robots.txt is checked conservatively: 404 permits, successful policy is parsed,
and unavailable/error/redirected policies cause acquisition to stop for that
page. This is intentionally restrictive, not a claim of complete RFC conformance.
A robots allowance is **not** authorization or permission to redistribute the
source. Respect terms, copyright and rate limits independently. Sources and
fetch logs remain private. The same configured model receives their selected
text; external-network use is explicit, separate from corpus analysis.

## Changes to writing

The write Skill now selects an abstract **whole-story pattern**, builds a
hierarchical plot and scene contract, then writes scenes while updating separate
character knowledge and reader knowledge. Original characters remain the default;
canon mode is only used when explicitly requested with a canon/time boundary.

```bash
python3 skills/pastiche-profile/scripts/workbench.py export \
  --out /path/to/.pastiche-private/profile --canon-scope work:original
python3 skills/pastiche-profile/scripts/workbench.py plan \
  --out /path/to/.pastiche-private/profile \
  --context /path/to/.pastiche-private/profile/writer_context.json \
  --brief /path/to/new-story-brief.txt \
  --api-base http://model-server.example/llm/v1 --model el-local
```

`audit-plan` checks pattern IDs, allowed character references, scene/tree coverage,
cycles, earlier-scene prerequisites and declared character knowledge. It cannot
prove motivation or literary quality. `audit-draft` is an additional LLM review
that must cite actual new-draft passages, with an explicit length limit rather
than silently truncating a long draft. An empty issue list is not proof of quality.
Neither routine changes human approvals. Existing deterministic lexical audits
remain available. Full prose is produced by the write Skill's host LLM; the plan
helper is not an autonomous book generator.

## Main outputs

- `story_maps.json` / `story_maps.jsonl`: all selected work trees, events, state
  changes and links; these contain private plot summaries.
- `plot_patterns.json` / `narrative_bible.md`: grounded abstract structural motifs.
- `story_audit.json` / `story_progress.json`: coverage, source snapshot and attempts.
- `character_graph.sqlite3` / `character_graph.json`: private claims and review state.
- `writer_context.json` / `character_bible.md`: auto-accepted and human-corrected character context (statuses preserved).
- `writing/story_blueprint.json`, `writing/plan_audit.json`, `writing/plot_audit.json`.
- `research/sources/`, `research/map.md`, `research/fetch-log.jsonl`, `research/audit.json`.

Use the SQLite database, not an old JSON export, for review decisions. Back up the
private output directory before migrations. Source paths are local registrations;
when moving a corpus, re-register/re-analyze it rather than trusting stale paths.

## References for data handling (not reproduction claims)

- Web Annotation Data Model: https://www.w3.org/TR/annotation-model/
- PROV-O: https://www.w3.org/TR/prov-o/
- Robots Exclusion Protocol: https://www.rfc-editor.org/rfc/rfc9309.html
- imozuru investigation workflow: https://github.com/MaryCache/imozuru/tree/main/skills/imozuru

Evaluation remains necessary on real models and representative Japanese fiction.
Synthetic tests check invariants, not literary extraction accuracy.
