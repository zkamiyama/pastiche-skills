#!/usr/bin/env python3
"""Offline, dependency-free Japanese prose style profiler and evaluator.

Reads raw TXT *only* in this process; never uses HTTP, subprocesses, or an LLM.
Reports contain aggregate numbers rather than corpus text, filenames or excerpts.
This is descriptive stylometry, not literary/causal comprehension.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

VERSION = "1.1.0"
SCHEMA = "pastiche.style.v1"
MARKERS = {
    "contrast": ("しかし", "だが", "ところが", "けれども", "それでも", "一方で", "とはいえ"),
    "cause": ("だから", "そのため", "したがって", "なぜなら", "結果として"),
    "reappraisal": ("実は", "つまり", "本当は", "今にして思えば", "振り返れば"),
    "time": ("やがて", "そのとき", "次の瞬間", "しばらくして", "翌朝", "それから"),
    "uncertainty": ("おそらく", "たぶん", "もしかすると", "どうやら"),
}
PUNCT = {"comma": "、", "period": "。", "ellipsis": "…", "dash": "―", "question": "？?", "exclamation": "！!"}
FUNCTION_WORDS = {"は", "が", "を", "に", "で", "と", "も", "へ", "の", "から", "まで", "より", "ね", "よ", "か", "だ", "です", "ます"}
PHASES = (("opening", 0, .20), ("middle", .20, .80), ("ending", .80, 1))
KEY_METRICS = ("p50_sentence_chars", "p90_sentence_chars", "dialogue_char_ratio", "comma_per_1000_chars", "ellipsis_per_1000_chars", "newline_per_1000_chars", "question_sentence_ratio")


def compact(text: str) -> str:
    return "".join(text.split())


def quantile(seq: list[float], p: float) -> float:
    if not seq:
        return 0.0
    a = sorted(seq)
    k = (len(a) - 1) * p
    lo = int(k)
    return round(a[lo] * (1 - (k - lo)) + a[min(len(a) - 1, lo + 1)] * (k - lo), 5)


def rate(n: float, d: float, scale: float = 1) -> float:
    return round(scale * n / d, 5) if d else 0.0


def sentences(text: str) -> list[str]:
    """Punctuation OR line ends are approximate boundaries (not syntax)."""
    pieces: list[str] = []
    for line in text.splitlines():
        pieces.extend(s.strip() for s in re.findall(r"[^。！？!?]+[。！？!?]?", line) if s.strip())
    return pieces


def quoted_ratio(text: str) -> float:
    # Nested Japanese quotes are approximated; non-paired quotes do not crash.
    depth, quoted, total = 0, 0, 0
    for c in text:
        if c.isspace():
            continue
        total += 1
        if c in "「『":
            depth += 1
        elif c in "」』":
            depth = max(0, depth - 1)
        elif depth:
            quoted += 1
    return rate(quoted, total)


def measure(text: str) -> dict[str, float]:
    clean = compact(text)
    lines = [s for s in text.splitlines() if s.strip()]
    sents = sentences(text)
    lengths = [float(len(compact(s))) for s in sents]
    markers: Counter[str] = Counter()
    for sent in sents:
        beginning = sent.lstrip(" \t\u3000「『（(")
        for key, options in MARKERS.items():
            if beginning.startswith(options):
                markers[key] += 1
    length = len(clean)
    stats: dict[str, float] = {
        "characters": float(length),
        "sentences": float(len(sents)),
        "mean_sentence_chars": round(statistics.mean(lengths), 5) if lengths else 0.,
        "p10_sentence_chars": quantile(lengths, .10),
        "p50_sentence_chars": quantile(lengths, .50),
        "p90_sentence_chars": quantile(lengths, .90),
        "p50_line_chars": quantile([float(len(compact(s))) for s in lines], .50),
        "dialogue_char_ratio": quoted_ratio(text),
        "question_sentence_ratio": rate(sum(bool(re.search(r"[？?][」』]?$", s)) for s in sents), len(sents)),
        "newline_per_1000_chars": rate(text.count("\n"), length, 1000),
        "hiragana_ratio": rate(sum("\u3040" <= c <= "\u309f" for c in clean), length),
        "katakana_ratio": rate(sum("\u30a0" <= c <= "\u30ff" for c in clean), length),
        "kanji_ratio": rate(sum("\u4e00" <= c <= "\u9fff" for c in clean), length),
    }
    for key, signs in PUNCT.items():
        stats[f"{key}_per_1000_chars"] = rate(sum(clean.count(s) for s in signs), length, 1000)
    for category in MARKERS:
        stats[f"{category}_per_100_sentences"] = rate(markers[category], len(sents), 100)
    return stats


def phases(text: str) -> dict[str, dict[str, float]]:
    sents = sentences(text)
    out: dict[str, dict[str, float]] = {}
    for name, start, end in PHASES:
        lo, hi = int(len(sents) * start), int(len(sents) * end)
        if lo < hi:
            out[name] = measure("\n".join(sents[lo:hi]))
    return out


def morphology(text: str, tagger: Any) -> dict[str, float]:
    """Optional fugashi features: aggregate only whitelist particles + POS bigram rate."""
    tokens: list[tuple[str, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        for chunk in re.findall(r".{1,1000}", line):
            for word in tagger(chunk):
                part = str(getattr(word.feature, "pos1", ""))
                if part not in ("補助記号", "記号", "空白"):
                    tokens.append((str(word.surface), part))
    n = len(tokens)
    f = Counter(word for word, pos in tokens if pos in ("助詞", "助動詞") and word in FUNCTION_WORDS)
    p = Counter(pos for _, pos in tokens)
    stats = {f"function_{word}_per_100_tokens": rate(f[word], n, 100) for word in sorted(FUNCTION_WORDS)}
    for pos in ("助詞", "助動詞", "名詞", "動詞", "形容詞", "副詞", "接続詞"):
        stats[f"pos_{pos}_per_100_tokens"] = rate(p[pos], n, 100)
    return stats


def make_tagger(mode: str) -> Any:
    if mode == "none":
        return None
    try:
        from fugashi import Tagger
        return Tagger()
    except (ImportError, RuntimeError, OSError) as exc:
        raise ValueError("--morphology fugashi requires `pip install 'fugashi-plus[unidic-lite]'` and a working dictionary") from exc


def read_text(path: Path, max_bytes: int = 15_000_000) -> str:
    if path.is_symlink():
        raise ValueError("symlink inputs are unsupported")
    if path.stat().st_size > max_bytes:
        raise ValueError(f"input over {max_bytes} bytes")
    data = path.read_bytes()
    for enc in ("utf-8-sig", "cp932"):
        try:
            return data.decode(enc).replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            pass
    raise ValueError("not UTF-8/CP932")


def input_files(root: Path) -> list[Path]:
    if root.is_symlink():
        raise ValueError("symlink input roots are unsupported")
    if not root.exists():
        raise ValueError("corpus path not found")
    if root.is_file():
        if root.suffix.lower() != ".txt":
            raise ValueError("corpus file must be .txt")
        return [root]
    return [p for p in sorted(root.rglob("*")) if p.suffix.lower() == ".txt" and p.is_file() and not p.is_symlink()]


def collect(root: Path, tagger: Any, min_chars: int) -> tuple[list[dict[str, float]], list[dict[str, dict[str, float]]], dict[str, int]]:
    works, phase_list = [], []
    skipped = Counter()
    for path in input_files(root):
        try:
            text = read_text(path)
            if len(compact(text)) < min_chars:
                skipped["too_short"] += 1
                continue
            feats = measure(text)
            if tagger is not None:
                feats.update(morphology(text, tagger))
            works.append(feats)
            phase_list.append(phases(text))
        except (OSError, UnicodeError, ValueError):
            skipped["unreadable_or_oversize"] += 1
    return works, phase_list, dict(skipped)


def aggregate(works: list[dict[str, float]], phase_list: list[dict[str, dict[str, float]]], skipped: dict[str, int], mode: str) -> dict[str, Any]:
    if not works:
        raise ValueError("no qualifying TXT files; check UTF-8/CP932, .txt and --min-chars")
    metrics: dict[str, dict[str, float]] = {}
    for name in works[0]:
        values = [w[name] for w in works]
        metrics[name] = {"median": quantile(values, .5), "p25": quantile(values, .25), "p75": quantile(values, .75)}
    phase_metrics: dict[str, dict[str, float]] = {}
    for phase, _, _ in PHASES:
        rows = [item[phase] for item in phase_list if phase in item]
        if rows:
            phase_metrics[phase] = {name: quantile([r[name] for r in rows], .5) for name in rows[0]}
    return {
        "schema": SCHEMA, "profiler_version": VERSION,
        "method": "unweighted median of per-work features; descriptive proxies only",
        "morphology": mode, "works": len(works), "total_characters": int(sum(w["characters"] for w in works)),
        "skipped": skipped,
        "confidence": "low" if len(works) < 5 else "moderate_descriptive_only",
        "metrics": metrics, "phase_medians": phase_metrics,
        "limits": ["marker frequency is not causal or narrative reasoning", "genre and length confounding are possible", "character dialogue detection is heuristic"],
    }


def reference_delta(primary: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
    return {
        "note": "descriptive differences, not significance tests or author identification",
        "reference_works": reference["works"],
        "median_differences": {
            key: round(primary["metrics"][key]["median"] - reference["metrics"][key]["median"], 5)
            for key in KEY_METRICS
        },
    }


def safe_profile(profile: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(profile, dict) or profile.get("schema") != SCHEMA:
        raise ValueError(f"expected schema {SCHEMA}")
    metrics = profile.get("metrics")
    if not isinstance(metrics, dict) or not all(k in metrics for k in KEY_METRICS):
        raise ValueError("incomplete profile metrics")
    for name, item in metrics.items():
        if not isinstance(item, dict):
            raise ValueError("invalid metric entry")
        for key in ("p25", "median", "p75"):
            value = item.get(key)
            if not isinstance(value, (float, int)) or not math.isfinite(value):
                raise ValueError(f"non-finite metric {name}.{key}")
    return profile


def drafting_guide(profile: dict[str, Any], notes: dict[str, Any] | None = None) -> str:
    m = profile["metrics"]
    def value(key: str, field: str = "median") -> float:
        return m[key][field]
    lines = ["# Style Bible — quantitative profile (offline)", "",
             "> These measurements are descriptive, not semantic claims. Do not insert reference passages or mimic copyrighted plot/characters.",
             f"> Corpus: {profile['works']} works / {profile['total_characters']} non-whitespace characters. Reliability: {profile['confidence']}.", "",
             "## Reproducible surface-level targets (soft, not word-for-word constraints)", "",
             f"- Sentence median across works: {value('p50_sentence_chars'):.1f} characters (between-work IQR {value('p50_sentence_chars','p25'):.1f}–{value('p50_sentence_chars','p75'):.1f}); preserve variation.",
             f"- Long sentence (P90) typical: {value('p90_sentence_chars'):.1f} characters, not a hard upper bound.",
             f"- Quoted dialogue character share: {value('dialogue_char_ratio'):.1%} (rough Japanese quote heuristic).",
             f"- Commas: {value('comma_per_1000_chars'):.1f}/1000 chars; ellipses: {value('ellipsis_per_1000_chars'):.1f}/1000 chars.",
             f"- Newlines: {value('newline_per_1000_chars'):.1f}/1000 chars; question sentences: {value('question_sentence_ratio'):.1%}.",
             "",
             "## Positional evidence (opening 20%, middle 60%, ending 20% by sentence count)", ""]
    for key, label in (("opening", "Opening"), ("middle", "Middle"), ("ending", "Ending")):
        item = profile.get("phase_medians", {}).get(key)
        if item:
            lines.append(f"- {label}: sentence median {item['p50_sentence_chars']:.1f} chars, dialogue {item['dialogue_char_ratio']:.1%}; contrast marker {item['contrast_per_100_sentences']:.2f}/100 sentences.")
    lines += ["", "## What the instrument CANNOT infer", "",
              "- These marker counts do not establish motive, ideology, causality, arc shape, or preferred genre.",
              "- Do not infer hidden narrative rules from numbers; require separate verified notes.",
              "- Do not copy unique phrases, passages, plot beats, character identities, or settings.", ""]
    rules = (notes or {}).get("rules", [])
    accepted = [r for r in rules if is_guide_eligible(r)]
    lines += ["## Narrative mechanisms — provenance required", "",
              "These are optional, not copyable plot templates. `model_supported` denotes automated model agreement and recurrent corpus coding; it is NOT independent verification or author-specificity.", ""]
    if accepted:
        for r in accepted:
            support = (f"; {r.get('support_work_count')} independent TXT works, model-double-checked"
                       if r.get('status') == 'model_supported' else '; manually verified')
            lines.append(f"- IF {r.get('trigger', 'unspecified')}; THEN {r['mechanism']}; EXPECT {r.get('effect', 'unspecified')}{support}.")
    else:
        lines.append("No validated narrative mechanisms supplied. Do not infer causal laws from punctuation statistics.")
    return "\n".join(lines) + "\n"


def template_notes() -> dict[str, Any]:
    return {"schema": "pastiche.narrative.v1", "rules": [],
            "instructions": "Add rules manually after local reading: {status: verified, trigger, mechanism, effect, evidence_ids: [local anonymized IDs]}. Do not paste source passages or identifying details. Speculative rules must use status: hypothesis."}


def is_guide_eligible(rule: dict[str, Any]) -> bool:
    if not isinstance(rule, dict) or not isinstance(rule.get("mechanism"), str) or not rule["mechanism"].strip():
        return False
    if rule.get("status") == "verified":
        return True  # Legacy human confirmation (v1), not automatic proof.
    if rule.get("status") != "model_supported":
        return False
    evidence = rule.get("evidence_ids") or []
    distinct_works = {eid.split(":", 1)[0] for eid in evidence
                      if isinstance(eid, str) and re.fullmatch(r"W\d{4}:S\d{4,}", eid)}
    return (rule.get("source") in {"local_llm_two_pass", "model_two_pass"} and rule.get("validation_passes") == 2
            and type(rule.get("support_work_count")) is int and rule["support_work_count"] >= 2
            and len(distinct_works) >= 2 and len(distinct_works) == rule["support_work_count"])


def load_notes(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    obj = json.loads(path.read_text(encoding="utf-8"))
    schema = obj.get("schema")
    if schema not in {"pastiche.narrative.v1", "pastiche.narrative.v2"} or not isinstance(obj.get("rules"), list):
        raise ValueError("invalid narrative notes schema")
    allowed = {"verified", "hypothesis", "rejected"} if schema.endswith("v1") else {"model_supported", "hypothesis", "rejected"}
    for rule in obj["rules"]:
        if not isinstance(rule, dict) or rule.get("status") not in allowed:
            raise ValueError(f"invalid narrative rule status for {schema}")
        if schema.endswith("v2") and rule.get("status") == "model_supported" and not is_guide_eligible(rule):
            raise ValueError("unverified auto rule: missing two supporting works or two-pass provenance")
    return obj


def overlap_scan(draft: str, corpus: Path, min_span: int = 40) -> dict[str, int]:
    """Exact, whitespace-normalized reuse screening. Only returns counts/offsets."""
    # No hashes/files/excerpts are written; source material is never returned.
    import hashlib
    text = compact(draft)
    if len(text) < min_span:
        return {"threshold_chars": min_span, "matching_windows": 0, "longest_matching_span_chars": 0}
    def digest(s: str) -> bytes:
        return hashlib.blake2b(s.encode("utf-8"), digest_size=16).digest()
    windows: dict[bytes, set[int]] = {}
    for i in range(len(text) - min_span + 1):
        windows.setdefault(digest(text[i:i + min_span]), set()).add(i)
    positions: set[int] = set()
    for path in input_files(corpus):
        try:
            original = compact(read_text(path))
        except (OSError, ValueError):
            continue
        for j in range(max(0, len(original) - min_span + 1)):
            positions.update(windows.get(digest(original[j:j + min_span]), ()))
    longest_run = cur = 0
    previous = -2
    for i in sorted(positions):
        cur = cur + 1 if i == previous + 1 else 1
        longest_run = max(longest_run, cur)
        previous = i
    return {"threshold_chars": min_span, "matching_windows": len(positions),
            "longest_matching_span_chars": longest_run + min_span - 1 if longest_run else 0}


def evaluate(profile: dict[str, Any], text: str) -> dict[str, Any]:
    observed = measure(text)
    comparisons = {}
    for key in KEY_METRICS:
        corpus = profile["metrics"][key]
        n = observed[key]
        comparisons[key] = {"draft": n, "target_median": corpus["median"],
                            "target_between_work_iqr": [corpus["p25"], corpus["p75"]],
                            "within_iqr": corpus["p25"] <= n <= corpus["p75"]}
    return {"note": "Proxy distances only; not a quality, identity, safety, or originality score.",
            "draft_characters": int(observed["characters"]),
            "draft_too_short_for_stable_comparison": observed["characters"] < 500,
            "style_comparisons": comparisons}


def json_write(dest: Path, data: Any) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def command_profile(args: argparse.Namespace) -> int:
    tagger = make_tagger(args.morphology)
    works, phase_rows, skipped = collect(args.corpus, tagger, args.min_chars)
    result = aggregate(works, phase_rows, skipped, args.morphology)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    json_write(out / "style_profile.json", result)
    notes_file = out / "narrative_rules.json"
    if notes_file.exists():
        notes = load_notes(notes_file)  # Preserve existing auto rules on re-profiling.
    else:
        notes = template_notes()
        json_write(notes_file, notes)
    (out / "style_bible.md").write_text(drafting_guide(result, notes), encoding="utf-8")
    if args.reference:
        refs, ref_phases, ref_skipped = collect(args.reference, tagger, args.min_chars)
        comparison = reference_delta(result, aggregate(refs, ref_phases, ref_skipped, args.morphology))
        json_write(out / "reference_comparison.json", comparison)
    print(f"Profile created: {out.resolve()} | works={result['works']} | confidence={result['confidence']}")
    print("No source text, filenames, excerpts, or lexical topics are stored in the profile.")
    return 0


def command_evaluate(args: argparse.Namespace) -> int:
    p = safe_profile(json.loads(args.profile.read_text(encoding="utf-8")))
    draft = read_text(args.draft)
    result = evaluate(p, draft)
    if args.corpus:
        if args.overlap_chars < 24:
            raise ValueError("--overlap-chars must be >= 24")
        result["overlap"] = overlap_scan(draft, args.corpus, args.overlap_chars)
    json_write(args.out, result)
    print(f"Evaluation saved: {args.out.resolve()}; no draft or source excerpts exported")
    return 0


def command_guide(args: argparse.Namespace) -> int:
    profile = safe_profile(json.loads(args.profile.read_text(encoding="utf-8")))
    notes = load_notes(args.notes)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    content = drafting_guide(profile, notes)
    if args.lexical:
        lex = args.lexical.read_text(encoding="utf-8")
        if len(lex) > 80_000:
            raise ValueError("lexical guide is too large")
        content += "\n---\n\n" + lex
    if args.qualitative:
        qualitative = args.qualitative.read_text(encoding="utf-8")
        if len(qualitative) > 80_000 or not qualitative.startswith(
                "# Qualitative Style Bible — LLM-assisted (private)"):
            raise ValueError("invalid or oversized qualitative guide")
        content += "\n---\n\n" + qualitative
    if args.lexical_review:
        interpretation=args.lexical_review.read_text(encoding="utf-8")
        if len(interpretation)>80_000:
            raise ValueError("lexical interpretation is too large")
        content += "\n---\n\n"+interpretation
    args.out.write_text(content, encoding="utf-8")
    print(f"Style guide saved: {args.out.resolve()}; evidence-filtered narrative rules and selected lexical features incorporated")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local-only Japanese style profiling and screening (no network / no LLM)")
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("profile", help="Profile a UTF-8/CP932 TXT directory")
    p.add_argument("corpus", type=Path)
    p.add_argument("--out", type=Path, default=Path(".pastiche-private/profile"))
    p.add_argument("--reference", type=Path, help="Optional other-author same-genre TXT collection")
    p.add_argument("--morphology", choices=("none", "fugashi"), default="none")
    p.add_argument("--min-chars", type=int, default=80)
    e = sub.add_parser("evaluate", help="Check one draft against a saved profile")
    e.add_argument("--profile", type=Path, required=True)
    e.add_argument("--draft", type=Path, required=True)
    e.add_argument("--corpus", type=Path, help="Optional local-only exact-overlap scanning")
    e.add_argument("--overlap-chars", type=int, default=40)
    e.add_argument("--out", type=Path, default=Path(".pastiche-private/evaluation.json"))
    g = sub.add_parser("guide", help="Re-render prompt guide from the profile and verified notes")
    g.add_argument("--profile", type=Path, required=True)
    g.add_argument("--notes", type=Path, required=False)
    g.add_argument("--lexical", type=Path, help="Optional private lexical_bible.md (may contain exact short expressions)")
    g.add_argument("--lexical-review", type=Path, help="Optional model-language-impression lexical_interpretation.md")
    g.add_argument("--qualitative", type=Path, help="Validated LLM qualitative_bible.md")
    g.add_argument("--out", type=Path, default=Path(".pastiche-private/style_bible.md"))
    args = parser.parse_args(argv)
    try:
        if args.command == "profile":
            if args.min_chars < 1:
                raise ValueError("--min-chars must be positive")
            return command_profile(args)
        if args.command == "evaluate":
            return command_evaluate(args)
        return command_guide(args)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
