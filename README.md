# pastiche-skills

日本語TXTコーパスから **文体・情感・全文の物語構造・出典付き人物グラフ**を作り、新しい題材の執筆へつなぐ Agent Skills。Python 3.10以降。原文をリポジトリへ置く必要はありません。

## Pythonが参照を管理する文体解析

文体・語彙の新規LLM要求からIDの採番・転記・所属一覧の返却を外しました。LLMは短い説明文と最小限の判定を返し、Pythonが要求対象・根拠照合・グループ所属・参照IDを管理します。短い別名IDへの置換や、誤記の推測補正ではありません。

既存の検証済み観察・語彙キャッシュは再利用できます。分類方式が変わるため、クラスタリング以降は新しいキャッシュ領域で再計算します。比較要求は増えますが、上限と要求単位の再利用があります。`--restart`は不要です。全文物語・人物・世界観・執筆計画の別経路は今回の変更対象外です。[仕様・境界・移行](docs/PYTHON_OWNED_STYLE.md)。

## v3: 認識・信念と実行方式

世界状態と人物の信念を分離しました。未観測の出来事で人物の信念を更新せず、情報を「聞いた」と「信じた」も区別します。読者への開示は提示順で管理し、内面・台詞には人物別の制限コンテキストを渡せます。宣言された観測・知識を機械検証し、本文との一致は追加のLLM監査で確認します。完全な心の理論の実装ではありません。

`--executor subagent --task-dir /private/tasks` で、HTTP不要のホスト・サブエージェント連携も利用できます。コードが子エージェントを直接起動するのではなく、終了コード75でprivateな作業を受け渡し、結果登録後に再開します。

[設計・コマンド・v3への移行](docs/PERSPECTIVES_AND_EXECUTORS.md)。旧 `fast/full` コードは削除し、人間のレビューを持つDBは維持しています。

## v3.1: 厳密性と不具合修正

認識状態を独立した有限モデルと照合し、時系列・誤信念・観測・伝聞・忘却・視点分離を検査しています。人物用コンテキストは場面開始前に限定し、物語パターンの出典更新、推論中の訂正、競合するサブエージェント回答、JSONの曖昧さ、生成打ち切りも検査します。全プログラムやLLMの読解精度を形式証明したものではありません。[検証仕様と再現コマンド](docs/CORRECTNESS_AUDIT.md)。

## v3.2: Writeの文体Refine

生成稿の文体を根拠付きで診断し、必要な箇所だけ改稿して、人物の認識・世界設定を再監査します。匿名A/B比較を提示順を入れ替えて2回行い、改善が明確でない場合は元稿を保持します。既定1回・最大2回。原稿の上書きはせず、差分・採否・ハッシュを保存します。HTTP/Ollamaとホストサブエージェントの両方に対応。[実行方法と限界](docs/STYLE_REFINE.md)。

## 旧ID形式のクラスタリング診断

旧方式の `STYLE_CLUSTER invented/duplicated IDs or invalid label` は過去の診断記録です。新しい文体経路はIDをモデルに返させません。旧復旧処理では、原因を区別し、厳密な検証を維持したまま既定で最大2回の訂正要求を行います。**同じ出力先・設定で、`--restart` を付けずに再実行**すると、完了済みの観察キャッシュを再利用します。インストール済みSkillも更新してください。[復旧手順・診断・制約](docs/STYLE_CLUSTER_RECOVERY.md)。

## 観察（STYLE_OBSERVE）で停止した場合

`STYLE_OBSERVE returned no valid evidence-grounded observations` では、説明の長さ・原文との一致・根拠IDなどの棄却理由を記録し、同じ検証基準のまま要求を再生成します。通信・JSON・観察検証を合わせて既定で最大3回です。診断は `<out>/style_observe_diagnostics/`。**成功済みキャッシュを維持し、`--restart` なしで再開**できます。全件棄却を空の成功に置き換えません。[復旧手順と診断コード](docs/STYLE_OBSERVE_RECOVERY.md)。

## 原文再確認で `bounded_description_required` が出た場合

