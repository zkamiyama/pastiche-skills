# Audited style refinement — v3.2

## Purpose and scope

The Write skill now has an explicit **review → local edit → revalidate → anonymized
comparison → select** stage. It improves compatibility with supported abstract
style patterns; it is **not** an independently validated author-similarity model.
No source passages, original story maps, full lexical inventory or contrast corpus
are sent to style workers. Existing current world/character evidence may still be
read locally by the freshness checks and abstract context goes to the director audit.

The original draft is never overwritten. Default: one refinement round, maximum
two. A completed operation can legitimately select the unchanged original, and
`needs_structural_revision` is a request to repair the plan/draft, not a quality pass.

## Entry points

Run from the repository root (Python 3.10+, no additional runtime packages):

```bash
python3 skills/pastiche-write/scripts/refine.py \
  --out /private/profile \
  --draft /private/scene.txt \
  --plan /private/profile/writing/story_blueprint.json \
  --context /private/profile/writer_context.json \
  --scene-id S2 --character-id A \
  --patterns /private/profile/qualitative_patterns.json \
  --style-profile /private/profile/style_profile.json \
  --lexical-profile /private/profile/lexical_profile.json \
  --api-base http://model-server.example/llm/v1 --model MODEL_ID
```

`workbench.py refine` is the identical shared implementation. The Write entry
point requires the sibling `pastiche-profile` installation; it does not duplicate
its state machine or executor. `--patterns` defaults to `OUT/qualitative_patterns.json`.
The numeric and lexical profiles are optional and explicitly selected; their
statistics are recorded but never treated as acceptance quotas. To use a subset
of more than 32 supported patterns, repeat `--pattern-id ID`; input is not silently
truncated. Supported style dimensions are lexicon, emotion, voice, dialogue,
sensory and pacing; plot structure stays in the fixed plan.

Supply a **single scene/event excerpt**, not a silently truncated manuscript.
Maximum: 16,000 decoded characters. `--character-id` provides only the selected
character's event-entry perspective to style review, editing and comparison.
World truth, other minds and later state snapshots are absent. Multi-event private
scenes require `--event-id EVENT` and a file containing that event's excerpt.
`--director` explicitly enables omniscient narration instead of a character view.
The model semantic audit receives director context regardless, but its feedback
is never fed to the private style worker. A problematic unit is returned for
structural repair instead of telling that worker the hidden answer.

The scene/event binding, neutral fact labels and information already present in
the supplied draft remain assumptions. The code cannot infer that an arbitrary
file really is exactly that event, or erase spoilers embedded in prose/labels.
No character or reader knowledge is automatically advanced by refinement.

## Diagnosis and patch contracts

`STYLE_REVIEW` must assess every selected supported pattern exactly once:
`aligned`, `underused`, `overused`, `not_applicable`, or `uncertain`. Applicable
underuse/overuse requires exact new-draft segment IDs and a concrete instruction.
Inapplicable, uncertain and aligned rows cannot request edits. A pattern not
appearing is not automatically a defect.

`STYLE_REFINE` returns only local patches with the input SHA256, cited segment,
one exact unique `before` substring, replacement `after`, and the actionable
pattern IDs supporting that particular span. Unknown/duplicate evidence,
overlapping spans, a stale input hash and unrelated edits are rejected. The
Python side applies patches in reverse offset order, not by global replace.
At most 12 patches; the edited source spans total at most 35% of the unit (a
40-character minimum budget). Final length changes at most 25% (also a 40-character
minimum). The input version and all characters outside patched spans are preserved.
These limits bound damage; they do not prove semantic equivalence inside a patch.

## Decision gates

1. If there are no supported patterns or no actionable diagnosis, keep the text.
2. Before editing, the shared `DRAFT_AUDIT` reviews the original excerpt against
   the declared plan, perspectives and current world/character constraints. Any
   reported issue returns `needs_structural_revision`; style refinement does not
   silently change a plan, observation, belief, disclosure or world rule.
3. Apply the validated local patch and run the same semantic review on the
   candidate. Any finding retains the preceding text.
