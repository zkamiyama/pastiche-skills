# pastiche-skills

日本語TXTコーパスから文体統計・ナラティブルールを抽出し、新しい文章向けの執筆ガイドを作るAgent Skills。

## はじめかた（1コマンド）

Python 3.10以降。コーパスは**1作品1TXT**として手元のフォルダに用意してください。

```bash
python3 -u skills/pastiche-profile/scripts/run.py /path/to/corpus \
  --api-base http://192.168.1.20/llm/v1
```

このコマンドが **文体の計量分析 → モデルで構造抽出 → 執筆ガイド作成** を順番に行います。
APIの**ベースURL**だけ指定すれば、`/chat/completions` の付加、OpenAI互換/Ollamaの判別、モデルIDの選択を自動化します。

モデルIDは、該当URLと一致する `~/.pi/agent/models.json` を優先し、そこに無ければサーバーの `/models` を確認します。候補が複数の場合だけ `--model MODEL_ID` を指定。モデルIDを推測しないでください。

**`--allow-remote`、`--allow-insecure-http` の指定は不要**です。指定したURLを送信先として扱います。本文チャンクがそのサーバーに送信されるため、接続先を確認してください。HTTP自体は暗号化されません。

`--out /path/to/profile` で保存先変更可能。既定は `.pastiche-private/profile`。処理中に進捗を表示し、チャンクごとのチェックポイントがあるので、途中で中断しても**同じコマンドを再実行**すれば続きから処理します。

### 接続だけ確認したい

```bash
python3 skills/pastiche-profile/scripts/run.py /path/to/corpus \
  --api-base http://192.168.1.20/llm/v1 --check
```

`--check` は合成JSONを問い合わせ、**コーパスを読み込みません**。

## Agent Skillsから使う

```bash
npx skills add zkamiyama/pastiche-skills
```

> pastiche-profileを使って、指定したTXTフォルダから作風プロファイルを作ってください。APIベースURLは `http://192.168.1.20/llm/v1`。結果は `.pastiche-private/profile` に保存。

Skillは `scripts/run.py` を直接実行します。三つの処理を人間に逐一指示させたり、モデル名を推測したりする必要はありません。

## 出力

```text
.pastiche-private/profile/
├── style_profile.json           # 文体統計
├── narrative_rules.json         # 作品横断の抽象ルール
├── narrative_audit.json         # 解析・検証件数
├── style_bible.md               # 執筆時に使用するガイド
├── narrative_progress.json      # 進捗
└── narrative_checkpoint.sqlite3 # 中断再開
```

ルールにはモデル自身による二段階の照合を使います。`model_supported` は人手による検証済みという意味ではありません。人物や筋書きなど、原文独自の表現を再利用しないための確認も別途必要です。

## トラブルシューティング

- `model ID unavailable`：`--model 実際のモデルID` を付けるか、Piの `models.json` の `baseUrl` を確認。
- `HTTP 404`：APIベースURLを確認。リバースプロキシの `/llm/` のようなサブパスは削除しない。
- `HTTP 401/403`：必要ならAPIキーを環境変数に入れ、`--api-key-env ENV_NAME` を指定（リモートへキーを送るときはHTTPS）。
- ストリーミングに非対応：`--no-stream`。
- 応答に時間がかかる：既定の `--timeout 0` は読み取り待ち無制限。進捗JSONを確認し、必要なら再実行。

## 詳細・研究

文体統計だけなら `python3 skills/pastiche-profile/scripts/pastiche.py profile /path/to/corpus --out .pastiche-private/profile`。
詳細は [pastiche-profile](skills/pastiche-profile/SKILL.md)、[pastiche-write](skills/pastiche-write/SKILL.md)、[構造抽出方法](skills/pastiche-profile/references/NARRATIVE_METHOD.md)、[研究資料](docs/research.md)。
テストは `python3 -m unittest discover -s tests -v`。
MIT License。