補助説明 `reason` に未告知の300文字上限が適用される問題を修正しました。説明文は段落を含む自由記述として受け付け、根拠判定は従来どおり別に検証します。型・長さのエラーはフィールドと実測値を診断し、訂正要求へ渡します。今回の互換修正では完了済みキャッシュを再利用でき、`--restart` は不要です。[説明文の契約と再開](docs/PYTHON_OWNED_STYLE.md#explanatory-prose-is-not-a-compact-artifact-field)。

## 始め方

```bash
npx skills add zkamiyama/pastiche-skills

python3 -u skills/pastiche-profile/scripts/run.py /path/to/corpus \
  --api-base http://model-server.example/llm/v1 --model model-placeholder \
  --out /path/to/.pastiche-private/profile \
  --canon-scope work:original --llm-workers 4
```

**標準のLLMモードは、選択した作品の全文をチャンクに分けて指定モデルサーバーへ送信します。** 文体用のサンプル原文と短い語形も送ります。ローカルのファイルを使うことと、ローカルで推論することは別です。接続先と送信範囲を確認してください。HTTPは暗号化されません。認証には `--api-key-env` で環境変数名を指定し、認証つきリモート接続はHTTPSを使います。

- TXTはUTF-8またはCP932。出力はGit管理外の非公開フォルダを推奨します。
- APIベースURLの `/llm/v1` 等の接頭辞を保持し、`/chat/completions` を補います。Ollamaの `/api/generate` にも対応します。
- HTTP実行時、モデル名省略時はPi設定またはサーバーの `/models` から検出。複数候補なら `--model` で選択してください。
- `--check` は合成JSONだけで通信確認し、コーパスを読みません。
- 初回は必要に応じて `fugashi-plus[unidic-lite]` を専用キャッシュvenvに導入。オフライン等では `--no-auto-install --morphology none` で全文語彙一覧なしの計測ができます。

## Reasoning / Thinking は既定でOFF

HTTP解析は `--reasoning-effort none` が既定です。OpenAI互換APIへ
`reasoning_effort: "none"` と `chat_template_kwargs: {"enable_thinking": false}` を送り、
Ollamaには `think: false` を送ります。必要な場合だけ `low` / `medium` / `high` を明示し、
`auto` はこれらの指定を送らずサーバー既定へ戻します。モデル・サーバーの対応が必要です。

テンプレート拡張を受け付けないOpenAI互換APIには `--no-thinking-template` で拡張だけを省略できます。
profile・plan・audit・refine・researchで共通です。ホストLLM／サブエージェントの推論は変更しません。
**推論設定ごとにキャッシュを分離するため、旧版から既定OFFへ変えるとLLM段階は再計算されます。**
既存キャッシュ・原文・人間のレビューは削除しません。詳細: [Reasoning controls](docs/REASONING_CONTROL.md)。

## 文体のサンプリングと全文の構造解析を分離

| 経路 | 読む範囲 | 処理 |
| --- | --- | --- |
| 決定論的計測 | 全適格作品の全文、ローカル | 文長、表記、記号、反復、語形・連語の頻度 |
| 文体・情感 | 全選択作品から最大2チャンクずつ | 語彙・情感・語り口・台詞・感覚・緩急・局所技法をLLMで観察、統合、再検証 |
| 物語構造・人物 | **全選択作品の全チャンク** | 出来事と人物状態 → 順序を保つ階層構造 → 作品全体の展開 → 作品横断の構造候補 |
| 人間による検証 | 自動採用された出典付き主張 | 必要なものだけ使用停止・訂正・却下。根拠ハイライト、確認済み判定、履歴、人物統合・解除 |
| 執筆 | 抽象的構造と自動採用・人間修正済みの人物情報 | 階層計画、人物・読者の知識状態、場面執筆、新稿監査 |

既定の `--analysis-works 0` は全作品を対象にします。91作品・630チャンクなら文体側は最大182チャンク、物語側は全630チャンクを読解対象にします。`--chunks-per-work` は文体経路だけに作用し、全文経路を切り詰めません。全文を入力したことは、重要イベントや因果を正しく理解した証明ではありません。

## ローカルのグラフ検証エディタ

```bash
python3 skills/pastiche-profile/scripts/workbench.py serve \
  --out /path/to/.pastiche-private/profile
```

起動時に表示されるセッショントークン付きのローカルURLをブラウザで開きます。URLは他人に共有しないでください。

左に主張の検証キュー、中央に人物グラフ、右に原文根拠と編集履歴を表示します。ノードのドラッグ、ズーム、作品版・提示位置フィルタ、口調・価値観・外見などの属性表示、根拠付き手動追加、人物統合・解除、利用可能な主張の出力（自動採用を含む）に対応します。物語タブは階層構造・出来事・出典を確認する閲覧中心の機能です。

出典付き人物情報は既定で `auto_accepted` として執筆用に出力できますが、モデルによる解釈が自動で真実になるわけではありません。明示・伝聞・推定を分け、人間が個別に使用停止・訂正・却下・確認済み判定を行います。原文のハッシュが変わった主張は古い承認を執筆側へ渡しません。再解析で人間の訂正・却下を上書きしません。作品や版をまたぐ同名人物の自動統合は行いません。

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
| `character-research` | ホストのWeb検索で原典や参照リンクを芋づる式に調査し、許可ドメイン内の公開HTML・テキストを取得して出典付きの自動採用候補を作る |
| `pastiche-graph-review` | エディタ上で出典・作品版・時点を確認し、必要な主張を使用停止・訂正・却下。自動採用を含めて利用可能な情報を出力 |
| `pastiche-llm-worker` | ホストのサブエージェントへprivateなJSONタスクを委譲・取り込み・再開 |
| `pastiche-write` | 抽象的な構造、文体ガイド、自動採用・人間修正済み人物制約から新作を計画・執筆・監査 |

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
├── llm_progress.json
└── style_bible.md                  # 統合ガイド
```

人間の全件承認は不要です。`writer_context.json` と `character_bible.md` には、出典が現行で、使用停止・却下・保留になっていない自動採用の主張も含みます。人物名、短い語形、要約、登録したローカルパス、Web取得資料が含まれるため、分析成果物も非公開で保管してください。

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

`--mode fast/full` はv3で削除しました。現在の `llm` とローカル計測の `none` のみを提供します。`--no-lexical-review` は短い語形の一般語感判定だけを省略します。詳しい構造解析・調査・執筆コマンドは [技術仕様](docs/STORY_CHARACTER_WORKBENCH.md) を参照してください。

**World Knowledge Graphを追加しました。** 場所・組織・国家・時代・文化・技術／魔法・資源を、条件・例外・出典付きの主張として人物・出来事に接続します。自動採用が既定で、人間は必要な設定だけ訂正・使用停止できます。詳細は [World Knowledge Graph](docs/WORLD_KNOWLEDGE_GRAPH.md)。

各段階の成功結果はSQLiteへ保存します。中断時は同じコマンドを再実行してください。`--restart` はモデルタスクのキャッシュを再計算する指定で、人間の承認・訂正履歴を消す指示ではありません。`--timeout 0` は既定で無制限のソケット待機です。ハートビートは完了時間の保証ではありません。

## 品質の限界と検証

モデルの心理解釈、因果、情感、典型構造は仮説です。根拠IDの存在だけでは解釈の正しさを証明しません。構造候補の支持数は調べた提案メンバーに対する数であり、全作品の出現率ではありません。同一シリーズの作品は独立標本とも限りません。

この実装は特定論文やベンチマークの再現ではありません。コーパスでの品質向上は、独立した人手確認、新しい題材での生成、条件を隠した比較評価が必要です。原作の長い文章・固有の筋書きを転用せず、既存キャラクターを使用する場合も作品版と時点を指定してください。

```bash
python3 -m unittest discover -s tests -v
```

合成データの回帰テスト、OS/Python別CI、テスト専用Playwrightによるブラウザ確認を含みます。実行環境にPlaywrightを追加する必要はありません。

設計・検証・セキュリティ: [STORY_CHARACTER_WORKBENCH.md](docs/STORY_CHARACTER_WORKBENCH.md)。文体経路: [LLM Style Method](skills/pastiche-profile/references/LLM_STYLE_METHOD.md)。MIT License。

## 世界観グラフ（2.1）

標準の全文読解で `world_graph.json` / `world_context.json` / `world_bible.md` / `world_audit.json` も生成します。エディタの「世界観」タブで種別・条件・例外・出典・矛盾候補を確認できます。

```bash
python3 skills/pastiche-profile/scripts/workbench.py serve --out /private/profile
python3 skills/pastiche-profile/scripts/workbench.py export \
  --out /private/profile --canon-scope novel:original \
  --character-mode original --world-mode canon
```

`--world-mode original`（既定）は新しい世界、`canon` は指定版の世界設定を利用します。人物モードとは独立です。`world-research` Skillでは公式資料を芋づる式に追い、同じグラフへ構造化できます。出典・版・時点が合わない設定や使用停止済みの主張を執筆へ渡しません。

既存のDBは継続利用できます。抽出プロンプトの変更に伴い、関連するLLM処理は更新時に再実行されます。未変更の人物ID・主張ID・人間の判断は保持します。世界観の全矛盾を解決できるわけではなく、文脈・条件・語り手の確認は必要です。

### 執筆用要約の平文生成

未完了の執筆用要約はLLMが平文だけを生成し、Pythonが項目名とパターン参照を付与します。
`summary` / `writing_use` の転記やJSONキー生成は要求しません。既存の成功キャッシュは維持し、
新規の文章は項目ごとに再開できます。詳細は [平文要約](docs/PLAIN_TEXT_SYNTHESIS.md)。

## 読めるBibleと語彙の改善

括弧文の顔文字抽出は廃止しました。語彙候補は基本形と実在する連語に分け、表記・記号統計とは混ぜません。短い用例を参照した用法説明と、根拠とは区別した新規の説明例を付けます。本文の匿名ID表示は作品数の説明に置き換え、追跡用IDはJSONに保持します。[仕様と限界](docs/GUIDE_QUALITY.md)。

## 特徴語の探索と執筆への反映

語彙の既定レビューは固定96件ではなく`--lexical-review-limit -1`の適応予算。Pythonが全語彙を測定し、比較頻度・未知語/表記変種・連語・低頻度語・反復語を別経路で選ぶ。LLMは選ばれた語の実際の文脈を確認する。`lexical_repertoire.json`を場面別パレットへ絞り、元の表記と使い分けを新稿へ渡す。[方法・予算・制約](docs/LEXICAL_DISCOVERY.md)。
