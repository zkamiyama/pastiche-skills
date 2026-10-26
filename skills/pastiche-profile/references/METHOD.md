# Feature interpretation

- `p50_sentence_chars`: median sentence length per work; the cross-work `median` is the median of these medians. Newline boundaries make these typographical units, not fully parsed grammatical sentences.
- `p90_sentence_chars`: each work's 90th percentile of length, then cross-work median.
- `dialogue_char_ratio`: non-space characters inside paired Japanese corner quotes, approximate for nested or incomplete formatting.
- `comma_per_1000_chars`, `ellipsis_per_1000_chars`, `newline_per_1000_chars`: counts per whitespace-stripped characters.
- `contrast/cause/reappraisal/time/uncertainty_per_100_sentences`: sentence-initial lexical cues; **not valid causal or psychological conclusions**.
- `phase_medians`: sentence position segments (opening 20% / middle 60% / ending 20%); not act boundaries.
- `morphology=fugashi`: only preselected functional word and POS frequency; no open-class vocabulary exported.
- `confidence=low`: fewer than five TXT works; `moderate_descriptive_only`: five or more (not a confidence interval).
- Repeated narrative IF/THEN/EXPECT candidates, when present, come from the separate **local-LLM** procedure described in [NARRATIVE_METHOD.md](NARRATIVE_METHOD.md). Two model passes and >=2 supporting works do not constitute independent proof, causal identification, or author identification.
- Human interpretation is not *required* to produce v2 candidate rules, but human review and genre-matched baseline evaluation remain important for literary validity.

For research references see repository `docs/research.md`.
