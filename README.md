# pastiche-skills

日本語の TXT コーパスを、**全文の決定論的計測 + 根拠付き LLM 質的分析**で作風ガイドに変換し、別の人物・題材・筋書きでの新規執筆を支援する Agent Skills です。Python 3.10 以降。利用者の原文を GitHub に置く必要はありません。

標準の `--mode llm` は、これまでの文字列類似度だけによるナラティブ分類を使いません。場面単位で語彙・情感・語り口・台詞・感覚・テンポ・物語構造を観察し、LLM が共通技法をグループ化してから、グループの整合性と原文根拠を再検査します。**同じ語彙やテーマの共有だけでは同一技法とみなしません。**

## クイックスタート

```bash
python3 -u skills/pastiche-profile/scripts/run.py "/path/to/corpus" \
  --api-base "http://model-server.example/llm/v1" \
  --out "/path/to/.pastiche-private/profile" \
  --llm-workers 2
```

- TXT を含むフォルダを渡します（UTF-8 / CP932）。`--out` は Git 管理外の非公開フォルダを推奨します。
- OpenAI 互換 API のパス接頭辞は保持され、`/v1` に `/chat/completions` を補います。Ollama の `/api/generate` にも対応します。
- `~/.pi/agent/models.json` に同一ベース URL のモデルが一つあれば自動選択します。それ以外は `/models` を参照し、複数あれば `--model MODEL_ID` が必要です。
- リモートに指定したモデルサーバーには、**サンプルした原文チャンクと短い語彙候補を送信します**。HTTP は暗号化されないため、信頼できるネットワークで使ってください。`--api-key-env` は環境変数名を指定します。認証つきリモート接続は HTTPS を使用してください。
- `--check` は合成 JSON のみで通信テストし、コーパスを読みません。
- **標準で全ての適格な作品を対象**とし、各作品から最大2チャンクを抽出します。91作品なら最大182チャンクのLLM観察です（630チャンクすべての分析ではありません）。作品数を減らす場合のみ `--analysis-works N` を指定します。
- 初回のみ必要なら `fugashi-plus[unidic-lite]` を専用キャッシュ venv に導入します。`npx skills add` 自体にインストールフックはありません。導入できない場合は `--no-auto-install --morphology none` で全文語彙一覧なしの縮退運転ができます。

## アーキテクチャ

| 工程 | 方法 | 出力・検査 |
| --- | --- | --- |
| 全文の文体・語彙・表記 | Python（原文全文のローカル計測） | `style_profile.json` / `lexical_profile.json` |
| 短い語形の一般語感（補助） | LLMによる一般的な日本語としての直感評価 | `lexical_judgments.json`（実際の人口頻度ではない） |
| 場面の観察 | LLM・選択した作品/チャンクを並列分析 | 語彙、情感、声、台詞、感覚、緩急、構造を証拠 ID 付きで抽出 |
| 作品横断の意味的グループ化 | LLM・次元ごとの有限サイズのパネルを複数回比較 | 同一の「作者が行う操作」を仮説化。単にテーマが近いだけの候補は区別 |
| グループの審査と原文確認 | LLM・独立したグループ判定 + 元チャンクでの再検証 | **異なる2作品以上**の根拠が確認された場合のみ `model_supported` |
| 根拠付きの全体要約 | LLM・`model_supported` のみを読み、パターンIDつきで解説 | `qualitative_summary.json` |
| 統合 | Python・検証済み記録だけを取り込む | `qualitative_bible.md` と `style_bible.md` |

場面の観察結果・分類結果は、自由記述ですが抽象化した短い説明と匿名セグメントIDだけを保存します。モデルが出したIDはPythonで照合し、原文の長い連続文字列の転記を棄却します。語彙カテゴリでは、原文に実在したごく短い語形だけを任意で保持します。LLMの生成内容や人間の効果に関する断定は正しいとは限らず、`model_supported` は**人間による独立検証ではありません**。

### なぜナラティブが0件だったのか

旧 `fast` は `SequenceMatcher` による閾値0.70の文字列類似度でしか候補をまとめませんでした。同じ技法が違う言い回し（日本語/英語を含む）で記述されると結合されず、再検証する複数作品候補が0になることがあります。新規の `llm` では表現方法・条件・効果の一致を明示的に比較するため、単語の一致だけに依存しません。モデルが共通技法を発見できる保証はありません。候補数、グループ数、検証数を監査出力に記録します。

## 生成ファイル

