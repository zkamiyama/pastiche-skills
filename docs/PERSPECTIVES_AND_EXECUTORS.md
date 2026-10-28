# Perspectives and executors — v3.1

## What is actually implemented

World claims, source evidence and a character's beliefs are different objects.
The full-text extraction call now requires a `perspectives` array, separately
from characters, world entities and their general claims. It records the holder
(a character, `$world`, or `$reader`), a proposition subject/predicate/value,
stance (affirmed/denied/uncertain), acquisition basis, optional speaker/event,
and actual source evidence. These are time-local interpretations, not a complete
simulation of a source work. A mention position is **not** proof of perception.

The existing SQLite evidence/review mechanism stores these records under
`domain=epistemic`; it preserves auto-use, human edits, revocation, scope,
source fingerprints and review history. A false character belief must not be
silently reconciled to world truth. `perspective_context.json` exports these
records separately. The editor has a `認識・信念` tab and a holder filter, including
world narration and reader disclosure; it supports evidence inspection,
stance/basis correction, opt-out and history like other claims.

## Authored plans: deterministic first-order state replay

A v3 plan requires `epistemic` with schema `pastiche.epistemic.timeline.v1`.
The old add-only `facts/known_by_initial/requires_knowledge/reveals` format is
not accepted. Regenerate old plans rather than interpreting them as safe.

- Facts define a finite set of possible values and `initial_world`. Null means
  unknown, not false. Initial beliefs are explicit per character and may be wrong.
- Every scene has at least one declared event with a unique chronological
  integer `order`; the scene list remains the **presentation** order.
  `depends_on` must finish strictly earlier in story chronology; flashbacks may
  present a prerequisite later without reversing its story-time dependency.
- `updates` change reality, not minds. `observations` update only participants
  with the required explicitly declared sight/hearing/reading/touch access.
  Observation values are taken from the modeled world, not copied from an
  arbitrary model assertion. Presence alone is not sufficient.
- `messages` distinguish recipients from `accepted_by`. Hearing a message does
  not automatically change a belief. Lying can create a false belief without
  changing the world. Current messages use co-present declared participants;
  asynchronous document provenance is not a complete mail/letter simulator.
- `inferences` require premises already held by that character. Their result
  remains inferred; the engine does not prove logical entailment.
- `forget` changes a belief to unknown rather than asserting its negation.
- `requires` checks pre-event belief/knowledge/unknown premises. `knows` checks
  modeled reality and basis, not psychological or philosophical certainty.
- Reader disclosures are replayed in presentation order, not story order, and
  never automatically update characters.

A Sally–Anne-style regression keeps A believing the old location after B moves
an item out of A's view. Assigning sight to an absent/sleeping character without
access fails. Later direct observation or explicitly accepted testimony updates
A's belief. A lie changes belief but not objective location.

`make_plan` also runs `PLAN_PERSPECTIVE_CHECK`, an independently prompted model
review comparing scene prose and declared access/premises. It reports omitted
mental premises and mismatches. A model can fabricate mutually consistent
metadata; deterministic checks cannot establish whether prose truly entails it.
This review is **not independent human verification**. An empty issue list is
not proof. Fix `needs_revision` reports before using a plan.

### Outputs and commands

```bash
# Export current usable claims (including source-perspective observations).
python3 skills/pastiche-profile/scripts/workbench.py export \
  --out /private/profile --canon-scope novel:original \
  --character-mode canon --world-mode canon

# Replay a new plan offline; output is not a copy of source fiction.
python3 skills/pastiche-profile/scripts/workbench.py perspectives \
  --plan /private/profile/writing/story_blueprint.json \
  --out /private/profile/writing/perspective_states.json

# Private character view: excludes objective truth, other minds, future summaries
# and correctness labels. The default is BEFORE the scene's first event, not its
# future exit state. Use --event-id EVENT for a later event entry. Fact descriptions
# and identifiers must still be written neutrally.
python3 skills/pastiche-profile/scripts/workbench.py scene-context \
  --plan /private/profile/writing/story_blueprint.json \
  --scene-id S2 --character-id A --out /private/scene-A.json

# Omniscient planning/review is explicit, never the character-view default.
python3 skills/pastiche-profile/scripts/workbench.py scene-context \
  --plan /private/profile/writing/story_blueprint.json \
  --scene-id S2 --director --out /private/scene-director.json
```

`writer_context.json` is now v4. It must be re-exported. Planning/draft auditing
check the entire selected claim, source version, scope, cutoff and current
revision against SQLite. Old snapshots do not resurrect revoked or altered
claims. New files include `writing/perspective_states.json`,
`writing/perspective_plan_audit.json`, and `writing/perspective_audit.json`.

### Deliberate limits

