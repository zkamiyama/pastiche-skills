# pastiche-skills

日本語TXTコーパスから **文体・語彙・表記・物語構造** のプロファイルを作り、新しい題材の執筆に使う Agent Skills です。原文をリポジトリに置く必要はありません。Python 3.10以降。

`pastiche-profile` は全文の統計をローカルで計測し、必要な場面だけモデルに送って構成技法を抽出します。`pastiche-write` は生成されたガイドを使って、独立した人物・設定・プロットで執筆します。

## 最初の1コマンド

```bash
python3 -u skills/pastiche-profile/scripts/run.py "/path/to/corpus" \
  --api-base "http://model-server.example/llm/v1" \
  --out "/path/to/.pastiche-private/profile"
```

- `/path/to/corpus` はTXTファイルが入ったフォルダです（UTF-8またはCP932）。
- APIのベースURLをそのまま指定します。`/llm/v1` のようなプレフィックスも保持します。スクリプトが `/chat/completions` を補います。
- `~/.pi/agent/models.json` に同じベースURLのモデルが1つあれば自動選択します。それ以外では `/models` から調べます。複数モデルがあれば `--model MODEL_ID` を指定してください。
- 接続先が別のPCでも利用できます。**そのサーバーには物語解析用の本文チャンクと、短い語形の評価候補が送られます。** HTTP自体は暗号化されないので、送信先とネットワークを確認してください。
- `--check` は合成データのみで接続テストし、コーパスを開きません。
- 標準モード `fast` は一部の作品・場面から構造を探索するため、従来の全チャンク二重解析よりLLM呼び出しを減らせます。実際の所要時間・作風再現性はモデルと資料に依存します。

### 語彙解析のPython依存関係は初回実行時に自動導入

**手動の `pip install` は不要です。** `npx skills add` はSkillファイルを配置するため、Pythonパッケージを自動実行するインストールフックはありません。代わりに `scripts/run.py` が初めてコーパスを解析するとき、必要なら `fugashi-plus[unidic-lite]` を専用Python仮想環境（ユーザーキャッシュの `~/.cache/pastiche-skills/`）にインストールして、その環境で処理を継続します。既に正常に導入されていればインストールを省略し、2回目以降は環境を再利用します。

`fugashi-plus` は本家 `fugashi` の互換forkで、`import fugashi` は変わりません。両者は同じPythonパッケージ名を使うため、同じ環境への重複インストールを避け、専用仮想環境に分離します。これは辞書の扱いや環境互換性の改善であり、解析速度の向上を保証するものではありません。

形態素解析により、出現語形すべての頻度・作品数を `lexical_inventory.jsonl` に保存できます。セットアップ中のネットワークアクセスはPythonパッケージの取得用であり、小説本文を送るものではありません。実際のモデル解析時には、指定したモデルサーバーに必要な本文チャンクが送信されます。

パッケージを自動導入できないオフライン環境や、導入を望まない場合は `--no-auto-install --morphology none` で記号・顔文字・反復・表記揺れのみの分析を実行できます。その場合は全語彙一覧を生成しません。依存関係のインストールは最初の一回だけ通信が必要です。

## パイプライン

| 工程 | 解析すること | モデル呼び出し |
| --- | --- | --- |
| 1. 文体統計 | 文長、会話率、改行、句読点、文字種、接続表現 | 0 |
| 2. 語彙・表記 | 全語彙、短い連語、文末、`♡♡`、`♪♪`、顔文字、`っ/ッ`、指定した表記ペア | 0 |
| 3. 語彙の言語感覚評価 | 短い語形が一般的な日本語として「普通・目立つ・珍しい」か、何の効果を持つか | デフォルト96候補を24件ずつ、最大約4回 |
| 4. ナラティブ探索 | 選んだ場面の自由記述の構成技法、再出現した候補の証拠検査 | デフォルト最大36回の探索＋最大16回の検証 |
| 5. 執筆ガイド統合 | 全部を `style_bible.md` に結合 | 0 |

工程3の「一般的な日本語らしさ」は **LLMの事前学習による言語感覚の推定** です。日本語社会全体の実測頻度でも、作者固有性の証明でもありません。作品内出現回数・複数作品での再出現はPythonで独立に計測します。対照コーパスを持たなくてもモデル評価は使えますが、真の相対頻度が必要なら `--reference` が使えます。

記号や表記揺れは **Unicodeを正規化せず** に計測します。したがって `♡` と `♥`、`来たっ！` と `来たッ！`、`じゃねえか` と `じゃあねえか` を区別します。デフォルトの比較ペアに加え、`--variants variants.json` で `[ ["表記A", "表記B"], ... ]` の形で比較対象を増やせます。比較は各表現の完全一致によるものです。

## 主な生成ファイル

```text
.pastiche-private/profile/
├── style_profile.json               # 文長・句読点などの数値
├── lexical_profile.json             # 記号、表記揺れ、語彙・連語の分布
├── lexical_inventory.jsonl          # 辞書による全出現語形（fugashi-plus導入時のみ）
├── lexical_candidates.json          # モデル評価に回す短い表現
├── lexical_judgments.json           # LLMの一般的な語感による判定
├── lexical_bible.md                 # 実測値だけの書き方ガイド
├── lexical_interpretation.md        # LLMによる語感評価、利用できたときのみ
├── narrative_rules.json            # 構造規則の仮説・証拠ID
├── narrative_audit.json            # 呼び出し件数・検証件数
├── style_bible.md                  # 統合ガイド
├── narrative_progress.json         # 進捗
└── *_checkpoint.sqlite3            # 中断時に再開するためのデータ
```