4. Re-diagnose the candidate. At least one actionable pattern must become aligned
   and no other pattern's assessment may regress. Relabelling a previously
   actionable pattern as inapplicable/uncertain does not count as improvement.
5. `STYLE_COMPARE` evaluates A/B on profile fit, writing quality and meaning
   preservation. It gets neither the original/candidate labels, edit rationale,
   diagnosis nor model identity. Repeat with A/B reversed. Both orders must
   favor the candidate's style, favor it or tie on quality, and judge meaning
   preserved. A tie, uncertainty, order disagreement or quality regression keeps
   the preceding text. This conservative gate may reject useful changes.

The retained version can be a candidate accepted in an earlier round; a worse
second round does not undo an already accepted improvement. Each later comparison
is against the current retained text, with at most two rounds overall.

One unchanged diagnosis costs one model request. A fully evaluated one-round
candidate costs seven requests (including two independent requests for the pair
orderings); cache reuse and early stopping reduce this. Separate requests/agents
are **not** statistically independent, especially with the same model.

## HTTP and host-subagent execution

All stages use the existing Engine/TasksPending/checkpoint protocol:

```bash
python3 skills/pastiche-write/scripts/refine.py \
  --out /private/profile --draft /private/scene.txt \
  --plan /private/plan.json --context /private/profile/writer_context.json \
  --scene-id S2 --character-id A \
  --executor subagent --task-dir /private/tasks \
  --agent-profile style-editor-v1 --judge-agent-profile style-reviewer-v1
```

The host invokes actual subagents, submits their JSON responses using
`workbench.py tasks submit`, then reruns the same command. Pending work exits 75;
partial, validated work is cached. For HTTP, `--judge-model JUDGE_MODEL_ID` can
select a separate review model at the same endpoint/auth configuration. Without
an override, the same configuration handles all roles; this is labelled in the
result. A separate model/profile label still does not prove independent judgment.
Only `STYLE_REFINE` is routed to the editor configuration; diagnosis, semantic
checks and comparisons go to the reviewer. Stage labels and content hashes prevent
reuse across a different prompt/input. Deployment IDs/URLs are hashed for run
identity, not copied into result artifacts or logs by the refinement pipeline.

## Artifacts, freshness and resume

Each operation writes to `OUT/writing/refine/<content-bound-run-id>/`:

- `original.json`, `patterns.json`, `surface-original.json`.
- Per round: diagnosis before/after, patches, candidate, diff, original/candidate
  semantic audits, descriptive measurements, anonymized comparisons and label mapping.
- `selected.txt`, `surface-selected.json`, and `result.json` with hashes, selection,
  stop reason and the per-round acceptance record.

`OUT/writing/refine_status.json` is the latest operation's state/hash marker.
Require `state=completed`, a matching result SHA256 and selected-text hash before
using the output. Completion is not an independent quality certification.
Input files are fingerprinted once and rechecked at stage boundaries and before
publication. Current DB decisions and source provenance are revalidated, including
a revoke or source edit during inference. A stale request fails; it does not write
a new selected text. Existing prior artifacts may remain, but are not the failed
operation's successful result. Original and output paths cannot alias.

Keep task files, drafts, patches and results private: they contain the user's
new text. There is no general multi-writer transaction across DB and output files;
run only one writing/refinement operation per output directory. This extends,
not replaces, the [correctness audit](CORRECTNESS_AUDIT.md) trust boundaries.

## Verification and remaining evaluation

Synthetic tests exercise acceptance, conservative rejection, invented IDs,
patch overlap/ambiguity, stale fingerprints, human withdrawal, private/director
separation, swapped-order bias, independent routing, subagent resume and exact
preservation outside edits. The categorical acceptance rule is exhaustively
checked over 1,024 combinations of two style votes, two quality votes and two
meaning judgments. This is not a formal proof of model judgment quality.

No actual private corpus or deployment model is used in these tests. To evaluate
literary effectiveness, use held-out writing prompts with/without refinement,
blinded human judgments of profile fit and prose quality, and continuity errors.
Compare same-genre corpora before claiming distinctiveness. A separate local
original-source reuse check can be run with permission, but is not automatically
performed here. Same-model review, human meaning equivalence, author identity
and long-form global consistency are not certified by a successful run.
