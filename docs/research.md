# Research and implementation notes

本資料は、`pastiche-skills` の設計に用いた文献・技術仕様と、それぞれの適用範囲を記録する。研究結果をそのまま再現したとみなしたり、特定のモデルでの性能を保証したりするものではない。

## 調査の論点

1. Agent Skills形式で、異なるエージェント間に再利用できる作業手順をどう記述するか。
2. 日本語文章の文体を、個々の作品内容に依存せずどこまで定量化できるか。
3. 事象、人物の意図、因果、談話関係をどう区別するか。
4. 個別の物語から、別の題材にも適用できる構成規則をどう一般化するか。
5. モデルによる検証と、人手・独立検証をどう区別するか。
6. 原文のローカル処理と、抽象化済みの生成ガイドをどう分離するか。

## 1. 文体分析・執筆制御

| 原著・仕様 | 設計への反映 | 再現していないもの |
| --- | --- | --- |
| [Agent Skills Specification](https://agentskills.io/specification) | `SKILL.md`、段階的な `references/`、同梱CLI | 特定のエージェントへの実行保証 |
| [Huang & Jin (2020), 日本語の機能フレーズ](https://doi.org/10.2964/jsik_2020_035) | 品詞・助詞と文体の関連に着目 | 原著の著者識別器 |
| [Burrows (2002), Delta](https://doi.org/10.1093/llc/17.3.267) | 頻度特性の作品間差に着目 | Delta手法そのもの |
| [Wang et al. (2025), individual style imitation](https://aclanthology.org/2025.findings-emnlp.532/) | 少数の参照例だけで個人文体を再現する限界に注意 | 著者識別不能な生成の保証 |
| [Kim & Jurgens (2026), style-eliciting prompts](https://aclanthology.org/2026.findings-acl.2039/) | 定量値を自然言語のStyle Bibleへ変換 | 専用の学習済みプロンプト変換器 |
| [Yang et al. (2022), Re3](https://aclanthology.org/2022.emnlp-main.296/) | 計画、草稿、事実整合性の確認を分離 | 論文のモデル学習と実験環境 |
| [Yang et al. (2023), DOC](https://aclanthology.org/2023.acl-long.190/) | アウトラインを先に固定し、文章化と改稿を分離 | DOCの専用制御モデル |
| [Wang et al. (2025), DOME](https://aclanthology.org/2025.naacl-long.63/) | 人物の知識・状態更新を意識した執筆 | 動的な知識グラフの再実装 |
| [GEM study (2021)](https://aclanthology.org/2021.gem-1.6/) | 文体・意味・読みやすさを別々に評価 | 単一数値での文学的品質の判定 |
| [Pauli et al. (2025), evaluation](https://aclanthology.org/2025.findings-emnlp.1175/) | 自動文体スコアの過信を避ける | 人間評価との相関の保証 |
| [fugashi](https://github.com/polm/fugashi) | 日本語形態素解析のオプション | 辞書なしの意味理解 |

集約統計は作品ごとの中央値を基準にし、長い一作品だけでプロファイルが決まらないようにする。文頭の逆接語・因果語の頻度は、文中の本当の因果関係や人物心理を確定する指標ではない。

## 2. 物語構造の符号化と一般化

| 原著 | 採用した着想 | 実装上の限界 |
| --- | --- | --- |
| [Chambers & Jurafsky (2008), Narrative Event Chains](https://aclanthology.org/P08-1090/) / [Schemas and Participants (2009)](https://aclanthology.org/P09-1068/) | 同じ参与者に結びつく事象と連鎖を捉える | 教師なし帰納や時間順序分類器は実装していない |
| [Elson, Story Intention Graphs](https://www.cs.columbia.edu/~delson/pubs/Modeling-Narrative-Discourse_Elson_R4.pdf) | 行為・動機・物語内の知識を分ける | 完全な意図グラフではない |
| [Rashkin et al. (2018), Event2Mind](https://aclanthology.org/P18-1043/) | 事象と人物の意図・反応を別フィールドとして扱う | 内面の推定精度は未測定 |
| [Mostafazadeh et al. (2020), GLUCOSE](https://aclanthology.org/2020.emnlp-main.370/) | 文脈依存の具体的な関係を、条件付きの一般規則に写す | 全10次元を網羅していない |
| [Mann & Thompson, RST](https://www.sfu.ca/rst/01intro/intro.html) | 対比・詳細化・因果と単なる時系列を区別する | 完全なRST木を作らない |
| [Sun et al. (2024), Event Causality](https://aclanthology.org/2024.naacl-long.191/) | 物語理解のために事象間の因果を明示する | LLMによる原因判定は誤り得る |
| [Xu et al. (2024), OpenToM](https://aclanthology.org/2024.acl-long.466/) | 人物ごとの認識と客観的な作中事実を区別する | 完全なbelief trackerは未実装 |
| [Kryściński et al. (2021), BookSum](https://arxiv.org/abs/2105.08209) | 長編を複数の粒度で扱う必要性 | 章を越えた伏線の追跡は弱い |

### 実装での因果推論の扱い

事象に `(kind, actor, evidence_ids)` を、事象間に `(from, to, relation, evidence_ids)` を割り当てる。`relation` は `causes/motivates/enables/blocks/contradicts/reveals/contrasts/elaborates/before_only` から選ぶ。`before_only` は因果を支持する証拠とみなさない。

候補の `trigger → operation → effect` は、別のローカルモデル呼び出しで証拠を再照合する。複数の作品で同型の三項組が繰り返された場合だけ `model_supported` とする。出力する説明文は固定辞書から生成し、個別の筋書きや引用を含めない。詳細な手順は [NARRATIVE_METHOD.md](../skills/pastiche-profile/references/NARRATIVE_METHOD.md) に記載した。

**評価上の注意**：同じモデルを2回呼び出しても、独立した第三者の検証にはならない。作品数の下限は実装上の条件であり、統計学上の有意性の保証ではない。シリーズや翻案が含まれる場合は、作品間の独立性も弱くなる。

## 3. ローカル推論とデータ境界

- 文体統計：Python標準ライブラリ。任意で [fugashi](https://github.com/polm/fugashi) を使う。
- ナラティブ解析：[Ollama API](https://github.com/ollama/ollama/blob/main/docs/api.md) または [llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) のローカルHTTPを使う。
- 推論サーバーのアドレスはloopbackのみ。HTTPプロキシやリダイレクトを使わず、外部のモデルAPIへのフォールバックもしない。
- 出力には匿名の証拠ID、定量値、固定タグ、固定テンプレートを使う。自由記述の分析結果・作品名・本文は出力しない。
- 推論結果は使用者のローカルディレクトリに置き、Git管理対象へ追加しない。

**未評価の項目**：実コーパス上での文体・因果抽出精度、モデル間の再現性、人間によるブラインド評価、物語全体をまたぐ構造の推定、表現の新規性、エンドツーエンドの外部監査。モデルが生成したルールを「作者固有の不変の規則」と断定しない。

初期調査：2026-10-26。本文に基づく機能検証は合成テストと模擬モデル応答に限定している。
