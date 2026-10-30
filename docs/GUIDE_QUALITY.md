# Readable Bible: lexical units, examples and provenance

## Removed parenthetical harvesting

The kaomoji heuristic is removed, not just hidden: neither the lexical profiler
nor the write-side evaluator extracts arbitrary parenthetical spans. Legacy
`kaomoji_candidates` data is ignored on rendering/audit. Symbol and exact glyph
statistics remain separate, deterministic measurements.

## Meaningful vocabulary rather than fragments

The raw, lossless surface inventory is unchanged. A companion vocabulary layer
uses UniDic `orthBase` for inflected words, while retaining counts of the actual
surface forms. Counts of a base are explicitly labelled as aggregated counts,
not occurrences of that exact spelling. Work counts are unions per input work,
not sums over inflections. Dictionary-only bases never observed literally are
kept in the private inventory but omitted from the writing shortlist; automatic
morphology can otherwise assign a misleading lexical sense to a fragment.

Compounds and noun/case-particle/predicate expressions must be actual contiguous
source spans. Token strings are never joined with invented spaces or across
sentence, whitespace, name or tokenizer-chunk boundaries. Proper nouns and
non-independent grammatical categories are excluded. A small documented
function-word filter, POS balancing and cross-work recurrence are triage
heuristics, NOT evidence of author distinctiveness. Symbols, short cries and
clipped inflections do not occupy vocabulary slots. Ordinary but useful words
can appear; "rare/marked" is no longer a prerequisite.

`lexical_vocabulary.jsonl` holds the full private unit inventory.
`lexical_profile.json` holds a bounded 128-entry preview plus metadata;
`lexical_candidates.json` is the selected review input. These counts do not mix
with symbol, symbol-run or pair counts. Duplicate display forms are shown once,
without adding incompatible counts.

With `lexical_review.py --corpus`, up to two short observed contexts from separate
works are selected locally for sense disambiguation. Dictionary-recognized proper
names are masked; this is not a guarantee of exhaustive anonymization. Contexts
are sent ONLY to the configured model, remain transient, and are not saved as
passages. Cache identities include their digest. The guide displays the model's
abstract usage note, not the contexts. A 24-character overlap guard rejects long
copied context fragments. This does not prove that every lexical judgment is right.
Topic-bound, unclear, low-confidence and merely general labels are not promoted.
Raw notes remain available privately; very long notes are explicitly shortened
for display, with a reference to the full JSON.

Without a reference corpus, frequency and an LLM impression do not establish
unusualness, author uniqueness or the true sense in every passage.

## Examples are illustrations, never replacement evidence

The human-readable guide no longer calls an opaque `P0001` marker an explanation.
It shows the number of works rechecked by the model. IDs and their evidence
bindings remain in the original JSON artifacts for traceability.

`guide_examples.py` requests up to two examples per supported pattern by default.
Each request returns only prose; Python attaches the pattern and display label.
The model receives an abstract mechanism and a NEW neutral everyday scenario,
not source passages. Names and original setting are not requested. These are
**new explanatory illustrations, not anonymized quotations, empirical evidence,
or proof that an author actually wrote such sentences**. The distinction is
printed in the guide and stored in `guide_examples.json`.

A local 24-character overlap screen compares proposed text against the corpus.
It is a conservative copying check, not an originality or semantic-quality proof.
A measured dominant heart glyph is supplied when applicable. A literal
`\u3000` emitted in prose is rendered as an ideographic space; no arbitrary
JSON/key reinterpretation or sentence rewriting is performed. Topic-dependent
mechanisms that cannot be illustrated neutrally may be omitted rather than
silently generalized. Invalid examples get bounded retries; omissions are explicit
and do not falsify the underlying support verdict or block an otherwise completed
profile. `guide_examples_audit.json` records generated/omitted counts separately.
`--examples-per-pattern 0` disables examples. Their task caches are independent.

## Integration and safe refresh

`run.py` creates examples after successful style analysis and before final guide
assembly. Existing observation, grouping, source-check and synthesis cache keys
are unchanged. A vocabulary shortlist/context change re-evaluates only the
lexical review, not the entire style analysis. No `--restart` is needed.

Existing profiles may be refreshed by running `lexical.py`, `lexical_review.py
--corpus`, `guide_examples.py`, then the normal `pastiche.py guide` composition.
Use a private staging copy, inspect the generated guide, and preserve the original
analysis/audit/checkpoints. Lexical review's progress file is not a style-completion
signal. New illustrative prose remains model output and should be reviewed before
using it as a writing demonstration.

## Primary references

- [UniDic hierarchy and base-form terminology](https://clrd.ninjal.ac.jp/unidic/glossary.html)
- [UniDic feature names](https://clrd.ninjal.ac.jp/unidic/faq.html)
- [fugashi named feature access](https://github.com/polm/fugashi)
