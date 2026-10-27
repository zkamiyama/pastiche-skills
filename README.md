# pastiche-skills

**日本語の文章コーパスから文体・物語構造を分析し、再利用可能な執筆ガイドを作る Agent Skills。**

`pastiche-profile` は文章の統計的特徴と、繰り返し現れる物語上の構成パターンを抽出します。`pastiche-write` は作成したガイドを使って、新しい題材の構成・執筆・改稿を進めます。

文章の統計処理はローカルで完結します。物語構造の解析では、利用者が指定したOllama/OpenAI互換モデルサーバーへ作品のチャンクを送信します。モデルサーバーは同じPCでも、LAN内の別マシンでも、外部のHTTPSホストでも構いません。原文を本リポジトリへ格納する必要はありません。Python 3.10以上で利用できます。

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

### 2. ナラティブルールの抽出（指定したモデルサーバー）

日本語文章を解析できるモデルをOllama等で用意します。初期状態ではPC内のOllamaに接続します。**チャンク処理は自動的に途中保存されるので、失敗時には同じコマンドで再開できます。**

```bash
python3 skills/pastiche-profile/scripts/narrative.py \
  /path/to/corpus \
  --backend ollama --model YOUR_LOCAL_MODEL \
  --out /path/to/.pastiche-private/profile \
  --min-works 2
```

`llama.cpp` 等のOpenAI互換サーバーも使用できます。

```bash
python3 skills/pastiche-profile/scripts/narrative.py \
  /path/to/corpus \
  --backend openai-compatible \
  --endpoint http://127.0.0.1:8080/v1/chat/completions \
  --model YOUR_LOCAL_MODEL \
  --out /path/to/.pastiche-private/profile
```

### 接続先を別のマシンにする

LAN内のOllamaサーバーへの接続（**プライベートIPのHTTP**）：

```bash
python3 -u skills/pastiche-profile/scripts/narrative.py /path/to/corpus \
  --backend ollama --model MODEL_NAME \
  --endpoint http://192.168.1.20:11434/api/generate \
  --allow-remote --out /path/to/.pastiche-private/profile
```

**外部HTTPS**のOpenAI互換エンドポイント（APIキーは環境変数から渡す）：

```bash
python3 -u skills/pastiche-profile/scripts/narrative.py /path/to/corpus \
  --backend openai-compatible --model MODEL_NAME \
  --endpoint https://inference.example.com/v1/chat/completions \
  --allow-remote --api-key-env MODEL_API_KEY \
  --out /path/to/.pastiche-private/profile
```

外部IP・ホストへ接続する場合は、**原文の一部をそのモデルサーバーへ送信します**。送信を許可できることを確認して `--allow-remote` を指定します。パブリックホストへの暗号化されていないHTTPは通常拒否し、明示的に許可するなら `--allow-insecure-http` を追加します（非推奨）。リダイレクトと環境プロキシは無効、HTTPSの証明書検証は有効です。

### 長時間処理・進捗・中断再開

- **既定の `--timeout 0` はタイムアウト無制限**（ソケットの読み取り待ち）。固まった場合は `Ctrl+C` で中断し、そのまま再実行できます。上限が必要なら `--timeout 1200` 等を指定します。
- **ストリーミングと15秒ごとの進捗表示**が既定で有効。生成中の文章は表示しません。非対応サーバーは `--no-stream` を使用します。`--progress-interval 5` などに変更できます。
- **SQLiteによるチャンク単位のチェックポイント**を使用します。抽出・検証が終わった部分だけが再利用されるので、前回 `W0001 chunk 3` で止まってもチャンク1・2に戻りません。再実行時には入力本文・モデル・接続先・解析バージョンの一致も検証します。全件再処理は `--restart`。
- **失敗したリクエストを2回まで再試行**。回数は `--retries`、待機は `--retry-delay` で調整できます。恒久的な認証・指定エラーは再試行しません。
- `narrative_progress.json` は別プロセスから状態・現在の作品/チャンク・完了数を確認できます。標準エラーの進捗も確認してください。

```text
.pastiche-private/profile/
├── narrative_checkpoint.sqlite3  # 抽出・検証段階の復帰データ
├── narrative_progress.json       # 現在の状態（running/completed/failed/interrupted）
├── narrative_rules.json
└── narrative_audit.json
```

エージェントや端末ツール自体の実行時間制限は、`--timeout` と別です。端末セッションが切れても、同じコマンドで継続できます。モデルサーバーと通信できても、推論の遅延以外にネットワークの停止などが原因となる場合があります。

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

- **コーパスを公開しない**：公開リポジトリにはプログラム、実行手順、合成テストだけを含めます。
- **生成物を分離する**：レポートやプロファイルは `.pastiche-private/` などのGit管理外の場所に保存します。抽象的なプロファイルも共有前に内容を確認してください。
- **解析と執筆を分離する**：原文を読むのはPython処理と、ユーザーが接続を選択した推論サーバー。執筆エージェントには抽象化済みの成果物だけを渡します。
- **根拠を示す**：因果・人物心理の判定には誤りがあり得ます。モデルの一致を独立した学術的検証とみなしません。
- **元作品との境界を保つ**：人物名・固有設定・台詞・文章の転用を避け、データの使用許可や各サービスの利用条件を確認してください。

## 研究資料とテスト

手法の参考文献と適用範囲は [docs/research.md](docs/research.md)、因果抽出・一般化の具体的手順は [NARRATIVE_METHOD.md](skills/pastiche-profile/references/NARRATIVE_METHOD.md) を参照してください。論文の専用モデルをそのまま再実装したものではありません。

```bash
python3 -m unittest discover -s tests -v
```

テストは合成文章と模擬LLM応答を使用します。実際の文章群に対する人手評価や、モデル間での同等の再現性はまだ測定していません。

MIT License（[LICENSE](LICENSE)）。
