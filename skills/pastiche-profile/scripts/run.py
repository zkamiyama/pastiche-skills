#!/usr/bin/env python3
"""Single-entrypoint corpus profiler: statistics + exact lexical forms + sampled narratives."""
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
    parser = argparse.ArgumentParser(description="作風プロファイルを作成: 統計+表記+選択的構造抽出")
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--api-base", "--endpoint")
    parser.add_argument("--backend", choices=("auto","ollama","openai-compatible"), default="auto")
    parser.add_argument("--model", default=os.environ.get("PASTICHE_LOCAL_MODEL"))
    parser.add_argument("--out", type=Path, default=Path(".pastiche-private/profile"))
    parser.add_argument("--mode", choices=("fast","full","none"), default="fast",
                        help="fast=sampled open discovery (default); full=legacy exhaustive; none=local analysis only")
    parser.add_argument("--timeout", type=int, default=0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--chunk-chars", type=int, default=3800)
    parser.add_argument("--max-works", type=int, default=0,help="legacy full mode: maximum works (0 means all)")
    parser.add_argument("--narrative-works", type=int, default=18,help="fast mode: spread sample over up to N works; 0 means all")
    parser.add_argument("--chunks-per-work", type=int, default=2,help="fast mode: 2 (default) means two phases per work")
    parser.add_argument("--verify-groups", type=int, default=8)
    parser.add_argument("--max-output-tokens", type=int, default=0)
    parser.add_argument("--min-works", type=int, default=2)
    parser.add_argument("--progress-interval", type=float, default=15)
    parser.add_argument("--variants",type=Path,help="exact variant pairs: JSON array of [A,B]")
    parser.add_argument("--morphology",choices=("auto","none","fugashi"),default="auto")
    parser.add_argument("--reference",type=Path,help="genre-matched reference TXT corpus for descriptive lexical contrast")
    parser.add_argument("--include-words-in-guide",action="store_true",help="also include frequent short lexical forms in private guide")
    parser.add_argument("--lexical-review-limit",type=int,default=96,
                        help="model-judged typicality candidates (default 96, 0=all qualifying; can be very slow)")
    parser.add_argument("--lexical-batch-size",type=int,default=24)
    parser.add_argument("--no-lexical-review",action="store_true",
                        help="skip model language-impression review; exact statistics still run")
    parser.add_argument("--api-key-env")
    parser.add_argument("--no-stream", action="store_true")
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--check", action="store_true", help="synthetic API check only, without reading corpus")
    args = parser.parse_args(argv)
    try:
        if args.lexical_review_limit<0 or not 1<=args.lexical_batch_size<=48:
            raise ValueError("--lexical-review-limit >=0 and --lexical-batch-size 1..48 required")
        if args.mode!="none" or args.check:
            backend,endpoint,base,models_url=resolve_url(args.api_base,args.backend)
            name=model_id(args.model,base,backend,models_url)
            parsed=urllib.parse.urlsplit(endpoint)
            flags=[]
            if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
                flags.append("--allow-remote")
                if parsed.scheme=="http":
                    flags.append("--allow-insecure-http")
                    print("[pastiche] Note: HTTP is not encrypted; use a trusted network.",file=sys.stderr,flush=True)
                print("[pastiche] Source chunks will be sent to the selected model server.",file=sys.stderr,flush=True)
            cli=[sys.executable,"-u",str(HERE/("narrative_fast.py" if args.mode=="fast" else "narrative.py")),
                 str(args.corpus),"--out",str(args.out),"--backend",backend,"--endpoint",endpoint,
                 "--model",name,"--timeout",str(args.timeout),"--retries",str(args.retries),
                 "--chunk-chars",str(args.chunk_chars),"--progress-interval",str(args.progress_interval),
                 "--min-works",str(args.min_works),*flags]
            if args.mode=="fast":
                cli += ["--narrative-works",str(args.narrative_works),
                        "--chunks-per-work",str(args.chunks_per_work),
                        "--verify-groups",str(args.verify_groups),
                        "--max-output-tokens",str(args.max_output_tokens)]
            else:
                cli += ["--max-works",str(args.max_works)]
            if args.api_key_env:
                cli += ["--api-key-env", args.api_key_env]
            if args.no_stream:
                cli += ["--no-stream"]
            if args.restart:
                cli += ["--restart"]
            print(f"[pastiche] backend={backend} model={name} endpoint={endpoint}",flush=True)
            if args.check:
                test_model=narrative.LocalModel(backend,name,endpoint,args.timeout,
                                                allow_remote=True,allow_insecure_http=True,
                                                api_key_env=args.api_key_env,stream=False)
                test_model.ask('Reply ONLY with a JSON object containing {"ok":true}.')
                print("Connection OK (synthetic request; corpus not read).",flush=True)
                return 0
        args.out.mkdir(parents=True,exist_ok=True)
        print("[1/5] Basic stylometry (local, no LLM)",flush=True)
        cmd=[sys.executable,str(HERE/"pastiche.py"),"profile",str(args.corpus),"--out",str(args.out)]
        if args.reference:
            cmd += ["--reference",str(args.reference)]
        r=subprocess.run(cmd)
        if r.returncode:
            return r.returncode
        print("[2/5] Exact typography and complete lexical inventory (local, no LLM)",flush=True)
        lex=[sys.executable,str(HERE/"lexical.py"),str(args.corpus),"--out",str(args.out),
             "--morphology",args.morphology,"--candidate-limit",str(args.lexical_review_limit)]
        if args.reference:
            lex += ["--reference",str(args.reference)]
        if args.variants:
            lex += ["--variants",str(args.variants)]
        if args.include_words_in_guide:
            lex += ["--include-words-in-guide"]
        r=subprocess.run(lex)
        if r.returncode:
            return r.returncode
        model_review_ok=False
        if args.mode != 'none' and not args.no_lexical_review:
            print("[3/5] Lexical typicality: model estimates ordinary vs marked expression",flush=True)
            review=[sys.executable,"-u",str(HERE/"lexical_review.py"),
                    "--candidates",str(args.out/"lexical_candidates.json"),
                    "--out",str(args.out),"--backend",backend,"--endpoint",endpoint,
                    "--model",name,"--timeout",str(args.timeout),"--retries",str(args.retries),
                    "--progress-interval",str(args.progress_interval),
                    "--batch-size",str(args.lexical_batch_size),*flags]
            if args.api_key_env:
                review.extend(["--api-key-env",args.api_key_env])
            if args.no_stream:
                review.append("--no-stream")
            if args.restart:
                review.append("--restart")
            result=subprocess.run(review)
            model_review_ok=result.returncode==0
            if not model_review_ok:
                print("[pastiche] lexical review unavailable; continuing with measured lexical statistics. "
                      "Check lexical_review_checkpoint.sqlite3 before retrying.",file=sys.stderr,flush=True)
        else:
            print("[3/5] Lexical model impressions skipped; all measured typography retained",flush=True)
        if args.mode!="none":
            print(f"[4/5] Narrative discovery ({args.mode}, resumable)",flush=True)
            r=subprocess.run(cli)
            if r.returncode:
                print("Completed chunks have a checkpoint. Re-run the same command to resume.",file=sys.stderr,flush=True)
                return r.returncode
        else:
            print("[4/5] Narrative discovery skipped (local-only mode)",flush=True)
        print("[5/5] Build combined style guide",flush=True)
        guide=[sys.executable,str(HERE/"pastiche.py"),"guide", "--profile",str(args.out/"style_profile.json"),
               "--notes",str(args.out/"narrative_rules.json"),
               "--lexical",str(args.out/"lexical_bible.md"),
               "--out",str(args.out/"style_bible.md")]
        if model_review_ok and (args.out/"lexical_interpretation.md").exists():
            guide += ["--lexical-review",str(args.out/"lexical_interpretation.md")]
        return subprocess.run(guide).returncode
    except (OSError,ValueError,TypeError) as exc:
        print(f"error: {exc}",file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
