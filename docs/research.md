# 研究マップ（2026-10-26時点）

## 問いと証拠の扱い

この調査は、`imozuru` 式に「一次資料と実装」の二系列から始め、元論文・調査・実装のドキュメントを辿った。以下は全文再配布ではなく **資料のURL・確認できた主張・実装判断**のトレーサビリティ表。研究成果を完全再現したと主張しない。

調査した問い：①仕様書をどのエージェントが使えるか、②日本語の個人文体を本文なしで測れるか、③語彙頻度から物語の因果や作風をどこまで推論できるか、④LLMによる模倣の限界は何か、⑤生成/評価は何を分離すべきか、⑥R-18原文を外部転送せず実行可能か。

## 参照資料（元文献・一次情報）

| 対象 | 原著・公式資料 | 抽出した根拠と採用判断 |
|---|---|---|
| Agent Skills仕様 | [Agent Skills Specification](https://agentskills.io/specification) | `SKILL.md` frontmatter・`scripts/`・`references/`・段階的読込を満たすよう実装。各Skill単体で検出される。 |
| 日本語の文体 | [Huang & Jin (2020), 機能フレーズ](https://doi.org/10.2964/jsik_2020_035) | 日本語20著者400作品を比較。機能フレーズ等が著者識別で有効であることを示す。助詞/句読点などの表層代理指標を採用。元論文の識別器は再現していない。 |
| 頻出語の著者差 | [Burrows (2002), Delta](https://doi.org/10.1093/llc/17.3.267) | 頻出語の相対頻度による著者差を検討。ただしGenre/時代に依存しうる。本実装はDeltaを名乗らず、単なる集約値を出す。 |
| 識別できる＝模倣できる？ | [Wang et al. (2025), EMNLP](https://aclanthology.org/2025.findings-emnlp.532/) | 少数の参考例では暗黙的な個人の文体を再現しにくい。統計的近接を模倣成功と取り違えない。 |
| 自然言語のスタイル指示 | [Kim & Jurgens (2026), ACL](https://aclanthology.org/2026.findings-acl.2039/) | 文体を自然言語のstyle-eliciting promptへ翻訳する研究。Style Bibleを明示指示に落とす。ただし本実装は学習済みdecoderを使用せず、性能の再現を主張しない。 |
| 構成→本文→改稿 | [Yang et al. (2022), Re3](https://aclanthology.org/2022.emnlp-main.296/) | 全体計画と現在の物語状態を使う反復生成、整合性改稿。書くSkillの複数段階に着想を得た。 |
| 詳細アウトライン＋記憶 | [Wang et al. (2025), DOME](https://aclanthology.org/2025.naacl-long.63/) | 動的アウトライン・時間的な事実管理で長編の整合性を改善。シーンごとの状態表を採用。 |
| イベントと登場人物 | [Li et al. (2025), STORYTELLER](https://aclanthology.org/2025.findings-acl.1071/) | SVOベースの出来事表現と知識グラフによる一貫性の試み。最小限の因果/人物状態チェックリストに簡約。 |
| 評価の多軸性 | [Briakou et al. (2021), GEM](https://aclanthology.org/2021.gem-1.6/) | Style・意味保持・流暢性を分け、人手評価の標準化に課題。単一のstyle scoreを出さず、複数指標で観測値を返す。 |
| 自動指標の限界 | [Pauli et al. (2025), EMNLP](https://aclanthology.org/2025.findings-emnlp.1175/) | 既存の文体転送指標と人間評価の相関の過信を警告。自動数値に「作者らしさ点」等のラベルを与えない。 |
| 形態素解析実装 | [fugashi / polm](https://github.com/polm/fugashi) | MeCab Python wrapper とUniDic導入方法を参照。オプション依存にしてネット接続なしでも中核機能を使えるようにした。 |

## 調査経路と不確実性

- `stylometry / function phrase` → Huang & Jinの原著 → 機能フレーズ、形態素、品詞n-gramという候補。コンテンツ依存の語彙を外部に出さずに扱うため、本文から抽出するのは表層の数値に限定。
- `individual style imitation` → Wangらの大規模評価 → Kim & Jurgensの明示的スタイル指示研究。少数shotでの再現性に限界があり、自然言語化も原研究のモデルをそのまま再現しない限り性能を保証できない。
- `long-form plot coherence` → Re3 → DOME / STORYTELLER。計画・状態追跡・改稿という共通原理は採用、専用モデル/知識グラフは未実装。
- `text style transfer evaluation` → GEM survey → 2025 meta-evaluation。自動指標を文学的品質や意味保存の代理にしない。
- `Agent Skills` → 公式spec → self-contained Skill + progressive disclosure。実行に必要なスクリプトをprofile Skill配下に同梱。

**未検証・未実装：** 実コーパスでの文体再現度、人間によるブラインド比較、R-18専用評価、ジャンルごとの実証的特徴抽出、マルチモデルでの同等出力保証、文学的な因果構造の自動理解、外部APIと接続しないことの第三者監査。すべてを完成済みの学術再現実装として扱わない。

**安全境界：** 研究用のWeb検索にコーパス内容は使わず、GitHubには原文を含めない。ローカル実行コードはネットワークAPI呼出や外部モデル接続を持たず、標準出力も集約値とパスだけに限定する。

---

## 2026-10-26 追加調査：LLMによるナラティブルールの自動帰納（v0.2）

### 新しい論点と一次文献

手動の `narrative_rules.json` 作成を自動化するため、**(1) 人物に沿った事象列、(2) 意図・結果と因果の区別、(3) 作品固有から汎用条件への一般化、(4) 原文根拠と複数作品の検証、(5) 個人作風の移転可能性と限界**の5論点を検討した。原文の取得・公開はこのリポジトリでは行っていない。次の文献は、直接の根拠と採用の限界を突き合わせたもの。

| 論点 | 原著 | 採用/非採用 |
| --- | --- | --- |
| 事象連鎖 | [Chambers & Jurafsky, 2008: Narrative Event Chains](https://aclanthology.org/P08-1090/) と [2009: Narrative Schemas and Participants](https://aclanthology.org/P09-1068/) | 事象・参与者を結びつける表現のみ。論文の教師なし帰納と時間分類器を再実装していない。 |
| 人物の行動・内面 | [Elson, *Modeling Narrative Discourse*, Story Intention Graphs](https://www.cs.columbia.edu/~delson/pubs/Modeling-Narrative-Discourse_Elson_R4.pdf); [Event2Mind (ACL 2018)](https://aclanthology.org/P18-1043/) | 出来事と認識／意図を別々に符号化。完全なSIGではなく短いチャンクの有限タグで代用。 |
| 具体→一般化 | [GLUCOSE (EMNLP 2020)](https://aclanthology.org/2020.emnlp-main.370/) | 文脈に依存する因果説明と一般的ルールを区別。抽出した本文表現をそのまま一般化しない安全な固定テンプレート採用。GLUCOSE全十次元は再現していない。 |
| 因果と談話 | [RST Introduction (Mann & Thompson, 1988)](https://www.sfu.ca/rst/01intro/intro.html); [Sun et al., 2024: Event Causality](https://aclanthology.org/2024.naacl-long.191/) | 局所LLMによる時系列-only棄却と因果／意図／談話の判定。因果同定の精度は未測定。 |
| 人物状態と長期依存 | [OpenToM (ACL 2024)](https://aclanthology.org/2024.acl-long.466/); [BookSum (2021)](https://arxiv.org/abs/2105.08209) | 人物知識状態の確認を生成Skillに追加。チャンクを跨ぐ長期依存は未解決と明記。 |
| 生成と独立評価 | [Re3 (EMNLP 2022)](https://aclanthology.org/2022.emnlp-main.296/); [DOC (ACL 2023)](https://aclanthology.org/2023.acl-long.190/); [Tian et al. (EMNLP 2024)](https://aclanthology.org/2024.emnlp-main.978/) | プラン→本文→因果/文体修正。専門モデルや論文の人手評価の性能改善をこのSkillの精度として転用しない。 |
| ローカルAPI | [Ollama API](https://github.com/ollama/ollama/blob/main/docs/api.md); [llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) | ローカルhostのみ・ストリーミングなし・JSON出力。汎用クラウドOpenAIエンドポイントを受け付けない。 |

### 実装における判断と未解決

- コーパスをクラウドAgentへ渡す設計はやめ、**ローカルLLMに対するローカルPythonからのリクエスト**へ限定。公開GitHubにはコード・研究URL・合成単体テストだけ。
- 根拠付きタグ抽出→同じ原文を使った別指示の再判定→作品横断で同型パターンを統合。出力文章は原文やモデル自由記述ではなく、タグに対する固定テンプレートから生成。
- `model_supported` は **自動検証フロー上の状態**。真の作者性、客観的因果、異ジャンルでの再現率とは別問題。モデルの自己検証は独立アノテーションではない。最低2作品という数は実装用閾値であり論文が有効性を保証したものではない。
- 反例探索と作品間の非独立性推定、文脈を跨ぐ人物同一性・伏線検出、タグ自体の網羅性は未解決。タグ付き `narrative_rules.json` を不変の真理として扱わない。
- 実装手順・具体的な出力仕様・失敗時の処理は [skills/pastiche-profile/references/NARRATIVE_METHOD.md](../skills/pastiche-profile/references/NARRATIVE_METHOD.md) を参照。
