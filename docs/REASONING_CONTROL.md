# Reasoning controls — HTTP requests default to OFF

## Defaults and explicit overrides

All HTTP model entry points share `execution.add_model_flags` and
`llm_runtime.HTTPModel`. New requests default to `--reasoning-effort none`.
This applies to profiling (lexical review, style, story), research, planning,
draft audit and write-skill refinement. It controls these HTTP requests, not
an unrelated host LLM writing prose or a host-subagent's internal settings.

| CLI value | OpenAI-compatible request | Ollama `/api/generate` |
| --- | --- | --- |
| `none` (default) | `reasoning_effort: "none"`, `chat_template_kwargs: {"enable_thinking": false}` | `think: false` |
| `low`, `medium`, `high` | Selected `reasoning_effort`, `chat_template_kwargs: {"enable_thinking": true}` | `think: "low"`, `"medium"` or `"high"` |
| `auto` | Omit both reasoning-control fields | Omit `think` |

These are requests to the server, not proof that any arbitrary model supports
or honors them. Effort levels and OFF support depend on the model, template and
server version. An always-thinking model may not offer a true OFF mode.
`auto` deliberately delegates to server defaults and does **not** mean OFF.
Ollama levels require a model supporting those string values; otherwise select
an appropriate supported mode instead of silently substituting `true`.

```bash
# Default OFF; no additional option is needed.
python3 skills/pastiche-profile/scripts/run.py /private/corpus \
  --api-base http://model-server.example/llm/v1 --model model-placeholder \
  --out /private/profile --llm-workers 4 --skip-story

# Opt into a server/model-supported effort level.
# Append this to the same command: --reasoning-effort medium

# Leave reasoning configuration entirely to the server (legacy wire behavior).
# Append: --reasoning-effort auto
```

`--no-thinking-template` omits the nonstandard `chat_template_kwargs` field
while retaining `reasoning_effort`. Use it with strict OpenAI-compatible servers
that accept effort but reject template extensions. If the server accepts neither
field, `--reasoning-effort auto` is the explicit compatibility escape hatch.
There is no automatic fallback that secretly re-enables thinking on an HTTP error.
The template switch has no effect on Ollama's native `think` field.

The defaults of temperature, streaming, timeout, output-token limit and worker
count are unchanged. `--max-output-tokens` limits generated output; it is not a
separate reasoning budget. No `reasoning_format`/visibility toggle is used to
pretend reasoning has been disabled. The normal JSON/schema/source checks and
abnormal-finish rejection remain enabled.

## CLI forwarding and host subagents

`run.py` forwards the selected controls to every HTTP child command, including
lexical review, style extraction and full-story workbench analysis. Direct
`workbench.py plan`, `audit-draft`, `refine` and the research/write wrappers use
the same parser and model factory. Worker copies retain the scalar controls;
each HTTP request receives a fresh options dictionary.

Host-subagent execution sends no HTTP request, so the inherited `none` default
(and `auto`) do not configure host reasoning. Non-default HTTP effort levels or
`--no-thinking-template` with `--executor subagent` fail explicitly. Configure
the host separately and version its `--agent-profile` when its model changes.
Offline `--mode none` performs no LLM requests and is not changed.

## Cache compatibility and resumption

Effective reasoning request fields now contribute to cache identities in all
four places: style analysis, lexical review, workbench tasks and refinement's
model fingerprint. OFF, enabled effort levels and the template-field opt-out
cannot silently share results. Streaming and worker count do not change the
reasoning identity. Controls omitted with `auto` retain the exact legacy keys;
non-HTTP fixture and host-subagent cache identities are unchanged.

**Updating from the old implicit server-default mode to the new default OFF
will re-evaluate affected LLM tasks under a new cache identity.** Old entries are
not deleted, relabelled as OFF, or migrated automatically. This includes completed
observations: a run can therefore revisit stages rather than resume only its last
failed panel. Human graph review decisions and the corpus are not cleared.
Use the existing output directory without `--restart` to keep all cache histories.
Use `auto` only when intentionally returning to server-default behavior, not as a
workaround to reuse old results while claiming OFF. Do not wipe caches to fix a
transport/finish problem.

## Verification

`tests/test_reasoning_control.py` exercises request defaults/overrides, strict
server opt-out, Ollama mapping, CLI/child forwarding, all entry-point help,
legacy embedded callers, subagent boundaries, exact legacy cache compatibility,
cache separation, real loopback JSON/SSE requests, four parallel worker copies,
and retained failure handling. Tests use synthetic inputs only. Successful JSON
validation is not a claim of semantic analysis quality or literary fidelity.

```bash
python3 -m unittest discover -s tests -p 'test_reasoning_control.py' -v
python3 -m unittest discover -s tests -v
```

## Primary protocol references

- [llama.cpp server: Chat Completions](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md#post-v1chatcompletions-openai-compatible-chat-completions-api): `reasoning_effort` and `chat_template_kwargs`.
- [Ollama generate API](https://docs.ollama.com/api/generate): native `think` request field; supported values are model-dependent.
