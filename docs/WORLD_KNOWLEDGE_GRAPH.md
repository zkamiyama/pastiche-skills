# World Knowledge Graph — v2.1 implementation

## What changed

The full-text story extraction path now also emits **typed world entities and evidence-linked world claims**. This is not a detached setting summary: characters may belong to organizations, events may refer to places/systems, and conditional world rules can constrain a writing plan. The numeric style path remains separate.

No additional runtime package is required. Python 3.10+, SQLite and bundled JavaScript/SVG are used. This is an independent implementation, not a reproduction of a named research paper or a benchmark claim.

## Entity and assertion model

Types: `character`, `place`, `organization`, `polity`, `species`, `artifact`, `concept`, `system`, `culture`, `era`.

World assertion kinds: `geography`, `affiliation`, `history`, `institution`, `law`, `culture`, `technology`, `magic`, `ecology`, `resource`, `cosmology`, `world_relation`.

A world claim contains registered subject/object IDs, `scope`, `evidence_ids`, `epistemic` (explicit/reported/inferred), `modality` (descriptive/normative/capability/belief/unknown), `polarity`, `conditions`, `exceptions`, optional textual `story_time`, source observation position and optional human-specified validity interval. A prohibition is not a proof of physical impossibility. A person's belief is not an unconditional setting fact. Empty conditions mean not observed, not universally applicable.

Names/aliases must appear in the cited segment. Unknown IDs, duplicate local IDs, unsupported types, missing world output arrays, cross-scope claims and long verbatim output are rejected. World arrays may be empty, but missing arrays fail: parser failure is not silently labelled absence.

World/entity names are not automatically unified across source files. Uncertain mentions are passage-local. Manual merge requires equal entity type and canon scope and can be undone. These conservative identity rules can leave duplicates; the system does not claim perfect entity resolution.

## Full-text integration

The existing `STORY_EXTRACT` call extracts characters, events and world assertions together. It adds no separate full-text world pass. Every selected chunk is submitted; this means coverage, **not** perfect recall or comprehension. The increased output size may require a larger model output limit.

Each event keeps `world_entities` with canonical database identifiers. Story hierarchy retains event leaves; the editor can show events referring to a world entity. Input evidence offsets still refer to the original registered source, whose hash is checked before use.

Updating from 2.0 changes the inference protocol/version and therefore reruns the changed inference stages. Existing character IDs and unchanged claim IDs remain stable; human overrides are not cleared by `--restart`. No destructive database migration is required. Older entity records without a type are characters.

## Files

- `character_graph.sqlite3`: shared private database (name retained for compatibility).
- `character_graph.json`: combined typed graph snapshot, schema `pastiche.knowledge.graph.v2`.
- `world_graph.json`: world entities, cross-domain relations, source evidence IDs and all review statuses.
- `world_context.json`: usable, current world claims for a selected scope/cutoff.
- `world_bible.md`: readable version including conditions, exceptions, provenance and conflict candidates.
- `world_audit.json`: extraction coverage/state/counts. A failure must not masquerade as an empty successful graph.
- `writer_context.json`: schema v3, includes characters, world knowledge and independent character/world modes.
- `writing/world_audit.json`: model draft-review findings restricted to world issues, citing actual draft evidence and actual world claim IDs.

The SQLite DB is authoritative for human decisions. JSON/Markdown files are snapshots and must be re-exported after edits. Unchanged snapshots do not silently restore withdrawn claims: plan generation and draft audit validate their eligibility/revision against the DB before calling the model.

## Review policy

Source-grounded claims are `auto_accepted` by default. This is operational eligibility, not verified truth. Human `correct` is immediately applied and labelled `human_approved`; `revoke`, `reject` and `defer` exclude claims without destroying evidence or history. Old or missing sources, incompatible canon and out-of-cutoff knowledge are excluded even when previously approved.

`world_context` and `writer_context` are separated: world assertions never enter the character-attribute array. Cross-domain affiliations remain in world context, with both endpoint records.

## Conflicts: intentionally bounded

