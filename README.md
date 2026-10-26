# pastiche-skills

**日本語の文章コーパスから文体・物語構造を分析し、再利用可能な執筆ガイドを作る Agent Skills。**

`pastiche-profile` は文章の統計的特徴と、繰り返し現れる物語上の構成パターンを抽出します。`pastiche-write` は作成したガイドを使って、新しい題材の構成・執筆・改稿を進めます。

処理はローカル実行を基本とします。コーパス、原文の抜粋、作品名をこのリポジトリに格納する必要はありません。Python 3.10以上に対応し、文体統計は標準ライブラリだけで利用できます。物語構造の意味解析にはローカルLLMが必要です。

## Skills

| Skill | 用途 | 主な成果物 |
| --- | --- | --- |
| [`pastiche-profile`](skills/pastiche-profile/SKILL.md) | 文体を計量分析し、作品横断の構成パターンを抽出する | `style_profile.json`, `narrative_rules.json`, `style_bible.md` |
| [`pastiche-write`](skills/pastiche-write/SKILL.md) | 作風プロファイルに従い、新規の文章を設計・執筆・改稿する | 新規原稿と任意の評価レポート |

自然言語での依頼例：

> pastiche-profileを使って、`/path/to/corpus` のTXTから作風プロファイルを作ってください。文体の統計と、繰り返し現れるナラティブルールを抽出してください。

> pastiche-writeを使って、作成した `style_bible.md` に従い、「廃駅で見つけた手紙によって主人公の認識が変わる」という短編を書いてください。

## 導入

Agent Skillsに対応した環境で、次のコマンドまたは対応するSkill管理機能を利用してください。

```bash
npx skills add zkamiyama/pastiche-skills
```

または `skills/pastiche-profile` と `skills/pastiche-write` を利用環境のSkillsディレクトリに配置します。CLIは前者の `scripts/` に含まれます。

## CLIで作風プロファイルを生成する

### 1. 文体の計量分析（LLM不要）

TXTファイルを一作品につき一ファイルで用意し、ローカルPCで実行します。入力はUTF-8 / CP932を受け付けます。

```bash
python3 skills/pastiche-profile/scripts/pastiche.py profile \
  /path/to/corpus --out /path/to/.pastiche-private/profile
```

オプションで同ジャンルの対照コーパスを比較できます。

```bash
python3 skills/pastiche-profile/scripts/pastiche.py profile \
  /path/to/corpus --reference /path/to/comparison \
  --out /path/to/.pastiche-private/profile
```

形態素解析を追加する場合：

```bash
python3 -m pip install 'fugashi[unidic-lite]'
python3 skills/pastiche-profile/scripts/pastiche.py profile \
  /path/to/corpus --morphology fugashi \
  --out /path/to/.pastiche-private/profile
```

### 2. ナラティブルールの抽出（ローカルLLM）

Ollamaなどのローカル推論環境に、日本語文章を解析できるモデルを用意します。モデルの導入や起動は、各推論環境の手順に従ってください。

```bash
python3 skills/pastiche-profile/scripts/narrative.py \
  /path/to/corpus \
  --backend ollama --model YOUR_LOCAL_MODEL \
  --out /path/to/.pastiche-private/profile \
  --min-works 2
```

`llama.cpp` などのOpenAI互換ローカルサーバーも使用できます。

```bash
python3 skills/pastiche-profile/scripts/narrative.py \
  /path/to/corpus \
  --backend openai-compatible \
  --endpoint http://127.0.0.1:8080/v1/chat/completions \
  --model YOUR_LOCAL_MODEL \
  --out /path/to/.pastiche-private/profile
```

通信先は同じPCのloopbackアドレスに限定しています。リモートのモデルAPIには接続しません。実行環境がクラウドの場合、`localhost` は利用者のPCではないため、コーパスのあるPC上でコマンドを実行してください。

### 3. 文体と物語構造を一つのガイドにまとめる

```bash
python3 skills/pastiche-profile/scripts/pastiche.py guide \
  --profile /path/to/.pastiche-private/profile/style_profile.json \
  --notes /path/to/.pastiche-private/profile/narrative_rules.json \
  --out /path/to/.pastiche-private/profile/style_bible.md
```

出力例：

```text
.pastiche-private/profile/
├── style_profile.json    # 文体の集約統計
├── narrative_rules.json  # 証拠IDと支持件数を付けた構成パターン
├── narrative_audit.json  # 自動抽出・検証処理の件数
└── style_bible.md        # 生成時に使う文体・構成ガイド
```

`narrative_rules.json` は人手入力しなくても生成できます。抽出処理は事象と人物の認識・判断を分け、別プロンプトによる証拠照合の後、複数作品に現れる共通の `trigger → operation → effect` をまとめます。二段階のモデル判定と作品間の反復は、文学的な解釈の正しさを保証するものではありません。

**ルールの扱い**：`model_supported` はローカルモデルによる二段階の照合を通過した候補、`hypothesis` は支持例が十分でない候補です。執筆ガイドには前者だけを採用します。`--min-works` は統計的な有意性ではなく、反復の最低作品数を指定します。

## 生成原稿のローカル評価

```bash
python3 skills/pastiche-profile/scripts/pastiche.py evaluate \
  --profile /path/to/.pastiche-private/profile/style_profile.json \
  --draft /path/to/new-draft.txt \
  --out /path/to/.pastiche-private/evaluation.json
```

原文へのアクセス許可がある場合、長い完全一致箇所の有無をローカルで検査できます。照合箇所の文章はレポートに含みません。

```bash
python3 skills/pastiche-profile/scripts/pastiche.py evaluate \
  --profile /path/to/.pastiche-private/profile/style_profile.json \
  --draft /path/to/new-draft.txt --corpus /path/to/corpus \
  --overlap-chars 40 --out /path/to/.pastiche-private/evaluation.json
```

一致検査は一定長以上の完全一致を探すためのもので、言い換えや物語展開の類似を評価するものではありません。文体統計も作品の品質・作者同定・独創性の証明にはなりません。

## データ管理の方針

- **コーパスはローカルに置く**：公開リポジトリにはプログラム、実行手順、合成テストだけを含めます。
- **生成物を分離する**：レポートやプロファイルは `.pastiche-private/` などのGit管理外の場所に保存します。抽象的なプロファイルも共有前に内容を確認してください。
- **解析と執筆を分離する**：原文を読むのはローカルPythonとローカル推論サーバーのみ。執筆エージェントには抽象化済みの成果物だけを渡します。
- **根拠を示す**：因果・人物心理の判定には誤りがあり得ます。モデルの一致を独立した学術的検証とみなしません。
- **元作品との境界を保つ**：人物名・固有設定・台詞・文章の転用を避け、データの使用許可や各サービスの利用条件を確認してください。

## 研究資料とテスト

手法の参考文献と適用範囲は [docs/research.md](docs/research.md)、因果抽出・一般化の具体的手順は [NARRATIVE_METHOD.md](skills/pastiche-profile/references/NARRATIVE_METHOD.md) を参照してください。論文の専用モデルをそのまま再実装したものではありません。

```bash
python3 -m unittest discover -s tests -v
```

テストは合成文章と模擬LLM応答を使用します。実際の文章群に対する人手評価や、モデル間での同等の再現性はまだ測定していません。

MIT License（[LICENSE](LICENSE)）。
