# pastiche-skills

日本語TXTコーパスから **文体・情感・全文の物語構造・出典付き人物グラフ**を作り、新しい題材の執筆へつなぐ Agent Skills。Python 3.10以降。原文をリポジトリへ置く必要はありません。

## 始め方

```bash
npx skills add zkamiyama/pastiche-skills

python3 -u skills/pastiche-profile/scripts/run.py /path/to/corpus \
  --api-base http://model-server.example/llm/v1 --model el-local \
  --out /path/to/.pastiche-private/profile \
  --canon-scope work:original --llm-workers 4
```

**標準のLLMモードは、選択した作品の全文をチャンクに分けて指定モデルサーバーへ送信します。** 文体用のサンプル原文と短い語形も送ります。ローカルのファイルを使うことと、ローカルで推論することは別です。接続先と送信範囲を確認してください。HTTPは暗号化されません。認証には `--api-key-env` で環境変数名を指定し、認証つきリモート接続はHTTPSを使います。

- TXTはUTF-8またはCP932。出力はGit管理外の非公開フォルダを推奨します。
- APIベースURLの `/llm/v1` 等の接頭辞を保持し、`/chat/completions` を補います。Ollamaの `/api/generate` にも対応します。
- モデル名省略時はPi設定またはサーバーの `/models` から検出。複数候補なら `--model` で選択してください。
- `--check` は合成JSONだけで通信確認し、コーパスを読みません。
- 初回は必要に応じて `fugashi-plus[unidic-lite]` を専用キャッシュvenvに導入。オフライン等では `--no-auto-install --morphology none` で全文語彙一覧なしの計測ができます。

## 文体のサンプリングと全文の構造解析を分離

| 経路 | 読む範囲 | 処理 |
| --- | --- | --- |
| 決定論的計測 | 全適格作品の全文、ローカル | 文長、表記、記号、反復、語形・連語の頻度 |
| 文体・情感 | 全選択作品から最大2チャンクずつ | 語彙・情感・語り口・台詞・感覚・緩急・局所技法をLLMで観察、統合、再検証 |
| 物語構造・人物 | **全選択作品の全チャンク** | 出来事と人物状態 → 順序を保つ階層構造 → 作品全体の展開 → 作品横断の構造候補 |
| 人間による検証 | 選択した主張の登録原文 | 根拠ハイライト、承認・訂正・却下・保留、人物統合・解除 |
| 執筆 | 抽象的構造と承認済みの人物情報 | 階層計画、人物・読者の知識状態、場面執筆、新稿監査 |

既定の `--analysis-works 0` は全作品を対象にします。91作品・630チャンクなら文体側は最大182チャンク、物語側は全630チャンクを読解対象にします。`--chunks-per-work` は文体経路だけに作用し、全文経路を切り詰めません。全文を入力したことは、重要イベントや因果を正しく理解した証明ではありません。

## ローカルのグラフ検証エディタ

```bash
python3 skills/pastiche-profile/scripts/workbench.py serve \
  --out /path/to/.pastiche-private/profile
```

起動時に表示されるセッショントークン付きのローカルURLをブラウザで開きます。URLは他人に共有しないでください。

左に主張の検証キュー、中央に人物グラフ、右に原文根拠と編集履歴を表示します。ノードのドラッグ、ズーム、作品版・提示位置フィルタ、口調・価値観・外見などの属性表示、根拠付き手動追加、人物統合・解除、承認済みの出力に対応します。物語タブは階層構造・出来事・出典を確認する閲覧中心の機能です。

人物情報は最初から「事実」として確定せず、明示・伝聞・推定を分けた主張として保存します。原文のハッシュが変わった主張は古い承認を執筆側へ渡しません。再解析で人間の訂正・却下を上書きしません。作品や版をまたぐ同名人物の自動統合は行いません。

UIは同梱SVG/HTML/JavaScript、APIとDBはPython標準ライブラリとSQLite。**追加のTorch・ONNX・Neo4j・Node.jsは不要**です。実行時CDNや外部通信はなく、ループバック限定、セッショントークン、Host/Origin検査を備えます。公開サーバー向けの多人数アプリではありません。

```bash
# 原文・モデル・外部ネットワークなしで画面を試す
python3 skills/pastiche-profile/scripts/workbench.py demo --out /path/to/new-demo
python3 skills/pastiche-profile/scripts/workbench.py serve --out /path/to/new-demo
```

## Skillの役割

| Skill | 役割 |
| --- | --- |
| `pastiche-profile` | 文体、情感、全文の物語構造、人物主張を抽出 |
| `character-research` | ホストのWeb検索で原典や参照リンクを芋づる式に調査し、許可ドメイン内の公開HTML・テキストを取得して未確認の人物主張を作る |
| `pastiche-graph-review` | エディタ上で出典・作品版・時点を検証し、承認済みの情報を出力 |
| `pastiche-write` | 抽象的な構造、文体ガイド、承認済み人物制約から新作を計画・執筆・監査 |

