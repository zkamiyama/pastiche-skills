> Current live style requests use [Python-owned prose binding](PYTHON_OWNED_STYLE.md). ID-bearing contracts below are retained for historical diagnostics and compatibility tests, not as the default request interface.

# Recovering STYLE_CLUSTER failures

## What the error establishes

`STYLE_CLUSTER invented/duplicated IDs or invalid label` was a shared message
for multiple checks. It does not establish which condition failed. A parallel
`ModelRequestError` log can describe a different in-flight request; do not infer
a particular rejected answer from an excerpt of concurrent logs.

The former pipeline retried transport/JSON parsing inside `ProgressReporter.call`
but validated the cluster partition **after** that retry boundary. Valid JSON
with an unknown/duplicated ID or an invalid label could therefore abort immediately,
even with `--retries 2`. The prompt also omitted the validator's label length
and whitespace constraints. This repair brings those checks into one bounded
request loop, without weakening membership validation.

## New behavior

- Initial request plus at most `--retries` additional requests (default: 3 total).
  Transport and cluster-schema failures share this budget, rather than multiplying
  separate retry limits. Non-retryable transport failures still stop immediately.
- Rejected cluster replies get a new, deterministic prompt containing only a
  safe error code and 1-based group/member positions, alongside the original
  allowed candidate list. Raw rejected model text is not echoed into prompts.
- Unknown IDs, duplicate membership within/across groups, singleton groups and
  invalid labels remain errors. No guessed IDs, arbitrary overlapping-group
  merge, automatic loss of candidates or silent empty-success fallback.
- Labels explicitly require 4–140 characters on one line with no outer whitespace.
- Every successful reply is checked before it is checkpointed. Empty groups is
  valid when the model reports no matching technique, not as an error fallback.
- A failed parallel task stops further scheduling once observed; already running
  tasks are drained so their valid results can be checkpointed before the failure
  propagates. Already running requests are not forcibly canceled, so an unbounded
  HTTP timeout can still delay final shutdown.

The grouping validity contract, common `VERSION` and checkpoint digests are
intentionally unchanged. Previously valid observations and partitions stay usable;
new recovery prompts apply only to cache misses. This is a retry/diagnostic fix,
not a new scientific clustering algorithm. Meaning-level errors still require
the later group review and source checks.

## Resume an interrupted installed skill

Update the installed `pastiche-profile` skill, not only an unrelated clone of
its repository. Keep the existing output directory and
`style_llm_checkpoint.sqlite3`; do not delete either and **do not use `--restart`**.
Rerun the original command with the same corpus, chunk/sampling settings, model
identity/endpoint (or subagent profile) and output path. If `--out` was relative,
use the same working directory or explicitly give its original absolute path.
Completed observations whose input fingerprints match are loaded from SQLite.
Some requests that had not been checkpointed by the old version may need repeating.

The fix does not require smaller panels. If repeated attempts still fail, add
`--cluster-batch-size 12` to the otherwise unchanged command; this reduces each
comparison window and rebuilds affected grouping panels, while preserving the
independent observation cache. Panel size can affect which groups are found;
it is a mitigation, not a quality guarantee. Changing `--llm-workers` alone does
not invalidate observation cache entries.

With host subagents, an invalid answer creates a new repair task. Fill the pending
request, submit its JSON result, and rerun. A suspension remains exit code 75;
replaying the same invalid answer emits the same deterministic repair request,
not an unbounded stream of new tasks. Exhausted invalid answers remain a failure.

## Diagnostics and privacy

Inspect `<out>/style_cluster_diagnostics/<panel-fingerprint>.json`. It records
the latest invocation's dimension, round, panel size, retry budget, attempt
outcomes, fixed error codes and indices. States include `running`, `retrying`,
`waiting_for_tasks`, `completed`, `interrupted`, and `failed`.

Codes distinguish `unknown_id`, `duplicate_within_group`,
`duplicate_across_groups`, `id_must_be_string`, `group_needs_two_ids`,
`groups_array_required`, `ids_array_required`, `group_object_required`,
`label_must_be_string`, `label_length_4_to_140`,
`label_must_be_single_line`, `label_outer_whitespace`, and
`model_transport_or_json_error`.

These files do **not** include raw replies, labels, corpus passages, prompts,
model names or endpoint strings. The filename is a content fingerprint, not an
identifier from a deployed service. A host-subagent queue is a different, private
artifact: it necessarily contains the task inputs and must not be published.
Clustering call counts include repair attempts; a subagent response replay is an
application request attempt, not evidence that another remote inference ran.

## Verification

Synthetic tests exercise exact errors and label boundaries, 625 exhaustive small
membership combinations, transport/schema retry bounds, non-retryable errors,
privacy, old checkpoint compatibility, concurrent-success preservation, local
HTTP recovery and subagent handoff/resume. No private user corpus or production
model was used; a particular model's ability to correct its output is not guaranteed.

```bash
python -m unittest discover -s tests -p 'test_cluster_recovery.py' -v
python -m unittest discover -s tests -v
```

## Subsequent reasoning-control update

The retry fix above preserved its original keys. The later default-OFF HTTP
reasoning change separately adds effective reasoning controls to cache identity.
Old keys remain available under explicit `--reasoning-effort auto`; OFF and
other explicit modes recompute affected tasks without deleting old entries.
See [Reasoning controls](REASONING_CONTROL.md) before updating an interrupted run.