```text
.pastiche-private/profile/
├── style_profile.json             # 全文の文体統計
├── lexical_profile.json           # Unicode表記・記号・頻度
├── lexical_inventory.jsonl        # 全語形（辞書が利用できる場合）
├── lexical_candidates.json        # 短い候補
├── lexical_judgments.json         # LLMの一般語感（成功した場合）
├── lexical_bible.md
├── lexical_interpretation.md      # LLM語感（成功した場合）
├── qualitative_observations.json  # サンプル場面の短い抽象的観察と証拠ID
├── qualitative_patterns.json      # 統合候補 + 支持作品数 + 検証状態
├── qualitative_summary.json       # LLMがまとめた根拠つき作風の特徴
├── qualitative_bible.md           # 検証を通った定性的な作風ガイド
├── narrative_rules.json          # ナラティブ構成技法
├── style_llm_audit.json           # 工程別の呼び出し/キャッシュ/再検証数
├── narrative_audit.json           # 上記と同じ分析の監査情報
├── narrative_progress.json        # 途中状態・成功/失敗
├── style_llm_checkpoint.sqlite3   # LLM工程のチェックポイント
├── lexical_review_checkpoint.sqlite3
└── style_bible.md                 # 定量 + 質的 + ナラティブの統合
```

特に `style_llm_audit.json` の `discovered_observations`、`cross_work_candidate_groups`、`verified_groups` を区別してください。`sample_coverage` は**サンプルした作品**を分母とし、未解析の作品に特徴が存在しないとは解釈できません。

## 速度・精度・コスト

```bash
# 負荷を減らしたいときだけ対象作品を最大30件に制限
python3 -u skills/pastiche-profile/scripts/run.py /path/to/corpus \
  --api-base http://model-server.example/llm/v1 \
  --analysis-works 30 --chunks-per-work 2 --llm-workers 4

# LLMを使わず全文の表記・統計だけ計測
python3 skills/pastiche-profile/scripts/run.py /path/to/corpus --mode none

# 従来の文字列類似度による高速モード（比較・互換用）
python3 skills/pastiche-profile/scripts/run.py /path/to/corpus \
  --api-base http://model-server.example/llm/v1 --mode fast

# 従来のチャンク全数二重解析
python3 skills/pastiche-profile/scripts/run.py /path/to/corpus \
  --api-base http://model-server.example/llm/v1 --mode full

# 語感の一般言語モデル評価を省略。文脈つき質的分析は維持
python3 skills/pastiche-profile/scripts/run.py /path/to/corpus \
  --api-base http://model-server.example/llm/v1 --no-lexical-review
```

| オプション（`llm` モード） | 既定 | 内容 |
| --- | ---: | --- |
| `--analysis-works` | **0（全作品）** | 対象は全ての適格な作品。正の整数で作品数を制限 |
| `--chunks-per-work` | 2 | 各作品の離れた箇所から選ぶ場面数 |
| `--chunk-chars` | 3800 | チャンクの目安文字数 |
| `--cluster-batch-size` | 30 | 1度にLLMに比較させる候補数 |
| `--cluster-rounds` | 3 | 分割された候補を再配置して比較する最大周数 |
| `--verify-groups` | 16 | 原文再検証へ進めるグループ上限 |
| `--verify-works` | 4 | 1グループあたり原文を検査する作品数上限 |
| `--min-works` | 2 | 採用の必要最低作品数 |
| `--llm-workers` | 1 | 各LLM段階の最大同時リクエスト数 |

全作品を標準で対象にするため、コーパスの規模に比例してLLMの観察リクエストが増えます。大規模コーパスでは `--analysis-works N` で負荷を制限できます。

LLM呼び出しは段階ごとに実行し、段階内で並列化します。実際の速度は指定したサーバーの同時推論能力次第です。局所観察のキャッシュはグループ化の設定変更でも再利用し、チェックポイント書き込みはメインスレッドのみが担当します。`--restart` のみ分析キャッシュを消去します。通信失敗や不正なJSONは、根拠がないという判定ではなくエラーとして扱います。`--timeout 0` は既定でソケット待機時間に上限を設けません。

## 品質・秘匿性の限界

- 解析は一部の場面だけを読むので、長い伏線、稀な語法、作品全体の情感の推移を見落とし得ます。
- 同じシリーズの多数のファイルは、互いに独立した作品とは限りません。別作者・同ジャンルの対照サンプルがない限り、作者固有性を立証しません。
- ナラティブと語感の評価はモデルの解釈です。証拠IDが原文にあることと解釈が正しいことは同じではありません。
- `lexical_inventory.jsonl`、短い語形の候補、質的出力は非公開で保管してください。外部にアップロードしないでください。
- 記号・表記揺れはUnicode正規化せず、`♡/♥`、`！/!` などを区別します。顔文字候補はヒューリスティックであり誤検出し得ます。
- 作風再現の成否はガイドが完成しただけでは確かめられません。新しい題材で執筆し、**条件を隠した比較評価**が別途必要です。

## 執筆に利用する

`pastiche-write` は `style_bible.md` の実測統計、短い語形選好、原文で再確認された質的・物語技法を使います。元作品の台詞、人物、筋書きや固有表現の長い連続は転用しません。生成後は `evaluate_lexical.py` と `pastiche.py evaluate` によりローカルで集計値を確認できます。

```bash
npx skills add zkamiyama/pastiche-skills
python3 -m unittest discover -s tests -v
```

実装手順: [LLM Style Method](skills/pastiche-profile/references/LLM_STYLE_METHOD.md)。
旧方式の背景: [Narrative Method](skills/pastiche-profile/references/NARRATIVE_METHOD.md)。
MIT License。
