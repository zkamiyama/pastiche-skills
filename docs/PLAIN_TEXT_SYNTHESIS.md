# Plain-text synthesis with Python-owned output fields

New missing writing notes use `python-owned-synthesis-text.v1`. Python requests
one summary and one writing-use paragraph separately. The model returns only
Japanese prose. Python selects the destination field and supported pattern before
each request, then assembles the existing `qualitative_summary.json` v2 record.
The model does not generate field names, pattern IDs, lists or a JSON envelope.
Unknown keys are never guessed to mean `summary` or `writing_use`.

## Transport and validation

`HTTPModel.ask_text` shares endpoint controls, authentication, streaming parsing,
size guards, output-token limits and abnormal-finish checks with the JSON client.
Only the text path omits `response_format` (or Ollama's `format`) and requests
plain prose in the system instruction. Other stages keep their JSON contracts.
Reasoning OFF remains the default. Reasoning text never replaces missing content.
Worker copies keep their own callbacks; no nested worker pool is introduced.

Each compact field still requires 8–160 Unicode characters after outer whitespace
is trimmed. The prompt states this bound. Text is not silently truncated or
rewritten. JSON objects/arrays and code fences are rejected, not unpacked.
Each field shares one `1 + --retries` budget across transport and validation;
a new pattern therefore uses at most two such budgets. A valid answer remains
a model interpretation, not proof of literary similarity or human verification.

## Compatible resumption

Previously completed and validated combined notes are reused. New per-field
answers have their own bound cache and transport identity. If writing-use fails,
a successful summary is reused on resume. Old observation, clustering, group-review
and source-check cache identities are unchanged. Do not delete caches or use
`--restart`. The final audit adds `synthesis_text_interface` while retaining the
existing style interface and artifact schema. New text and JSON tasks cannot
share cache identities accidentally.

## Host executors and diagnostics

Host text requests carry `response_mode=plain-text.v1` and a distinct task ID.
The host sends the unchanged prompt to the model, receives plain text, then wraps
it as `{"text": "the unchanged answer"}` for the existing `tasks submit` command.
That transport wrapper must not be requested from the inference model.
`CallableModel.ask_text` expects a plain string. Existing JSON host tasks are unchanged.

Synthesis logs identify both pattern and field. Missing-field errors in remaining
JSON contracts list only application-controlled expected names. Unexpected keys
are counted, never echoed. Diagnostics contain safe codes, types and lengths, not
rejected prose, prompts, source quotations or deployment credentials.