**注意：** `lexical_inventory.jsonl` や候補JSONには原文に登場する短い表現が含まれます。外部公開・Gitコミットはしないでください。出力先はGit管理外を推奨します。

## 速度と精度を調整する

LLMのリクエストは既定で逐次実行（`--llm-workers 1`）です。複数リクエストを同時処理できるモデルサーバーの場合、`--llm-workers N` で語彙の語感評価・fastモードのナラティブ探索・候補再検証を並列化できます。各段階の同時リクエスト数は最大 `N` です（段階間の同時実行は行いません）。

```bash
# 最大4つのLLMリクエストを同時実行
python3 -u skills/pastiche-profile/scripts/run.py /path/to/corpus \
  --api-base http://model-server.example/llm/v1 \
  --llm-workers 4
```

- 推奨は `--llm-workers 2` から開始して、モデルサーバーの実際の並列処理性能とメモリ消費を確認しながら増やすことです。クライアント側の並列化だけでは、サーバー内部の同時推論数は増えません。
- チェックポイントの読み書きはメインスレッドだけで実行し、途中から再開できます。応答の完了順が変わっても、プロファイルの統合順序は元の入力順のままです。
- 従来の `--mode full` のナラティブ抽出部分は逐次実行のままです（語彙評価のみ `--llm-workers` 対応）。`--mode none` ではLLMを呼ばないため、この指定の効果はありません。
- 並列化は呼び出し回数や料金を減らすものではありません。サーバーの並列容量が不足すると、かえって遅くなる場合があります。

```bash
# Pythonだけで全作品の文体・記号・語彙を分析（モデル不要）
python3 skills/pastiche-profile/scripts/run.py /path/to/corpus --mode none

# より多くの作品を調べる（品質を追求、速度は落ちる）
python3 -u skills/pastiche-profile/scripts/run.py /path/to/corpus \
  --api-base http://model-server.example/llm/v1 \
  --narrative-works 30 --chunks-per-work 3

# 旧方式：すべてのチャンクで事象グラフの抽出と検証（最も重い）
python3 -u skills/pastiche-profile/scripts/run.py /path/to/corpus \
  --api-base http://model-server.example/llm/v1 --mode full

# 語彙の言語感覚判定は不要なら省く（語彙統計は維持）
python3 -u skills/pastiche-profile/scripts/run.py /path/to/corpus \
  --api-base http://model-server.example/llm/v1 --no-lexical-review

# すべての適格な語形候補をLLMに評価させる（大量の呼び出しに注意）
python3 -u skills/pastiche-profile/scripts/run.py /path/to/corpus \
  --api-base http://model-server.example/llm/v1 --lexical-review-limit 0
```

通常のナラティブ高速モードは最大18作品、各2チャンクをサンプリングし、複数作品に現れた候補にだけ再検証を行います。固定13種類のOperationへの押し込みをやめ、抽象的な構成技法を自由記述で発見します。複数の異なる規則が似た文章で表される場合、単純な類似文字列のクラスタリングが失敗することがあります。また、標本抽出で稀な技法や長距離の伏線を見落とす可能性があります。**推論回数の削減と解析精度の維持は別問題**です。

中断したら**同じコマンドで再実行**してください。処理済みチャンクと語彙評価バッチはSQLiteに残ります。`--restart` を指定したときのみ対応チェックポイントを破棄します。`--timeout 0` が既定で、モデルの待機時間上限は設けません（実行環境側の制限とは別）。15秒ごとに状態を報告します。


## 執筆後の表記・語彙チェック（pastiche-write）

`pastiche-write` は統合済みの `style_bible.md` から文体・表記・物語構造を参照します。追加依存は不要で、次のコマンドは**元コーパスを読まず**に新しい原稿と `lexical_profile.json` を比較します。

```bash
python3 skills/pastiche-write/scripts/evaluate_lexical.py \
  --profile /path/to/.pastiche-private/profile/lexical_profile.json \
  --draft /path/to/new-draft.txt \
  --out /path/to/.pastiche-private/write_lexical_review.json
```

記号（♡・♪・☆など）の頻度と会話内の位置、連打長、混合記号列、顔文字候補、表記ペア（「じゃねえか／じゃあねえか」「来たっ！／来たッ！」）、短い文末表現を比較します。数値は文脈を考えて解釈するための参考で、機械的な置換や回数の強制には使いません。

## Agent Skillsでの利用

```bash
npx skills add zkamiyama/pastiche-skills
```

> pastiche-profileを使って `/path/to/corpus` の作風プロファイルを作って。APIベースURLは `http://model-server.example/llm/v1`。表記、語彙の一般的な語感、ナラティブの共通パターンまで分析して。

> pastiche-writeを使って `style_bible.md` に従い、指定した新規プロットの短編を書いて。実測の語彙・表記傾向も、内容に合う範囲で反映して。

**Agentはインストール済みのスクリプトを実行し、エラーを解消するために勝手に編集しないでください。** APIベースURLは `run.py` にそのまま渡します。接続の問題を勝手なモデル名やパス変更で回避せず、診断情報と修正候補を示してください。コードの修正は、この公開リポジトリ側でテストしてから配布します。

研究と限界：[docs/research.md](docs/research.md) · [Lexical Method](skills/pastiche-profile/references/METHOD.md) · [Narrative Method](skills/pastiche-profile/references/NARRATIVE_METHOD.md)。合成データによるテスト：`python3 -m unittest discover -s tests -v`。

MIT License ([LICENSE](LICENSE))。