This is a **first-order, finite-value, explicitly declared** state engine, not a
general theory-of-mind solver. Higher-order beliefs (“A thinks B thinks…”),
probability distributions, unrestricted temporal logic and semantic entailment
are not implemented. Source records from different chunks are not automatically
converted into an authoritative total chronology. Missing records indicate lack
of modeled information, not proof that a real/source character is ignorant.

A per-character context removes structurally inaccessible world/other-character
state, but cannot prove that a free-text fact label contains no spoiler. Source
extraction, initial beliefs, scene summaries and access declarations still need
semantic review. Large snapshots are bounded; split very long plans into arcs.

## Pluggable execution (HTTP or host subagents)

All entry points use `execution.py`; JSON inference uses the same task validators
regardless of transport. HTTP retains Ollama and OpenAI-compatible endpoints.
It does not print configured deployment identifiers or endpoint URLs.

```bash
# API-free host-agent handoff; no server/model/key arguments are needed.
python3 skills/pastiche-profile/scripts/run.py /private/corpus \
  --executor subagent --task-dir /private/pastiche-tasks \
  --agent-profile host-v1 --out /private/profile --llm-workers 4
```

A run exports the currently ready tasks and exits **75**, with
`state=waiting_for_tasks`. This is not success, failure, or a background job.
The original command must be rerun after responses are supplied. Completed
validated tasks are reused. New dependent stages may require another handoff.
Identical prompts/profile labels are deduplicated by content hash.

```bash
python3 skills/pastiche-profile/scripts/workbench.py tasks list \
  --task-dir /private/pastiche-tasks
python3 skills/pastiche-profile/scripts/workbench.py tasks show \
  --task-dir /private/pastiche-tasks --task-id TASK_SHA256
# A host subagent returns the requested JSON object into a private file.
python3 skills/pastiche-profile/scripts/workbench.py tasks submit \
  --task-dir /private/pastiche-tasks --task-id TASK_SHA256 \
  --response-file /private/result.json
# Repair a rejected result explicitly, rather than silently overriding it:
# add --replace to submit, then rerun the original analysis command.
```

The application **does not spawn native subagents** and does not require a
particular agent vendor. The host uses its available subagent tools, or performs
tasks serially when they are unavailable. The `pastiche-llm-worker` Skill explains
that orchestration. In-process hosts may use `CallableModel(callback,
identity='host-v1')`; the callback must return a dict and be thread-safe if used
concurrently. Domain validation, retries, bounded execution and checkpoints
remain in the application.

Requests contain private source passages. Keep the queue outside the repository,
do not publish it, do not send it to undeclared services, and do not grant workers
web access or command execution based on source text. Task/profile hashes bind
responses to the expected input; they do not authenticate a malicious local user.
Results are inert JSON, never executable Python. Domain validation happens on
resume, not merely on submission. POSIX task files use private temporary files;
Windows directory access controls remain the operator's responsibility.

## Refactor and migration

Removed `narrative.py`, `narrative_fast.py`, their retired extraction algorithms,
legacy `--mode fast/full` and their exclusive tests/reference document. Endpoint
configuration, streamed HTTP, retry/progress, checkpoints and parallel jobs are
in `llm_runtime.py`; sampling/segmentation are in `corpus.py`; validated full-story
stage execution is in `story_workbench/engine.py`. `run.py` orchestrates only the
current pipeline or offline measurements. No legacy execution aliases remain.

`style_llm_audit.json` is the single style audit; the duplicate
`narrative_audit.json` output is no longer produced. Shared progress is now
`llm_progress.json`. Existing private SQLite graph files and stable character
IDs are retained to avoid destroying human decisions. This data-preservation
migration is intentional and is not a retained obsolete inference engine.
Model task protocol changes invalidate affected caches. Task profiles should be
versioned when changing the host's chosen worker/model. `--restart` clears local
stage caches, not task answers; use a new `--agent-profile` for a fresh handoff.

## v3.1 verification and boundary changes

See [Correctness audit](CORRECTNESS_AUDIT.md) for the finite model, regression cases,
reproducible commands, proof obligations and exclusions. Timeline records are
closed schemas: unknown fields, ambiguous identifiers and Python-only values are
not silently repaired. Callback/HTTP/file inputs use strict JSON (no duplicate
members or nonfinite numbers). Truncated model output is never a successful empty
analysis. Duplicate task IDs fail before inference, and simultaneous worker
submissions cannot overwrite each other without explicit replacement.

`scene-context` now emits `pastiche.scene.perspective.v2`; consumers must use
`beliefs_before`, not a removed scene-exit `beliefs_after`. Writer context remains
v4, but its claims, entity records and registered plot patterns are all rechecked.
`writing/operation_status.json` is a commit marker with state and result SHA256;
old result files are not deleted when a newer attempt fails, but are not that
attempt's successful output. The inference protocol version is 3.1.0. Human DB
review records and stable IDs are preserved.