The deterministic check reports **opposite polarities for the same exact subject/predicate/object** and compatible scope, modality, conditions, exceptions and explicit era. Non-overlapping known intervals are excluded. Unknown durations are labelled unknown, not proven overlap. At most 100 candidates are returned; truncation is explicit.

It does not detect all semantic contradictions, aliases or paraphrases. Multiple capital cities or different descriptions are not automatically declared inconsistent. Conflicts remain separate supported-source claims rather than being overwritten or arbitrarily resolved. The writer receives candidate IDs and a caution, not an invented canonical resolution.

## Editor

`世界観` switches the existing three-pane canvas to typed world claims. Type and canon filters, conditions/exception fields, source highlights, human edits, revoke/restore, merge/unmerge and linked event inspection are available. The UI shows a warning for candidate conflicts; it is not a truth checker. SVG node types differ visually; no runtime CDN is used.

`/api/world` is a token-protected view of the same DB. The security model remains loopback-only with exact Host/Origin checks, bounded JSON, source allowlisting and strict CSP. No external uploads are triggered by opening the editor.

## Commands

```bash
# Existing entry point: full story/world path is enabled in default llm mode.
python3 skills/pastiche-profile/scripts/run.py /path/to/corpus \
  --api-base http://127.0.0.1:11434 --model MODEL_ID \
  --out /private/profile --canon-scope novel:original --llm-workers 2

# Inspect and edit.
python3 skills/pastiche-profile/scripts/workbench.py serve --out /private/profile

# Refresh world snapshots after a decision.
python3 skills/pastiche-profile/scripts/workbench.py world-export \
  --out /private/profile --canon-scope novel:original

# Original characters within an explicitly selected existing setting.
python3 skills/pastiche-profile/scripts/workbench.py export \
  --out /private/profile --canon-scope novel:original \
  --character-mode original --world-mode canon

# New characters in a new setting (default); no copying source entity IDs.
python3 skills/pastiche-profile/scripts/workbench.py export \
  --out /private/profile --canon-scope novel:original --world-mode original

# Safe synthetic data, no network or LLM.
python3 skills/pastiche-profile/scripts/workbench.py demo --out /private/new-demo
```

Deployment-specific model names belong in local arguments/configuration only. `MODEL_ID` is a placeholder, not an actual model ID.

## Writing contracts

Character mode and world mode are independent. Original-world mode requires empty source-world entity/claim references. Canon-world mode accepts only current world IDs. The plan explicitly lists `world_claim_ids`; scene-level claim IDs must belong to that selection. `world_note` explains application of conditions and exceptions. This is a declaration, not a logical proof that the fiction follows every rule.

A model draft audit examines rule conditions, exceptions, chronology, geography and affiliations, and must cite an allowed world claim and a supplied new-draft segment. Offline checks validate identifiers and declared dependencies; they cannot certify semantic or literary correctness.

## Optional Web research

`world-research` is a separate skill using host-agent search plus the existing bounded public-page crawler. It takes a topic, universe, canon, seed URLs and allowlisted domains. Official material, creator statements, licensed sources and fan sources remain distinct. The helper is not an autonomous search engine; no new site crawling was used to test this implementation.

## Tests and limits

Synthetic tests cover schema rejection, full-path world output, resume, original/canon plan references, conditions/exceptions, type/scope-safe identity links, stale source exclusion, opt-out edits, snapshot revocation, conflict scope/era handling, bounded Web fixtures and local HTTP endpoints.

`tests/browser_world_smoke.py` tests the browser flow over real loopback HTTP in ordinary CI. Sandboxes forbidding browser networking may set `PASTICHE_COMPONENT_ADAPTER=1`: this is a DOM-only test using mocked fetch backed by a Python local HTTP client. It does not test browser transport or CSP and must not be reported as full browser end-to-end validation.

Human review on a real corpus is still necessary. Sampling-free input does not imply exhaustive setting extraction. Rules are bounded per chunk; long-distance historical consistency, changing jurisdictions, reliable narrator identification and cross-work entity alignment remain imperfect.
