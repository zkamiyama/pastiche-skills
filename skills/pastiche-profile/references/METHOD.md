# Feature interpretation

- `p50_sentence_chars`: median sentence length per work; the cross-work `median` is the median of these medians. Newline boundaries make these **typographical** units rather than grammatical sentences.
- `p90_sentence_chars`: each work's 90th percentile of length, then cross-work median.
- `dialogue_char_ratio`: fraction of non-space characters inside paired Japanese corner quotes `「」` or `『』`, approximate for nested/imperfect formatting.
- `comma_per_1000_chars`, `ellipsis_per_1000_chars`, `newline_per_1000_chars`: visible formatting counts over whitespace-stripped character count.
- `contrast/cause/reappraisal/time/uncertainty_per_100_sentences`: lexical phrases anchored at the beginning of sentence-like segments. These reflect surface signals, **not** reliable inference about reasoning, causation, plot or thought.
- `phase_medians`: positions of sentence-like segments in work. They are not true acts or plot turns.
- `morphology=fugashi`: supplementary Japanese POS and function-word whitelist, no names and no content-word vocabulary. It requires optional local dictionary installation.
- `confidence=low`: fewer than 5 qualifying TXT works; `moderate_descriptive_only`: 5 or more, **not** a confidence interval or statistical significance.
- Similarity in these metrics neither proves identity nor guarantees authorial effect.

For research context, see repository `docs/research.md`. Suggested next step after profiling: fill *abstract* narrative rules manually and verify them before generation.
