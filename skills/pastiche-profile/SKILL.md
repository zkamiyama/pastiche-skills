---
name: pastiche-profile
description: >-
  TXTコーパスから文体統計・物語構造の抽象ルール・執筆ガイドを一括生成する。APIベースURLからモデル接続を設定し、進捗表示と中断再開に対応。
license: MIT
compatibility: Python 3.10+, OpenAI-compatible or Ollama model API.
---

# pastiche-profile

## 最初にやること

**通常は以下の1コマンドだけ実行する。** Skillディレクトリ内から実行するか、`run.py` の絶対パスを使う。

```bash
python3 -u scripts/run.py "/path/to/corpus" \
  --api-base "http://model-host.example/llm/v1" \
  --out ".pastiche-private/profile"
```

`/path/to/corpus` とAPI URLはユーザー提供の値に置き換える。API URLが無ければデフォルトのローカルOllamaを使う。実行コマンドのある場所がエージェントの端末ではない場合は、ユーザーのPCで実行できるコマンドを渡す。

- **モデルを適当に推測しない。** `run.py` がPiの `~/.pi/agent/models.json` にある同じ `baseUrl` のモデル、または接続先`/models`から決める。複数候補の場合だけモデルIDを追加で確認。
- **API URLのパスをいじらない。** `/llm/v1` のようなベースURLを受け付け、`/chat/completions` の補完を `run.py` が行う。サーバー固有のサブパスを勝手に削除しない。
- `--allow-remote` や `--allow-insecure-http` はユーザーに要求しない。`run.py` が通信先を明示し、必要な互換フラグを内部で処理する。
- ファイルの存在確認は `test -d`・`test -f` などで行う。シェルの組み込み `read` で`SKILL.md`を開こうとしない。データの中身やファイル名をログへ列挙しない。
- モデル接続だけを確認したければ`--check`。合成JSONだけで検証し、原文を読まず送信もしない。
- `--timeout 0` が既定で待機時間の上限なし。進捗を確認し、中断したら**同じコマンドで再開**する。エージェントの端末実行時間とモデル通信タイムアウトは異なる。
- コーパス本文はエージェントのモデルコンテキスト・GitHubへ渡さない。指定モデルサーバーには本文チャンクが送られる。信頼できる接続先を選び、HTTPの非暗号化を説明する。
- APIキーが必要なら`--api-key-env VARIABLE`を使用する。リモートへAPIキーを送る場合はHTTPS必須。

## 完了条件

`style_profile.json`、`narrative_rules.json`、`narrative_audit.json`、`style_bible.md` を確認し、`narrative_progress.json` の `state=completed` を確認する。処理中に作られた古いガイドを最新の結果と誤認しない。

**手法:** [references/NARRATIVE_METHOD.md](references/NARRATIVE_METHOD.md) を必ず参照する。2つ以上の事象と根拠を扱い、時系列と因果を区別する。別パスで根拠を再確認した候補を作品横断で一般化し、有限タグからテンプレート規則を構成する。`model_supported`はモデルによる判定で、人間による検証済みではない。

作風プロファイルを用いて新しい文章を書く場合は別Skill `pastiche-write` を使用する。
