# Stylometry, lexicon and exact orthography: methods and limitations

## Surface measurements

- `p50_sentence_chars` / `p90_sentence_chars`: punctuation and line splitting are typographic proxies. Aggregation is the median across individual work values, not pooled paragraphs.
- `dialogue_char_ratio`: approximate Japanese corner quote pairing (`「」` and `『』`), not dialogue attribution.
- `contrast/cause/reappraisal/time/uncertainty_per_100_sentences`: sentence-initial fixed expressions, NOT direct evidence of causal inference.
- `phase_medians`: fixed opening 20%, middle 60%, ending 20% of sentence-like units; these are not act boundaries.

## Exact lexical and expression features

- **No Unicode normalization:** `♡`/`♥`/`❤`, `っ`/`ッ`, `！`/`!`, `♪`/`☆` are kept distinct.
- `variant_pairs`: non-overlapping exact substring counts for each pair, per-work occurrence count, proportion within the pair, approximate dialogue/terminal use. Default examples include `じゃねえか` vs `じゃあねえか` and `来たっ！` vs `来たッ！`; supply additional pairs via `--variants`.
- `symbols`, `symbol_runs`: character-by-character counts and exact consecutive repeat length (no arbitrary cap on the **statistics**). Mixed symbol sequences use a bounded prefix detector; emojis with multiple Unicode scalars may not be a single unit.
- `kaomoji_candidates`: heuristic matching of compact parenthetical forms; false positives and misses are possible.
- `sentence_endings`: last four literal characters of sentence-like units; these are candidates, not a grammatical ending parser.
- `lexical_inventory.jsonl`: ALL surface tokens as segmented by fugashi/MeCab with observed counts, frequency per 10k tokens, word POS, number of works. This requires `pip install 'fugashi[unidic-lite]'`. Orthographic distinctions are preserved; morphological segmentation can divide a colloquial phrase into multiple tokens.
- `collocations`: immediately adjacent content-class morphological surface forms; does not infer dependency or semantic collocation. No whole sentences exported.
- `distinctive_words`: when an appropriate comparison corpus is provided, **descriptive** smoothed log2 relative frequency from full counters. Differences may reflect topic, subgenre or characters, not authorship.

## Model's ordinary-Japanese prior

`lexical_review.py` sends only a bounded shortlist of short forms and their aggregate counts, not original paragraphs. It asks the configured LLM to classify the perceived typicality (`ordinary`, `marked`, `unusual`, `uncertain`) and role (`colloquial`, `emphasis`, etc.) based on pretrained familiarity with general Japanese and fiction.

**Do not equate the judgment with population frequency.** The LLM has an implicit prior, but training-data frequency, register, model size and task prompting can bias it. A rare-looking word can be a topical term; a common word can be distinctive by placement or combination. The author-corpus recurrence (Python) and the model's general-language impression are separately stored, and neither by itself proves author identity.

The full inventory remains local. The default shortlist (96 forms, in batches of 24) includes lexical tokens plus exact variants, symbolic repetition, sentence endings and collocation samples. `--lexical-review-limit 0` permits all eligible forms; it is considerably slower. Batch results are resumable by content/model fingerprint. **No generated free-form reasons** are stored or used as rules.

## Relevant primary references

- [Huang & Jin (2020), Japanese function phrases for authorship identification](https://doi.org/10.2964/jsik_2020_035).
- [Koppel et al. related topic confounding, morphosyntactic stylometry](https://www.mdpi.com/2078-2489/15/4/211).
- [LLMs and human psycholinguistic norms (2024)](https://link.springer.com/article/10.3758/s13428-024-02337-z): model linguistic norms can be useful proxies but require validation.

These methods support the design, not a demonstrated guarantee of any particular style imitation result.
