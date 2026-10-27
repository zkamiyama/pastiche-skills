#!/usr/bin/env python3
"""One-command TXT profiling and guided narrative extraction."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.parse
import urllib.request

import narrative

HERE = Path(__file__).resolve().parent

def resolve_url(raw: str | None, backend: str):
    raw = raw or "http://127.0.0.1:11434/api/generate"
    parsed = urllib.parse.urlsplit(raw)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
        or parsed.username or parsed.password or parsed.query or parsed.fragment
        or any(c.isspace() for c in raw)):
        raise ValueError("APIベースURLに不正な指定があります")
    path = parsed.path.rstrip("/")
    if backend == "auto":
        backend = "ollama" if path.endswith("/api/generate") or (not path and parsed.port == 11434) else "openai-compatible"
    if backend == "ollama":
        if not path:
            path = "/api/generate"
        elif path.endswith("/api"):
            path += "/generate"
        elif not path.endswith("/api/generate"):
            raise ValueError("Ollama URLはサーバーのルートまたは/api/generateを指定")
        base_path = path[:-len("/api/generate")]
        model_path = base_path + "/api/tags"
    else:
        if not path:
            path = "/v1"
        if path.endswith("/v1"):
            path += "/chat/completions"
        elif not path.endswith("/v1/chat/completions"):
            raise ValueError("OpenAI互換APIのベースURLは/v1で終わる必要があります")
        model_path = path[:-len("/chat/completions")] + "/models"
    full = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    models_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, model_path, "", ""))
    base = full[:-len("/chat/completions")] if backend == "openai-compatible" else raw.rstrip("/")
    return backend, full, base, models_url

def model_id(manual: str | None, base: str, backend: str, models_url: str) -> str:
    if manual:
        return manual
    settings = Path.home() / ".pi" / "agent" / "models.json"
    candidates = []
    try:
        providers = json.loads(settings.read_text(encoding="utf-8")).get("providers", {})
        for provider in providers.values():
            if not isinstance(provider, dict) or provider.get("baseUrl", "").rstrip("/") != base.rstrip("/"):
                continue
            for item in provider.get("models", []):
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    candidates.append(item["id"])
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    candidates = sorted(set(candidates))
    if len(candidates) == 1:
        return candidates[0]
    # Discovery only retrieves model IDs, never corpus contents.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), narrative.NoRedirect())
    try:
        with opener.open(models_url, timeout=8) as response:
            data = json.loads(response.read(1_000_001))
        key = "models" if backend == "ollama" else "data"
        field = "name" if backend == "ollama" else "id"
        fetched = sorted(set(x[field] for x in data.get(key, [])
                             if isinstance(x, dict) and isinstance(x.get(field), str)))
        if len(fetched) == 1:
            return fetched[0]
        candidates = fetched or candidates
    except (OSError, ValueError, TypeError):
        pass
    if candidates:
        raise ValueError("複数モデルがあります。--model に次のいずれかを指定: " + ", ".join(candidates[:12]))
    raise ValueError("モデルIDを取得できませんでした。--model にサーバーの実際のモデルIDを指定してください")

def main(argv=None):
    parser = argparse.ArgumentParser(description="TXT作風プロファイルを1コマンドで作成")
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--api-base", "--endpoint")
    parser.add_argument("--backend", choices=("auto","ollama","openai-compatible"), default="auto")
    parser.add_argument("--model", default=os.environ.get("PASTICHE_LOCAL_MODEL"))
    parser.add_argument("--out", type=Path, default=Path(".pastiche-private/profile"))
    parser.add_argument("--timeout", type=int, default=0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--chunk-chars", type=int, default=3800)
    parser.add_argument("--max-works", type=int, default=0)
    parser.add_argument("--min-works", type=int, default=2)
    parser.add_argument("--progress-interval", type=float, default=15)
    parser.add_argument("--api-key-env")
    parser.add_argument("--no-stream", action="store_true")
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--check", action="store_true", help="原文を読まずに合成JSONで接続確認")
    args = parser.parse_args(argv)
    try:
        backend, endpoint, base, models_url = resolve_url(args.api_base, args.backend)
        name = model_id(args.model, base, backend, models_url)
        parsed = urllib.parse.urlsplit(endpoint)
        flags = []
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            flags.append("--allow-remote")
            if parsed.scheme == "http":
                flags.append("--allow-insecure-http")
                print("[pastiche] 注意: HTTPは単体では暗号化されません。",file=sys.stderr,flush=True)
            print("[pastiche] 指定サーバーへ本文チャンクを送信します。",file=sys.stderr,flush=True)
        cli = [sys.executable, "-u", str(HERE / "narrative.py"), str(args.corpus),
               "--out", str(args.out), "--backend", backend, "--endpoint", endpoint,
               "--model", name, "--timeout", str(args.timeout), "--retries", str(args.retries),
               "--chunk-chars", str(args.chunk_chars), "--max-works", str(args.max_works),
               "--min-works", str(args.min_works), "--progress-interval", str(args.progress_interval), *flags]
        if args.api_key_env:
            cli += ["--api-key-env", args.api_key_env]
        if args.no_stream:
            cli += ["--no-stream"]
        if args.restart:
            cli += ["--restart"]
        print(f"[pastiche] backend={backend} model={name} endpoint={endpoint}",flush=True)
        if args.check:
            # Synthetic query only; do not open the corpus.
            check_model = narrative.LocalModel(backend, name, endpoint, args.timeout,
                                               allow_remote=True, allow_insecure_http=True,
                                               api_key_env=args.api_key_env, stream=False)
            check_model.ask('Reply ONLY with a JSON object containing {"ok":true}.')
            print("Connection OK (synthetic request; corpus not read).",flush=True)
            return 0
        print("[1/3] 文体統計", flush=True)
        stats = subprocess.run([sys.executable, str(HERE / "pastiche.py"),"profile",str(args.corpus),"--out",str(args.out)])
        if stats.returncode:
            return stats.returncode
        print("[2/3] ナラティブルール抽出（再開対応）", flush=True)
        result = subprocess.run(cli)
        if result.returncode:
            print("終了した部分は保存済みです。同じコマンドで再開できます。",file=sys.stderr,flush=True)
            return result.returncode
        print("[3/3] 作風ガイド生成", flush=True)
        return subprocess.run([sys.executable,str(HERE / "pastiche.py"),"guide",
              "--profile",str(args.out / "style_profile.json"),"--notes",str(args.out / "narrative_rules.json"),
              "--out",str(args.out / "style_bible.md")]).returncode
    except (OSError,ValueError,TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
