# Correctness audit — v3.1

## Scope and result interpretation

This audit started from commit `36473a156b419295122b3b41c9d61196e6215f94`.
The local source tree was checked against the published Git tree before editing.
The pre-existing 122 synthetic tests passed. New counterexamples were then added
before changing the affected behavior. No production model, private corpus or
external character website was used.

This release **does not claim that the whole program is formally verified or
bug-free**. It combines regression tests, closed input contracts, an independent
finite reference model, exhaustive enumeration, controlled defect injection,
and cross-platform/browser CI. The state-machine argument below applies only to
the declared first-order model, not to whether a model correctly reads fiction.

## Reproduced defects and repairs

| Boundary | Counterexample | Repair |
| --- | --- | --- |
| Character scene context | A scene entry exposed discoveries from the end of that scene. | The default projection uses the state before the first chronological event. `--event-id` selects another event's entry, never its future state. |
| Chronology vs presentation | A scene depended on a scene whose events occurred later; flashback order was also mistaken for causal order. | Dependencies are checked against event chronology, independently of the reader-facing scene list. |
| Timeline contracts | Misspelled operations, invalid identifiers or non-object rows were ignored or failed with incidental Python errors. | A closed, detached normalization pass validates every record before replay. |
| Selected context | Empty claim lists could bypass entity checks; selected plot patterns were not revalidated against current source provenance. | Validate all sections, records, entity endpoints and persisted pattern provenance on every use. |
| Long inference | A user could withdraw a claim or change a draft during inference, after the initial freshness check. | Revalidate current DB decisions and source/draft hashes before publication. Mark failed/waiting operations explicitly. |
| Subagent submission | Two simultaneous first submissions could both pass an existence check and overwrite each other. | Exclusive publication uses a unique private temporary file and atomic no-clobber creation; replacing an answer requires explicit intent. |
| Task integrity | Altered queued requests could be shown to a worker before their hash was checked. | Validate the request schema, identifier, profile and prompt binding before show/list/submit as well as resume. |
| JSON/model protocol | Duplicate keys, non-finite numbers, Python-only callback values, or a truncated-but-parseable model result could cross the boundary. | Shared strict JSON validation and bounded parsing; incomplete generation is an error, not an empty successful extraction. |
| Caches and execution | Duplicate task IDs or identical prompts from different stages could collide. | Validate task identity before execution and bind cache keys to the stage and protocol version. |
| Memory and publication | Cumulative report snapshots could grow far beyond a cardinality limit; fixed-name temporary files could race. | Add a serialized-state byte budget and unique atomic output files. |
| Documentation | Removed legacy references still had inbound links. | Repair references and check relative Markdown links automatically. |

The shared modules are `json_boundary.py`, `epistemic_schema.py` and
`context_validation.py`. Application runtime dependencies remain Python's
standard library; verification also uses only the standard library.

## State model and invariants

Write a modeled state as `S = (W, B, H, R)`: current world values `W`, each
character's values and evidential bases `B`, reports received `H`, and reader
disclosures `R`. Unknown is distinct from either of the two modeled values.

1. **World/mind separation.** A world update modifies only the selected entries
   of `W`. A report, private inference or forgetting operation does not modify
   `W`. Observing a fact copies its current modeled world value into only the
   observer's state and requires declared participant/channel access.
2. **Frame conditions.** An operation does not modify another agent or another
   fact unless that agent/fact is explicitly an affected recipient or target.
3. **Unobserved false-belief persistence.** Updating `W[f]` without a matching
   mind-changing operation leaves `B[a,f]` unchanged. Falsehood relative to the
   current world is not itself a reason to delete a character's belief.
4. **Accepted testimony.** Receiving and accepting are separate. Only accepted
   recipients change belief; truth of the reported value is not assumed.
5. **Reader independence.** Presentation-order disclosure changes the reader
   ledger, not a character's modeled information. Story events are replayed in
   chronology, independently of presentation order.
6. **Entry projection noninterference.** For fixed neutral labels, two plans
   with equal pre-event private state for `a` expose equal private views for `a`,
   even when world truth, other agents' states or later discoveries differ.
7. **Chronological prerequisites.** For `A depends_on B`, every event of `B`
   precedes the first event of `A`: `max(order(B)) < min(order(A))`. This strict
   inequality also rules out dependency cycles: a cycle would imply a time is
   strictly less than itself.

