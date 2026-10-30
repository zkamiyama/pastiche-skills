#!/usr/bin/env python3
"""Compare a NEW draft with a private lexical/orthographic profile.

Only a generated lexical_profile.json and the new draft are read. No reference
corpus, tokenizer, model endpoint or network is needed. The report stores
aggregate counts and short exact forms from the profile, not source passages.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter
import json
from pathlib import Path
import re
import sys
from typing import Any

SCHEMA = "pastiche.lexical-evaluation.v1"
PROFILE_SCHEMA = "pastiche.lexical.v1"
RUN_CHARS = set("♡♥❤💕💖♪♫♬☆★！!？?…")
KAOMOJI_BRACKETS = re.compile(r"[（(][^\n()（）]{2,32}[)）]")
SENTENCE_END = re.compile(r"[^。！？!?\n]+[。！？!?]+[」』]?|[^。！？!?\n]+$")
SENTENCE_PUNCT = "。！？!?」』\r\n "


def read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("draft must be UTF-8 or CP932")


def load_profile(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict) or obj.get("schema") != PROFILE_SCHEMA:
        raise ValueError(f"lexical profile must use schema {PROFILE_SCHEMA}")
    if type(obj.get("total_characters")) is not int or obj["total_characters"] <= 0:
        raise ValueError("lexical profile has no valid character count")
    for key in ("symbols", "symbol_runs", "variant_pairs", "sentence_endings"):
        if not isinstance(obj.get(key), list):
            raise ValueError(f"lexical profile missing {key}")
    return obj


def compact_length(s: str) -> int:
    return sum(not c.isspace() for c in s)


def per_10k(n: int, chars: int) -> float:
    return round(10000 * n / chars, 3) if chars else 0.0


class Regions:
    """Approximate Japanese quote spans; intended for light feedback only."""
    def __init__(self, text: str):
        self.text = text
        stack: list[tuple[str, int]] = []
        ranges: list[tuple[int, int]] = []
        pairs = {"「": "」", "『": "』"}
        for i, char in enumerate(text):
            if char in pairs:
                stack.append((pairs[char], i))
            elif stack and char == stack[-1][0]:
                _, begin = stack.pop()
                if not stack:
                    ranges.append((begin, i + 1))
        self.ranges = ranges
        self.starts = [begin for begin, _ in ranges]

    def dialogue(self, pos: int) -> bool:
        index = bisect_right(self.starts, pos) - 1
        return index >= 0 and pos < self.ranges[index][1]

    def terminal(self, end: int) -> bool:
        tail = self.text[end:end + 8].lstrip()
        return not tail or tail.startswith(("」", "』", "。", "！", "!", "？", "?", "\n"))


def locate(text: str, form: str) -> list[tuple[int, int]]:
    if not form:
        return []
    result = []
    start = 0
    while True:
        i = text.find(form, start)
        if i < 0:
            return result
        end = i + len(form)
        result.append((i, end))
        start = end  # exact, non-overlapping matches


def context_counts(matches: list[tuple[int, int]], regions: Regions) -> dict[str, int]:
    answer = Counter()
    for start, end in matches:
        answer["dialogue" if regions.dialogue(start) else "narration"] += 1
        if regions.terminal(end):
            answer["terminal"] += 1
    return {kind: answer[kind] for kind in ("dialogue", "narration", "terminal")}


def variant_counts(text: str, first: str, second: str) -> list[int]:
    """Prefer longer variants, so overlapping spellings cannot double-count."""
    if not first or not second or first == second:
        return [0, 0]
    ordered = sorted({first, second}, key=lambda s: (-len(s), s))
    regex = re.compile("|".join(re.escape(s) for s in ordered))
    found = Counter(match.group(0) for match in regex.finditer(text))
    return [found[first], found[second]]


def repetition_counts(text: str, symbols: set[str]) -> Counter[tuple[str, int]]:
    output: Counter[tuple[str, int]] = Counter()
    i = 0
    while i < len(text):
        ch = text[i]
        if ch not in symbols:
            i += 1
            continue
        j = i + 1
        while j < len(text) and text[j] == ch:
            j += 1
        if j - i >= 2:
            output[ch, j - i] += 1
        i = j
    return output


def mixed_sequences(text: str) -> Counter[str]:
    found = Counter()
    group: list[str] = []
    def flush() -> None:
        if len(group) >= 2 and len(set(group)) >= 2:
            found["".join(group)] += 1
        group.clear()
    for char in text:
        if char in RUN_CHARS:
            if len(group) >= 24:
                flush()
            group.append(char)
        else:
            flush()
    flush()
    return found





def ending_counts(text: str) -> Counter[str]:
    result = Counter()
    for line in text.splitlines():
        for match in SENTENCE_END.finditer(line):
            s = match.group().rstrip(SENTENCE_PUNCT)
            if len(s) >= 2:
                result[s[-4:]] += 1
    return result


def compare(profile: dict[str, Any], draft: str) -> dict[str, Any]:
    n = compact_length(draft)
    base = profile["total_characters"]
    regions = Regions(draft)
    symbols = []
    for row in profile["symbols"][:100]:
        if not isinstance(row, dict) or not isinstance(row.get("form"), str):
            continue
        form = row["form"]
        if len(form) != 1:
            continue
        count = int(row.get("count", 0))
        matches = locate(draft, form)
        symbols.append({
            "form": form,
            "target_count": count,
            "target_works": row.get("work_count"),
            "target_per_10000_chars": per_10k(count, base),
            "draft_count": len(matches),
            "draft_per_10000_chars": per_10k(len(matches), n),
            "target_contexts": row.get("contexts", {}),
            "draft_contexts": context_counts(matches, regions),
        })

    variants = []
    for row in profile["variant_pairs"]:
        if not isinstance(row, dict):
            continue
        forms = row.get("variants")
        if not isinstance(forms, list) or len(forms) != 2 or not all(isinstance(f, str) for f in forms):
            continue
        counts = row.get("counts")
        if not isinstance(counts, list) or len(counts) != 2:
            continue
        observed = variant_counts(draft, forms[0], forms[1])
        base_total = sum(counts)
        total = sum(observed)
        target_share = [round(c / base_total, 4) for c in counts] if base_total else [None, None]
        draft_share = [round(c / total, 4) for c in observed] if total else [None, None]
        if total < 4 or base_total < 8:
            assessment = "insufficient_comparable_occurrences"
        elif abs(target_share[0] - draft_share[0]) >= 0.3:
            assessment = "inspect_context_before_revising"
        else:
            assessment = "roughly_aligned_not_a_quality_score"
        variants.append({
            "forms": forms,
            "target_counts": counts,
            "target_work_counts": row.get("work_counts", []),
            "target_share_within_pair": target_share,
            "draft_counts": observed,
            "draft_share_within_pair": draft_share,
            "assessment": assessment,
        })

    target_runs = profile["symbol_runs"][:40]
    used_marks = {r.get("symbol") for r in target_runs if isinstance(r, dict) and isinstance(r.get("symbol"), str) and len(r["symbol"]) == 1}
    draft_runs = repetition_counts(draft, used_marks)
    runs = [{"symbol": r["symbol"], "repetitions": r["repetitions"],
             "target_count": r.get("count", 0),
             "target_work_count": r.get("work_count", 0),
             "draft_count": draft_runs.get((r["symbol"], r["repetitions"]), 0)}
            for r in target_runs if isinstance(r, dict) and r.get("symbol") in used_marks
            and type(r.get("repetitions")) is int]

    # These optional fields were introduced in the exact-form profiler.
    mixed = mixed_sequences(draft)
    mixed_rows = [{"form": r["form"], "target_count": r.get("count", 0),
                   "draft_count": mixed[r["form"]]}
                  for r in profile.get("mixed_sequences", [])[:30]
                  if isinstance(r, dict) and isinstance(r.get("form"), str)]
    endings = ending_counts(draft)
    ending_rows = [{"form": r["form"], "target_count": r.get("count", 0),
                    "target_work_count": r.get("work_count", 0),
                    "draft_count": endings[r["form"]]}
                   for r in profile["sentence_endings"][:30]
                   if isinstance(r, dict) and isinstance(r.get("form"), str)]

    return {
        "schema": SCHEMA,
        "profile_works": profile.get("works"),
        "draft_characters": n,
        "draft_is_short": n < 500,
        "symbols": symbols,
        "variant_pairs": variants,
        "symbol_runs": runs,
        "mixed_sequences": mixed_rows,
        "sentence_endings": ending_rows,
        "limitations": [
            "Only measured exact forms and their counts are compared; no meaning or intent is inferred.",
            "Do not force a symbol or variant into a story merely to match a corpus ratio.",
            "A missing form may be appropriate when its discourse context is absent in the new brief.",
            "Short drafts and low-frequency variants are especially unstable; look for opportunities, not quotas.",
            "Quote and sentence-end detection are heuristic; these are not measures of literary quality or identity.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline check of a new draft's exact lexical/typographic preferences")
    parser.add_argument("--profile", required=True, type=Path, help="private lexical_profile.json")
    parser.add_argument("--draft", required=True, type=Path, help="newly generated TXT draft")
    parser.add_argument("--out", type=Path, default=Path(".pastiche-private/write_lexical_review.json"))
    args = parser.parse_args(argv)
    try:
        profile = load_profile(args.profile)
        draft = read_text(args.draft)
        report = compare(profile, draft)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        temp = args.out.with_suffix(args.out.suffix + ".tmp")
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(args.out)
        print(f"Lexical draft feedback saved: {args.out.resolve()} (no corpus files loaded)")
        return 0
    except (ValueError, OSError, TypeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
