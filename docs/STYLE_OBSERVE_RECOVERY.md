> Current live style requests use [Python-owned prose binding](PYTHON_OWNED_STYLE.md). ID-bearing contracts below are retained for historical diagnostics and compatibility tests, not as the default request interface.

# STYLE_OBSERVE validation and bounded recovery

## What the error means

`STYLE_OBSERVE returned no valid evidence-grounded observations` means that the
model returned a nonempty observations array but none of the considered items
passed the local contract. It does not establish which check failed, that a
source passage has no techniques, or that the model ran out of tokens. A
transport `done` log means a JSON response arrived, not that its observations
were accepted. With parallel workers the last completed log line need not be
the task that caused the failure.

The old observation path validated after the transport retry loop. A syntactically
valid JSON object containing only invalid observations therefore stopped the run
without using the remaining `--retries` budget. The observation path now validates
inside one bounded request/repair loop, like the separate cluster recovery path.

## Contract and behavior

- Initial request plus at most `--retries` further requests (default two retries,
  three requests total) **per chunk**. Transport, JSON and observation-validation
  failures share this budget. They do not multiply nested retry loops.
- Existing dimension, 4-110 character single-line description, verbatim-copy,
  source-ID and exact lexical-anchor checks remain in effect. Acceptance rules
  are unchanged; the prompt now states the literal dimension values and whitespace
  requirements explicitly.
- As before, at most the first seven observations are considered. Invalid items
  are filtered; valid items are kept. Partial acceptance completes the request
  without retrying rejected items, and records the rejected/ignored counts.
- A nonempty reply with zero valid observations is an error, not an empty success.
  Rejected items never enter the checkpoint.
- An empty array on the initial valid observation reply is allowed. After a domain
  validation failure in the same invocation, an empty repair is explicitly rejected
  as `empty_after_validation_failure`. Do not hide an error as "no features".
- Corrective requests contain the original passage plus fixed diagnostic codes,
  item positions, field names and counts. They do not echo the rejected response.
  Evidence checks are not weakened, IDs are not guessed, and a model is not asked
  to fabricate a feature merely to finish the run.
- Non-retryable transport failures stop immediately. Exhaustion remains a failure.
  `--timeout` and `--max-output-tokens` retain their separate existing meanings;
  an attempt budget is not a wall-clock deadline.
- Four-worker scheduling and main-thread checkpoint writes remain unchanged.
  Already running successes are drained and saved before another task's error is
  propagated. Observation request counts now include repair attempts.

## Safe diagnostics

`<out>/style_observe_diagnostics/<observation-fingerprint>.json` records the latest
invocation for each processed chunk. It has an anonymous work ID, chunk index,
segment count, maximum attempts, state and attempt records. States are `running`,
`retrying`, `waiting_for_tasks`, `completed`, `failed` or `interrupted`.

Each validation record includes `received_count`, `checked_count`, `accepted_count`,
`rejected_count`, `ignored_count`, and `issues`. An issue gives a 1-based
`item_index`, a fixed code, and sometimes a literal field name. Only the first
failing check per rejected observation is reported, not every possible defect.
For a missing/non-array observations member, `received_count` is unknown (`null`).

Codes include:

| Code | Check |
| --- | --- |
| `observations_array_required` | Top-level observations array missing or wrong type |
| `observation_object_required` | Observation is not an object |
| `unknown_dimension` | Dimension is not one of the seven literal values |
| `description_string_required` | Description field missing or not a string |
| `description_length_4_to_110` | Description length outside the existing bounds |
| `description_outer_whitespace` | Leading/trailing whitespace |
| `description_single_line_required` | Embedded newline/carriage return |
| `source_verbatim_overlap` | Existing source-copy detector rejected the field |
| `evidence_array_required` | Evidence IDs are not an array |
| `two_distinct_evidence_ids_required` | Fewer than two distinct IDs |
| `unknown_or_invalid_evidence_id` | Non-string or non-existent source ID |
| `anchors_array_max_two` | Anchor array missing its required form or too long |
| `anchor_not_exact_short_source_form` | Invalid/unobserved lexical anchor |
| `empty_after_validation_failure` | Empty repair after a domain failure |
| `model_transport_or_json_error` | Transport/API JSON failure, not a domain verdict |

No original passages, raw responses, descriptions, source filenames, model names,
endpoint URLs or credentials are recorded in these diagnostic files or error
messages. Keep them private nevertheless. Host-subagent task files are different:
they necessarily contain the task inputs and must not be published.

## Resume and cache compatibility

Update the **installed** `pastiche-profile` skill, retain the existing output
folder, and rerun the same command **without `--restart`**. Do not delete or relabel
`style_llm_checkpoint.sqlite3`, and do not manually insert a diagnostic reply.

This repair does not change `llm_style_core.VERSION`, observation cache keys,
reasoning controls, or the acceptance contract. Previously successful observations
with matching input/model/endpoint/reasoning settings are validated and reused.
Only missing or invalid cache entries need model requests. This differs from the
previous default-OFF migration, which intentionally separated reasoning modes.
Prompt clarification and recovery apply to cache misses; they do not force valid
cached observations to be regenerated. Corpus edits or a model/reasoning-setting
change can still invalidate entries under the normal rules.

With host subagents, a schema failure generates a deterministic corrective task.
Supply its JSON response and rerun the same pipeline command. Exit code 75 means
waiting, not completion. Replaying a submitted invalid response does not itself
prove a new model inference occurred; request counts include such replays.

Read the failing diagnostic and log label before changing a setting. Do not
increase output limits, re-enable reasoning, disable validation, or reduce source
coverage merely to make the run appear successful. A validated observation is
still a model interpretation, not independent semantic verification.

## Tests

Synthetic tests cover specific diagnostics, 840 combinations against the previous
acceptance contract, boundary lengths, Unicode anchors, partial/empty replies,
shared retry budgets, interruption and exhaustion, privacy, cache compatibility,
parallel-success draining, real loopback HTTP and host-subagent repair/resume.
They do not establish extraction accuracy on real fiction.

```bash
python3 -m unittest discover -s tests -p 'test_observe_recovery.py' -v
python3 -m unittest discover -s tests -v
```