For invariants 1–5, the base case is the explicitly declared initial state. Each
operation's write set preserves the unaffected components, so composition of
valid transitions preserves the frame and separation properties by induction.
This is a design argument, **not a mechanically checked unbounded proof of the
Python implementation**. The executable checks below compare that implementation
against a separately written small-state transition specification.

## Executable finite verification

```bash
python -m unittest discover -s tests -v
python verification/model_check.py --out /private/bounded-verification.json
python verification/mutation_check.py --out /private/mutation-verification.json
python -m compileall -q skills tests verification
node --check skills/pastiche-profile/scripts/story_workbench/web/app.js
```

`verification/model_check.py` exhaustively enumerates:

- two agents and three values (unknown, `v0`, `v1`);
- every initial assignment for one fact and every two-operation trace over
  22 operation variants;
- every initial assignment for two facts and every one-operation trace;
- explicit invalid access/chronology cases and relational private-view pairs.

The configured bounds yield **45,147 transition/guard cases** (36,216 accepted,
8,931 rejected) and **27 private-view comparisons**. Accepted results are also
serialized, normalized and replayed to test idempotence. The expected transition
state is calculated by an independent algebraic oracle, not by calling the
implementation being checked. A mandatory unit test runs the complete finite
check; there is no optional verification library to silently skip.

`verification/mutation_check.py` separately injects eight controlled defects:
lost world update, broken perception, believing every heard message, testimony
changing reality, ignored forgetting, removed access check, removed chronology
check and a future-knowledge projection leak. Each must be detected by the
checker. A syntax/import failure is not counted as successful defect detection.
These eight mutants are targeted sensitivity tests, not a mutation score for the
entire repository.

CI runs the synthetic suite on Linux (Python 3.10/3.12), Windows and macOS
(Python 3.12). Ordinary CI also exercises the character, world and perspective
editors with Chromium over real loopback HTTP. A sandbox-only component adapter,
when explicitly enabled, is not equivalent to browser transport/CSP validation.

## Publication, concurrency and trust boundaries

Writer operations produce `writing/operation_status.json`. `state=completed`
means the operation reached its final checks and output publication; its
`result_sha256` identifies the returned result. It does **not** mean an LLM
review found the content correct: inspect the separate plan/audit status and
issues. A failed, interrupted or waiting operation must not be presented as a
fresh successful result merely because an older output file remains on disk.

After long model calls, publication rechecks current claims and pattern source
versions under a SQLite write transaction. Draft audits also rehash their input.
Individual JSON files are atomically replaced. This is not a distributed
transaction across SQLite, all files, the model server and arbitrary external
source editors. Keep one writing operation per output directory; concurrent
writers to the same output directory are not a supported orchestration mode.

Task results are inert, bounded JSON. Atomic no-clobber submission chooses one
winner among concurrent first submissions; unsupported filesystem behavior
fails closed rather than falling back to a racy overwrite. Content hashes bind
answers to expected requests; they are not authentication against a malicious
user with write access to the private task directory. Symlink task files are
rejected. Windows directory ACLs remain the operator's responsibility.

## Deliberate limits and migration

This is a finite first-order model. Nested beliefs, probabilistic minds,
semantic entailment, asynchronous message delivery, perfect entity resolution
and comprehensive contradiction detection are outside this verification.
Initial states, observations and access declarations remain supplied assumptions.
Free-text labels/IDs can contain spoilers even when structured private-state
projection is correct. The application cannot certify that source prose entails
an LLM's mutually consistent declarations.

External source files are not locked against arbitrary simultaneous editors.
Hash checking narrows stale-input windows but is not filesystem snapshot
isolation. An authorized adversary editing source files or the private SQLite
DB can invalidate these trust assumptions. Resource limits are safety ceilings,
not performance guarantees for the maximum-size input.

Existing SQLite identities and human decisions are preserved. Re-export writer
context after editing claims or upgrading; provenance validation is stricter.
Private scene views now use `pastiche.scene.perspective.v2` and expose entry state
only (`beliefs_before`, not `beliefs_after`). Consumers that depended on the old
future-inclusive view must update. `--event-id` permits explicit later entry
views. Protocol version 3.1.0 invalidates affected inference caches.
