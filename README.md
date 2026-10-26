# pastiche-skills

**小説の文体・作風を計量分析し、抽象的な作風プロファイルを使って別の内容の文章を執筆する Agent Skills。** 日本語TXTコーパスを想定。Python 3.10+、初期状態では追加依存なし。原文の分析はローカルで完結し、LLMや外部APIは使いません。

本リポジトリには、作品本文・作者固有の実例・R-18文章は**一切入れていません**。公開GitHubリポジトリにコーパスをアップロードしないでください。

## 2つのSkill

| Skill | 自然言語での呼び出し例 | 動作 |
| --- | --- | --- |
| [`pastiche-profile`](skills/pastiche-profile/SKILL.md) | 「pastiche-skillsを使って、`/path/to/corpus` から作風プロファイルを作って」 | ローカルTXTを計量分析 → `style_profile.json`・`style_bible.md`・人手注釈テンプレートを作る |
| [`pastiche-write`](skills/pastiche-write/SKILL.md) | 「この `style_bible.md` を使い、廃駅で手紙を見つけるという内容の短編を書いて」 | 原案 → 因果・人物状態を設計 → 執筆 → 整合性・文体の改稿 |

## インストール

Agent Skills対応環境なら、リポジトリからSkillsを追加する（環境によって導入操作が異なります）。例：

```bash
npx skills add zkamiyama/pastiche-skills
```

または `skills/pastiche-profile` と `skills/pastiche-write` を、エージェントが読み取るSkillsディレクトリにコピーしてください。`SKILL.md` はAgent Skills仕様準拠で、両Skillが個別に検出されます。CLIは前者に同梱されているため、**Profile Skill単体でも分析できます**。

## 最初に手元のPCで行う（原文は一切アップロードしない）

```bash
# 作品ごとの UTF-8 または CP932 の .txt をフォルダに配置
# /path/to/corpus はGitHubリポジトリの外側を推奨
python3 skills/pastiche-profile/scripts/pastiche.py profile \
  /path/to/corpus --out /path/to/.pastiche-private/profile
```

生成物は3ファイル（原文やファイル名は含まれません）。

```text
.pastiche-private/profile/
  style_profile.json     ← 作品ごとの文長や会話率などの集約数値
  style_bible.md         ← 他のLLMに渡せる抽象的な文体ルール
  narrative_rules.json   ← 論理・心理・筋立ての人手検証用テンプレート
```

原文の閲覧が許されないクラウド型エージェントでも、**これら数値・抽象規則の出力ファイルだけを扱う**設計です。ただし、数値や人手注釈が絶対に機密でないとは限りません。送信先と共有可否を確認してください。

### オプション

```bash
# 同ジャンルの別作者を対照コーパスとして比較（差分は記述統計）
python3 skills/pastiche-profile/scripts/pastiche.py profile \
  /path/to/corpus --reference /path/to/control --out .pastiche-private/profile

# ローカルで形態素解析も追加（初回の依存インストールのみ通信あり）
python3 -m pip install 'fugashi[unidic-lite]'
python3 skills/pastiche-profile/scripts/pastiche.py profile \
  /path/to/corpus --morphology fugashi --out .pastiche-private/profile
```

`narrative_rules.json` の `rules` は初期状態で空。作者固有の「認識反転」「人物の選択」「葛藤を作る条件」等は**頻度表から確定できません**。原文を手元で読んだ人が、具体的な作品本文を転記せず、匿名の証拠IDとともに機構を記録します。人手で検証したものだけを執筆ルールに入れます。

```json
{
  "schema": "pastiche.narrative.v1",
  "rules": [
    {
      "status": "verified",
      "trigger": "登場人物の前提が行動の結果と食い違うとき",
      "mechanism": "矛盾への合理化を一度挟み、追加の結果を提示して前提を問い直す",
      "effect": "読者の認識も段階的に更新される",
      "evidence_ids": ["W01", "W04"]
    }
  ]
}
```

この内容は**説明用の架空の例**であり、特定の作者についての分析結果ではありません。編集後は：

```bash
python3 skills/pastiche-profile/scripts/pastiche.py guide \
  --profile .pastiche-private/profile/style_profile.json \
  --notes .pastiche-private/profile/narrative_rules.json \
  --out .pastiche-private/profile/style_bible.md
```

## 生成原稿のローカル検証

```bash
python3 skills/pastiche-profile/scripts/pastiche.py evaluate \
  --profile .pastiche-private/profile/style_profile.json \
  --draft /path/to/new-draft.txt \
  --out .pastiche-private/evaluation.json

# 取得許可のある原文と完全一致する長い文字列を検査する（原文は送信・出力しない）
python3 skills/pastiche-profile/scripts/pastiche.py evaluate \
  --profile .pastiche-private/profile/style_profile.json \
  --draft /path/to/new-draft.txt --corpus /path/to/corpus \
  --overlap-chars 40 --out .pastiche-private/evaluation.json
```

数値で測れるのは表面の文体だけ。評価結果は文学的な良し悪し、同一作者らしさ、著作権適法性、独創性の証明ではありません。`--corpus` は空白を取り除いた40文字以上の完全一致をスクリーニングしますが、パラフレーズを検知せず、短い共通言い回しで誤検出する場合もあります。

## 安全設計と制約

- 生コーパスに対する `cat`・読込・引用・送信をSkillsは要求しません。Pythonコード内で数値化し、集約値のみ出力します。
- 実行環境そのものがクラウドであれば、そこにコーパスを置く段階で外部送信になります。**ローカルPCで解析コマンドを実行してから**、許可できる抽象的な出力だけをエージェントに渡してください。
- 自動判定する談話マーカーは心理・論理的因果関係を意味しません。未知の特性を作者の癖として捏造しないでください。
- 特定作者の個性的な文章、台詞、人物、固有設定、筋書きをコピーしない。権利者の許諾やサービス規約もユーザー側で確認してください。
- 成人向け作品の規約上の閲覧制限や、生成先LLMのコンテンツ制約を回避するものではありません。
- GitHubのActionsでは合成テストデータしか処理しません。

## 研究の出典と検証

[研究マップ・一次資料・設計への対応・限界](docs/research.md) を参照。運用を固定するため、実装では **指標の数値化 → 検証済み構造規則 → 計画/執筆/改稿 → 数値・人手評価** を分離しています。

```bash
python3 -m unittest discover -s tests -v
```

MIT License（既存の `LICENSE` に従う）。
