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

## Full-story / character checks (v2)

- Is the selected structure a corpus-level abstract pattern rather than one original work's event sequence?
- Does every scene belong exactly once to a plot-tree leaf and have valid prior dependencies?
- Are character and reader knowledge separate, with no use before acquisition?
- Are original/canon modes explicit, with canon scope and presentation cutoff fixed?
- Are character constraints current usable auto-accepted/human-reviewed claims, with uncertainty preserved and revoked/stale claims excluded?
- Are conditional voice, audience, reported speech, belief and fact distinguished?
- Did structural revision happen before surface imitation? Does a source-cited LLM draft audit remain clearly a model judgment?

## v3 perspective contract

- Are world, private beliefs, heard reports and reader disclosures separate?
- Do events change only the perspectives that have declared perceptual access?
- Are every action's mental premises declared, with false belief distinguished from knowledge?
- Does the scene writer receive only the intended character-view context, not director truth?
- Have model-detected access/scene mismatches been repaired? An empty issue list is not proof.
- Are subagent tasks private and validated on resume, rather than unreviewed raw answers?


## 根拠付きRefine

- パターンの適用対象外・判断困難を許容し、根拠のない修正要求を出していない。
- 原稿ハッシュに結びついた局所差分のみ。人物の認識・観測・出来事・世界条件を勝手に変えていない。
- 人物用改稿へ全知情報や未来状態を混ぜていない。全知の監査役の指摘を人物用にそのまま流していない。
- 再診断・意味監査・提示順を入れ替えた比較のどれかが不明／悪化なら、元稿か前回採用稿を残した。
- `refine_status.json` と採用ファイルのハッシュを照合し、旧結果を今回成功したものと誤認していない。
- 統計への近さと文章品質を混同せず、同一モデルの一致を独立した文体類似度の証明としていない。
