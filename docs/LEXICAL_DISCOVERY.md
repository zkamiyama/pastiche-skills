# Lexical discovery and contextual reproduction

## Research-to-implementation decisions

The objective is finding useful, attested lexical choices for new writing, not
identifying an author from a word list. Rare words, corpus-preferred words,
spelling variants and generic words used expressively are different categories.
No invented comparison corpus or pretrained-model intuition is a measurement of
population rarity.

Primary sources investigated:

- Andrew Hardie, [Log Ratio](https://cass.lancs.ac.uk/log-ratio-an-informal-introduction/), 2014: normalized frequency differences are effect sizes, not significance.
- Stefan Th. Gries, [Keyness should integrate frequency, association, and dispersion](https://benjamins.com/catalog/cilt.370.02gri), 2025; its references lead to Egbert & Biber 2019 and Gries 2021. Frequency alone conflates broad usage and concentration in a few documents.
- Frantzi & Ananiadou, [C-value/NC-value](https://www.jstage.jst.go.jp/article/jnlp1994/6/3/6_3_145/_article), 1999: nested multiword terms require more than raw frequency. We use the C-value form for bounded contiguous candidates, not the full NC-value pipeline or its medical-domain performance claims.
- Murawaki & Kurohashi, [unknown morpheme acquisition](https://aclanthology.org/D08-1045/), 2008, and [orthographic variation](https://aclanthology.org/L10-1111/), 2010: morphological dictionary analysis is insufficient for unseen spellings. Our bounded unknown-token/span recovery is a heuristic, NOT a reimplementation of their acquisition system.
- NINJAL, [UniDic hierarchy](https://clrd.ninjal.ac.jp/unidic/glossary.html): orthBase groups inflection while preserving orthographic choices. We do not fold spelling variants into lemma-level identities.

Embedding-only keyword retrieval and generic TF-IDF were not used as the primary
selector: the goal is reusable lexical choices, not only document topics, and the
installed pipeline must remain lightweight. This is a design choice, not a claim
that those methods are universally inferior.

## Stage 1: complete measured inventory

Lossless lexical_inventory.jsonl remains unchanged. Vocabulary units retain
orthographic bases, actual observed forms, exact contiguous phrases, dictionary
unknown surfaces and bounded adjacent unknown spans. Proper nouns and grammatical
fragments are not deliberately promoted. Boundaries never cross whitespace,
sentences, names or 800-character tokenizer batches. Extra phrase templates cover
modifier+noun/predicate and complete noun+case-particle+predicate expressions.

Each unit records count, number of works, maximum single-work share, context
diversity, nonstandard-form signals, token components and DP. For occurrence
counts c_i and document character sizes s_i, DP = 0.5 * sum(abs(c_i/sum(c) -
s_i/sum(s))). High DP means concentrated usage. Counts from separate representations
are never added together. None of these measurements proves uniqueness.

## Stage 2: independent discovery lanes

All units receive a disposition in lexical_selection.jsonl. Singletons, unsafe
fragments, grammatical items and unattested dictionary-only headwords are screened
out. Multi-work candidates are preferred; a repeated nonstandard form in one work
can enter as single_work_candidate, never as an established cross-work habit.

Five lanes compete independently: comparison-corpus contrast, nonstandard/unknown
forms, phrases, low-frequency multi-work words (2–12 occurrences), and recurrent
words. Within each lane, parts of speech are interleaved. Each displayed form is
selected once. Phrase scores use log2(token_length) times frequency less mean
nested-parent frequency, with nesting matched on token boundaries. Ranking values
are heuristic and remain separate from evidence/support verdicts.

With --reference, comparisons use the SAME (kind, spelling, POS) units and smoothed
occurrence rates per non-whitespace character. Positive log2 ratios enter the
contrast lane; low counts are attenuated for ranking. This closes the old disconnect
where reference results existed but the new unit shortlist never used them. It is
not a p-value, causal effect or author-attribution test. Reference suitability and
multi-author confounds still require user judgment.

The default --lexical-review-limit -1 uses min(eligible, max(128, min(384,
32 + 8*ceil(sqrt(eligible))))). These are operational budgets, not statistically
validated optima. A positive integer is an explicit budget; 0 reviews every eligible
unit. lexical_discovery_audit.json exposes all counts, exclusions, lane counts and
budget-deferred candidates. No coverage guarantee is implied by an adaptive cap.

## Stage 3: context before semantic judgment

Up to three distinct-work contexts are hash-reservoir sampled across the whole
corpus, rather than always taking the first two files. Duplicate passages do not
pretend to provide independent contexts. Dictionary-recognized proper names and
URLs are masked in memory; this is not exhaustive anonymization. The configured
model sees only these short contexts. No source passage is stored in reports or
caches. Cache identities contain context digests, not context text.

New candidates without retrievable context are deferred without an LLM call.
Other candidates receive one bounded contextual judgment: typicality, role,
confidence and a prose note about scene conditions, register and contrast with a
more generic expression. IDs, frequencies and spellings are attached by Python.
No quotas or forced claims of rarity are requested. A 24-character overlap screen
rejects long copied context in notes. Reasoning OFF and worker concurrency settings
are preserved. Classification and literary interpretation still can be wrong.

## Stage 4: from discovery to writing

lexical_repertoire.json retains all context-screened useful lexical cards, not only
the small Bible shortlist. Low-confidence, topic-only and unclear judgments are
not promoted. It records the exact spelling, observed inflections, usage note,
discovery lane and evidence scope. The Bible displays at most 16 representative entries with 110-character usage
previews; full notes, inflections, ranking and provenance remain in JSON. A
preview ending in an ellipsis is truncated for display, not a rewritten judgment.

pastiche-write/scripts/lexical_palette.py retrieves up to 16 cards for a scene brief
using character n-gram overlap and role cues, with role diversity. It is an offline
retriever, not a semantic judge. The host writer checks the scene and retains the
attested orthography instead of automatically smoothing everything to generic
synonyms. It must not transplant source characters/plots or insert words just to
meet frequency targets. --draft reports longest-first exact substring hits. Zero
hits are not failure, and a stem hit is not a morphological word-frequency claim.

## Safe migration and tests

This change does not invalidate style observations, clustering, source checks,
summary caches or support verdicts. It rebuilds lexical measurements/reviews and
adds a writing-side artifact. For an existing completed profile, stage a copy,
run lexical.py, lexical_review.py --corpus, then render_bible.py --apply. Keep the
original style completion progress/audit when promoting lexical-only outputs.
Never use --restart or clear source-analysis caches for this upgrade.

Synthetic tests cover small-count retrieval despite common-word floods, adaptive
and explicit budgets, complete selection accounting, DP, reference-route wiring,
nested terms, unknown recovery, duplicate contexts, spelling preservation,
contextless deferral, contextual cards and draft auditing. Such contracts do not
prove literary similarity. Held-out corpus/author judgments remain separate.
