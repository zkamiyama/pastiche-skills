# 語彙の局所提案・挿入・置換

`lexical_palette.py`は執筆前の候補検索です。`lexical_edit.py`は、新しい原稿の
どこでその語を使うかを提案し、比較を通過した局所改稿を別ファイルに保存します。
分析結果・元原稿・Bibleは変更しません。

## 実行

場面単位のUTF-8原稿（12,000文字以下）、題材・話者・守るべき条件を記した
UTF-8のbrief（4,000文字以下）、生成済み`lexical_repertoire.json`を渡します。
既存の`pastiche-profile`を隣接インストールしてください。

```bash
python3 scripts/lexical_edit.py \
  --repertoire /private/profile/lexical_repertoire.json \
  --brief /private/brief.txt --draft /private/draft.txt \
  --out /private/writing/lexical-edits \
  --mode revise \
  --api-base http://model-server.example/llm/v1 --model MODEL_ID \
  --reasoning-effort none --max-output-tokens 4096 --llm-workers 4
```

`--mode suggest`（既定）は提案だけ、`--mode revise`は比較に合格した案を採用します。
`--protect 固有名詞`は変更しない文字列を指定します。繰り返し指定できます。
別の判定モデルが同じエンドポイントにある場合は`--judge-model MODEL_ID`。
`--max-units 6`が検討箇所の上限、`--max-edits 3`が改稿箇所の上限です。
検討は最大4並列、比較は逐次。同じ語の多重挿入と使用数ノルマは設けません。

## 分担

- Python: 文の範囲、候補検索、原稿ハッシュ、差分、語の参照、適用箇所を管理。
- 執筆モデル: 指定箇所だけの変更後の本文を返す。不適切なら元の本文を返す。
- 比較モデル: A/Bのどちらが文脈と語感に適切か判定。不明・同等なら採用しない。

LLMはID・位置・JSON・元原稿のハッシュを転記しません。挿入と置換は、返された
短い本文をPythonが比較して分類します。原稿全体の文字列置換は行いません。

## 採用条件

原稿の数値、明示的な否定、引用符、指定した保護語、大きすぎる変更を機械検査します。
候補にある実測表記が実際に導入されたかも確認します。採用案全体をA/BとB/Aで比較し、
意味・話者・出来事・視点を保ち、かつ明確に良いと両方で判定された場合だけ採用します。
比較が矛盾、不明、同等、形式不正なら元稿を残します。機械検査や同一モデルの一致は
意味保存の証明ではありません。人物認識・世界設定を扱う作品では既存の構造監査も必要です。

辞書にあるだけで未観測の語形、一作品限定の候補は自動挿入の対象にしません。
取得候補に意味的に不適切な語がある場合も、使う義務はありません。

## 出力

`--out`直下に新しい`lexical-edit-*`ディレクトリを作ります。

- `original.txt`: 入力原稿のコピー。
- `candidate.txt`: 検査を通過した挿入・置換案。まだ採用とは限らない。
- `selected.txt`: 比較で採用した本文。未採用・提案モードでは元稿。
- `changes.diff`: 元稿と候補の差分。
- `result.json`: 適用位置、対象語、変更種別、採否、ハッシュ。
- `status.json`: 最後に書かれる完了状態とresultのハッシュ。

元のファイルは上書きしません。入力が途中で変わったら停止し、完了扱いにしません。
`status.json=completed`は処理完了であり、採用判定は`result.json.state`を確認します。
`accepted_by_model`以外は自動採用しません。

サブエージェント実行は既存の`--executor subagent --task-dir ...`へ委譲できます。
終了75は回答待ちであり、結果登録後に同じコマンドを再実行します。今回はこの経路と
実サーバーへの接続は未検証です。元コーパスは読み取らず、原稿・短い語彙カードのみを
利用者が指定したモデルへ送ります。出力には新稿の一部が含まれるため非公開で保管します。