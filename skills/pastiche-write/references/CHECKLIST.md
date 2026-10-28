# Pastiche Write — planning and revision checklist

The following is a private checklist for an agent, not output to include in every draft.

## Before writing

- Are the user's new characters, setting, genre, viewpoint and target length explicit?
- Are selected narrative rules (`model_supported`, at most three) actually appropriate to the new premise? Do not force them.
- For each beat track `facts`, `character_belief`, `goal`, `decision`, `action`, `consequence`, `belief_after`, and `reader_knows`. An event occurring earlier does not by itself cause the next event.
- Build a small *contextual preference sheet* from `style_bible.md` and optional private `lexical_profile.json`: eligible expression variants, dialogue-vs-narration symbol use, common run lengths, sentence endings and soft rhythm targets. No blind global substitutions.

## Draft and revise

1. First pass — logical storyline and character motivation; avoid recognizable source scenes or long phrases.
2. Second pass — adjust sentence rhythm, dialogue share, punctuation and paragraphing within natural ranges.
3. Third pass — apply measured exact-form preferences only at genuine *opportunities*: a character's idiolect, expressive dialogue ending, sensory description, etc. Symbol and lexical targets are not quotas. `marked/unusual` is a model impression, not measured general-language rarity.
4. Audit — read `write_lexical_review.json` if `scripts/evaluate_lexical.py` was run. Compare `variant_pairs`, `symbols`, `symbol_runs`, `mixed_sequences`, `kaomoji_candidates`, `sentence_endings`; prioritize contextual mistakes over ratio differences. Values labeled `insufficient_comparable_occurrences` should not prompt edits.
5. Keep entity knowledge, chronology, spatial continuity, cause/effect, voice, content and originality intact. A short story may appropriately contain zero instances of a profile's signature symbol.

For long texts, outline → scene blocks with state notes → continuity revision → style pass. This operationalizes aspects of Re3 (Yang et al., 2022), DOC (Yang et al., 2023), and related work; it does not reproduce their models.

## Privacy

Use only generated profile artifacts, not the original corpus. `lexical_inventory.jsonl` should not be pasted into an LLM prompt. The offline audit reads a **new draft and profile** without network calls or source corpus access. Review private artifacts before uploading them to any hosted model or public repository.
