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