新しい補助Skillは共通エンジンを使います。`pastiche-profile` も一緒にインストールしてください。別配置では `PASTICHE_PROFILE_SCRIPTS` をそのscriptsディレクトリに設定できます。

Web取得ヘルパー自体は検索エンジンではありません。種URL・許可ドメイン・ページ数・深さを明示し、検索と原典追跡はSkillを実行するエージェントが担当します。公式設定、制作者発言、ファン考察を分け、robots・アクセス間隔・公開範囲を尊重します。PDF・JavaScript実行・ログインやペイウォールの回避には対応しません。コーパス解析から自動でWeb検索することもありません。

## 主な出力

```text
profile/
├── style_profile.json              # 全文の文体統計
├── lexical_profile.json            # 表記・記号・語彙の実測
├── lexical_inventory.jsonl         # 全語形（辞書あり）
├── lexical_candidates.json
├── lexical_judgments.json          # 一般語感のモデル推定（成功時）
├── lexical_bible.md
├── qualitative_observations.json   # サンプル場面の抽象的観察
├── qualitative_patterns.json
├── qualitative_summary.json
├── qualitative_bible.md
├── narrative_rules.json            # 局所技法とその根拠
├── story_maps.jsonl                # 全文の出来事・階層・状態変化
├── plot_patterns.json              # 作品横断の抽象的構造候補
├── narrative_bible.md
├── character_graph.sqlite3         # 人物・主張・出典・人間の判断履歴
├── character_graph.json
├── story_audit.json
├── story_progress.json
├── style_llm_audit.json
├── narrative_audit.json
├── narrative_progress.json
└── style_bible.md                  # 統合ガイド
```

人間の承認後に `writer_context.json` と `character_bible.md` を出力します。人物名、短い語形、要約、登録したローカルパス、Web取得資料が含まれるため、分析成果物も非公開で保管してください。

## 処理量と再開

```bash
# 対象作品数を制限（選択された作品の全文は解析対象）
python3 -u skills/pastiche-profile/scripts/run.py /path/to/corpus \
  --api-base http://model-server.example/llm/v1 --analysis-works 30

# 従来の文体・情感分析だけにする
python3 -u skills/pastiche-profile/scripts/run.py /path/to/corpus \
  --api-base http://model-server.example/llm/v1 --skip-story

# LLMなしで計測
python3 skills/pastiche-profile/scripts/run.py /path/to/corpus --mode none
```

| 指定 | 既定 | 内容 |
| --- | ---: | --- |
| `--analysis-works` | 0 | 全適格作品。正の整数で作品数を制限 |
| `--chunks-per-work` | 2 | 文体観察用のチャンク数。全文経路には影響しない |
| `--chunk-chars` | 3800 | チャンクの目安文字数 |
| `--llm-workers` | 1 | 段階内の最大同時LLM要求数 |
| `--skip-story` | 無効 | 明示した場合だけ全文構造・人物経路を省略 |

`--mode fast/full` は旧方式の比較・互換用に残します。`--no-lexical-review` は短い語形の一般語感判定だけを省略します。詳しい構造解析・調査・執筆コマンドは [技術仕様](docs/STORY_CHARACTER_WORKBENCH.md) を参照してください。

各段階の成功結果はSQLiteへ保存します。中断時は同じコマンドを再実行してください。`--restart` はモデルタスクのキャッシュを再計算する指定で、人間の承認・訂正履歴を消す指示ではありません。`--timeout 0` は既定で無制限のソケット待機です。ハートビートは完了時間の保証ではありません。

## 品質の限界と検証

モデルの心理解釈、因果、情感、典型構造は仮説です。根拠IDの存在だけでは解釈の正しさを証明しません。構造候補の支持数は調べた提案メンバーに対する数であり、全作品の出現率ではありません。同一シリーズの作品は独立標本とも限りません。

この実装は特定論文やベンチマークの再現ではありません。コーパスでの品質向上は、独立した人手確認、新しい題材での生成、条件を隠した比較評価が必要です。原作の長い文章・固有の筋書きを転用せず、既存キャラクターを使用する場合も作品版と時点を指定してください。

```bash
python3 -m unittest discover -s tests -v
```

合成データの回帰テスト、OS/Python別CI、テスト専用Playwrightによるブラウザ確認を含みます。実行環境にPlaywrightを追加する必要はありません。

設計・検証・セキュリティ: [STORY_CHARACTER_WORKBENCH.md](docs/STORY_CHARACTER_WORKBENCH.md)。文体経路: [LLM Style Method](skills/pastiche-profile/references/LLM_STYLE_METHOD.md)。MIT License。
