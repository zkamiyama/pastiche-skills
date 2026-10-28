#!/usr/bin/env python3
"""Lossless orthographic / lexical statistics for Japanese prose.

This is a local, deterministic, non-LLM stage. Output intentionally contains
short verbatim forms (single tokens and user-requested variants), so keep all
artifacts private and review before sending to a model or a repository.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Iterator

from pastiche import compact, input_files, read_text

SCHEMA = "pastiche.lexical.v1"
SYMBOLS = "♡♥❤💕💖♪♫♬☆★※！!？?…〜～ー"  # Distinct glyphs must remain distinct.
RUN_SYMBOLS = "♡♥❤💕💖♪♫♬☆★！!？?…"
DEFAULT_PAIRS = (
    ("じゃねえか", "じゃあねえか"),
    ("来たっ！", "来たッ！"),
    ("来たっ!", "来たッ!"),
    ("♡", "♥"),
    ("♪", "☆"),
    ("！！", "!!"),
    ("……", "・・・"),
)
# Deliberately conservative; arbitrary parenthetical prose is not a kaomoji.
PARENTHETICAL = re.compile(r"[（(][^\n()（）]{2,32}[)）]")
FACE_MARKERS = set("ω∀Д▽・＾^´｀¬ಠ◕ツT_T;；>＜≧≦ﾉノ/ヽ∩益ﾟ°˘﹏￣")
SENTENCE_END = re.compile(r"[^。！？!?\n]+[。！？!?]+[」』]?|[^。！？!?\n]+$")
SENTENCE_PUNCT = "。！？!?」』\r\n "
SYMBOL_SET = set(SYMBOLS)
RUN_SET = set(RUN_SYMBOLS)


def select_pairs(path: Path | None) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = list(DEFAULT_PAIRS)
    if path:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("--variants expects JSON array of two-string arrays")
        for row in raw:
            if not isinstance(row, list) or len(row) != 2 or not all(isinstance(x, str) for x in row):
                raise ValueError("each variant must have exactly two strings")
            a, b = row
            if not a or not b or a == b or any("\n" in v or len(v) > 80 for v in row):
                raise ValueError("invalid variant pair")
            values.append((a, b))
    return list(dict.fromkeys(values))


def dialogue_ranges(text: str) -> list[tuple[int, int]]:
    # Approximation: nested and unclosed quotes aren't analyzed as scenes.
    starts = {"「": "」", "『": "』"}
    stack: list[tuple[str, int]] = []
    ranges: list[tuple[int, int]] = []
    for i, c in enumerate(text):
        if c in starts:
            stack.append((starts[c], i))
        elif stack and c == stack[-1][0]:
            _, left = stack.pop()
            if not stack:
                ranges.append((left, i+1))
    return ranges


class RegionLookup:
    def __init__(self, text: str):
        self.text = text
        self.ranges = dialogue_ranges(text)
        self.starts = [a for a, _ in self.ranges]

    def in_dialogue(self, pos: int) -> bool:
        i = bisect_right(self.starts, pos) - 1
        return i >= 0 and pos < self.ranges[i][1]

    def at_end(self, pos_after: int) -> bool:
        tail = self.text[pos_after:pos_after+8].lstrip()
        if not tail or tail.startswith(("」", "』", "。", "！", "!", "？", "?", "\n")):
            return True
        return False


def find_all(text: str, pattern: str) -> Iterator[tuple[int,int]]:
    # Non-overlapping exact matches; NFC/NFKC and katakana conversion are forbidden.
    start = 0
    while True:
        idx = text.find(pattern, start)
        if idx < 0:
            return
        yield idx, idx+len(pattern)
        start = idx+len(pattern)


def runs(text: str) -> Iterator[tuple[str, int, int]]:
    i = 0
    while i < len(text):
        c = text[i]
        if c in RUN_SET:
            j = i + 1
            while j < len(text) and text[j] == c:
                j += 1
            if j-i >= 2:
                yield c, j-i, i
            i = j
        else:
            i += 1


def mixed_sign_sequences(text: str) -> Counter[str]:
    counter: Counter[str] = Counter()
    current = []
    def flush():
        if len(current) >= 2 and len(set(current)) >= 2:
            counter["".join(current)] += 1
        current.clear()
    for c in text:
        if c in RUN_SET:
            if len(current) >= 24:
                flush()
            current.append(c)
        else:
            flush()
    flush()
    return counter


def kaomoji(text: str) -> Counter[str]:
    found = Counter()
    for match in PARENTHETICAL.finditer(text):
        sample = match.group()
        inside = sample[1:-1]
        if any(c in FACE_MARKERS for c in inside) and not any(c in "\n。！？" for c in inside):
            found[sample] += 1
    return found


def endings(text: str) -> Counter[str]:
    found = Counter()
    for line in text.splitlines():
        for match in SENTENCE_END.finditer(line):
            segment = match.group().rstrip(SENTENCE_PUNCT)
            if len(segment) >= 2:
                found[segment[-4:]] += 1
    return found


def morph_tagger(mode: str):
    if mode == "none":
        return None
    try:
        from fugashi import Tagger
        return Tagger()
    except (ImportError, OSError, RuntimeError) as exc:
        if mode == "auto":
            return None
        raise ValueError("--morphology fugashi requires `pip install 'fugashi-plus[unidic-lite]'`") from exc


def words(text: str, tagger: Any) -> tuple[Counter[str], Counter[str], int, dict[str, Counter[str]]]:
    tokens: Counter[str] = Counter()
    cooccurrences: Counter[str] = Counter()
    total = 0
    pos_by_form: dict[str, Counter[str]] = defaultdict(Counter)
    for line in text.splitlines():
        if not line.strip():
            continue
        # Split into sentence-like units so collocations do not cross sentences.
        for sentence in re.split(r"[。！？!?\n]", line):
            # Only count *adjacent* content words as collocations, not words
            # with skipped particles or names between them.
            previous: str | None = None
            for start in range(0, len(sentence), 800):
                for item in tagger(sentence[start:start+800]):
                    surface = str(item.surface)
                    pos = str(getattr(item.feature, "pos1", ""))
                    subpos = str(getattr(item.feature, "pos2", ""))
                    # Preserve the *original surface form*, including atypical spelling.
                    # Store all lexical word classes in the private inventory;
                    # only some of them will enter the style-interpretation shortlist.
                    wordlike = (pos not in {"補助記号", "記号", "空白"}
                                and 0 < len(surface) <= 64
                                and bool(re.search(r"[^\W_]", surface, re.UNICODE)))
                    if not wordlike:
                        previous = None
                        continue
                    tokens[surface] += 1
                    pos_by_form[surface][pos + (":" + subpos if subpos else "")] += 1
                    total += 1
                    content = (pos in {"名詞", "動詞", "形容詞", "副詞", "感動詞", "接続詞"}
                               and subpos != "固有名詞"
                               and len(surface) <= 24)
                    if content and previous and len(previous)+len(surface)<=28:
                        cooccurrences[previous+" "+surface] += 1
                    previous = surface if content else None
    return tokens, cooccurrences, total, pos_by_form


def rank(counter: Counter, docs: Counter, n_works: int, chars: int, limit: int = 80, min_count: int = 1) -> list[dict[str, Any]]:
    return [
        {"form": form, "count": n,
         "per_10000_chars": round(n*10000/max(chars, 1), 4),
         "work_count": docs[form], "work_share": round(docs[form]/max(n_works, 1), 4)}
        for form, n in counter.most_common()
        if n >= min_count
    ][:limit]


def collect(corpus: Path, pairs: list[tuple[str,str]], tagger: Any, min_chars: int = 80,
            retain_internal: bool = False) -> dict[str, Any]:
    total = 0
    works = 0
    symbols: Counter[str] = Counter()
    symbol_docs: Counter[str] = Counter()
    symbol_regions: dict[str, Counter] = defaultdict(Counter)
    run_counts: Counter[tuple[str,int]] = Counter()
    run_docs: Counter[tuple[str,int]] = Counter()
    mixed: Counter[str] = Counter()
    mixed_docs: Counter[str] = Counter()
    emoticons: Counter[str] = Counter()
    emoticon_docs: Counter[str] = Counter()
    ending_counts: Counter[str] = Counter()
    ending_docs: Counter[str] = Counter()
    form_counts: Counter[str] = Counter()
    form_docs: Counter[str] = Counter()
    form_region: dict[str,Counter] = defaultdict(Counter)
    token_counts: Counter[str] = Counter()
    token_docs: Counter[str] = Counter()
    collocations: Counter[str] = Counter()
    collocation_docs: Counter[str] = Counter()
    total_tokens = 0
    token_pos: dict[str, Counter[str]] = defaultdict(Counter)
    skipped = Counter()
    alternatives = sorted({form for pair in pairs for form in pair}, key=lambda x:(-len(x),x))
    for path in input_files(corpus):
        try:
            text = read_text(path)
        except (OSError, UnicodeError, ValueError):
            skipped["unreadable"] += 1
            continue
        length = len(compact(text))
        if length < min_chars:
            skipped["too_short"] += 1
            continue
        works += 1
        total += length
        regions = RegionLookup(text)
        this_symbols = Counter(c for c in text if c in SYMBOL_SET)
        symbols.update(this_symbols)
        symbol_docs.update(this_symbols.keys())
        for c in this_symbols:
            for a,b in find_all(text, c):
                symbol_context = symbol_regions[c]
                symbol_context["dialogue" if regions.in_dialogue(a) else "narration"] += 1
                if regions.at_end(b):
                    symbol_context["terminal"] += 1
        # Keep the actual run length (rather than folding every run >=12 into 12).
        this_runs: Counter[tuple[str,int]] = Counter((c,n) for c,n,pos in runs(text))
        run_counts.update(this_runs)
        run_docs.update(this_runs.keys())
        this_mixed = mixed_sign_sequences(text)
        mixed.update(this_mixed)
        mixed_docs.update(this_mixed.keys())
        this_emoticons = kaomoji(text)
        emoticons.update(this_emoticons)
        emoticon_docs.update(this_emoticons.keys())
        this_endings = endings(text)
        ending_counts.update(this_endings)
        ending_docs.update(this_endings.keys())
        for expression in alternatives:
            n = 0
            for a,b in find_all(text, expression):
                n += 1
                form_region[expression]["dialogue" if regions.in_dialogue(a) else "narration"] += 1
                if regions.at_end(b):
                    form_region[expression]["terminal"] += 1
            if n:
                form_counts[expression] += n
                form_docs[expression] += 1
        if tagger:
            ws, coll, tok, pos = words(text, tagger)
            token_counts.update(ws)
            for term, counts in pos.items():
                token_pos[term].update(counts)
            token_docs.update(ws.keys())
            collocations.update(coll)
            collocation_docs.update(coll.keys())
            total_tokens += tok
    if not works:
        raise ValueError("no qualifying corpus TXT files")
    variants = []
    for first, second in pairs:
        a,b = form_counts[first], form_counts[second]
        variants.append({
            "variants": [first, second],
            "counts": [a, b],
            "share_within_pair": [round(a/(a+b), 4),round(b/(a+b), 4)] if a+b else [None,None],
            "work_counts": [form_docs[first],form_docs[second]],
            "contexts": [{k:form_region[v][k] for k in ("dialogue","narration","terminal")}
                         for v in (first,second)],
        })
    symbol_rows = rank(symbols,symbol_docs,works,total,limit=100)
    for item in symbol_rows:
        item["contexts"]={key:symbol_regions[item["form"]][key]
                          for key in ("dialogue","narration","terminal")}
    run_rows=[]
    for (c,n),count in run_counts.most_common():
        run_rows.append({"symbol":c,"repetitions":n,"count":count,"work_count":run_docs[(c,n)]})
    result = {
        "schema":SCHEMA, "works":works, "total_characters":total,
        "normalization":"none; exact Unicode codepoint strings retained",
        "morphology":"fugashi" if tagger else "unavailable; character and expression analyses only",
        "skipped":dict(skipped),
        "symbols":symbol_rows,
        "symbol_runs":run_rows,
        "mixed_sequences":rank(mixed,mixed_docs,works,total,limit=60,min_count=2),
        "kaomoji_candidates":rank(emoticons,emoticon_docs,works,total,limit=60,min_count=1),
        "variant_pairs":variants,
        "sentence_endings":rank(ending_counts,ending_docs,works,total,limit=60,min_count=2),
        "words":rank(token_counts,token_docs,works,total,limit=80,min_count=2) if tagger else [],
        "collocations":rank(collocations,collocation_docs,works,total,limit=60,min_count=3) if tagger else [],
        "token_total":total_tokens,
        "limits": ["short exact forms and possible lexical content occur in this PRIVATE file",
                   "kaomoji detection uses a heuristic and can have false positives",
                   "work counts do not prove author-specific usage",
                   "word counts require optional fugashi; morphological segmentation is imperfect"],
    }
    if retain_internal:
        result["_internal_token_counts"] = token_counts
        result["_internal_token_docs"] = token_docs
        result["_internal_token_pos"] = token_pos
    return result


def make_inventory(profile: dict[str,Any]) -> list[dict[str,Any]]:
    """Exhaustive surface-token inventory (not truncated top-K).

    The raw surface is intentionally preserved. All counts are local and no
    excerpts, filenames, or automatically synthesized vocabulary are emitted.
    """
    counts = profile.get("_internal_token_counts", Counter())
    docs = profile.get("_internal_token_docs", Counter())
    parts = profile.get("_internal_token_pos", {})
    total = profile.get("token_total", 0)
    n_works = profile["works"]
    return [{"form":form, "count":count,"work_count":docs[form],
             "work_share":round(docs[form]/max(n_works,1),4),
             "per_10000_tokens":round(count*10000/max(total,1),4),
             "pos":dict(parts.get(form, {}))}
            for form,count in sorted(counts.items(),key=lambda x:(-x[1], x[0]))]


def lexical_candidates(profile: dict[str,Any],limit:int=96) -> list[dict[str,Any]]:
    """Prioritize cross-work, expressive or keyness-backed candidates for LLM.

    The *full* private inventory is untouched. This shortlist is not itself a
    distinctiveness verdict, just a bounded model input budget.
    """
    inventory=make_inventory(profile)
    keyness_rows={v["form"]:v for v in profile.get("distinctive_words", [])}
    candidates=[]
    for row in inventory:
        form=row["form"]
        if len(form)>30 or len(form)<2 or row["count"]<2:
            continue
        major=max(row["pos"],key=row["pos"].get) if row["pos"] else ""
        if major.startswith(("助詞", "助動詞", "補助記号", "記号", "空白")) or ":固有名詞" in major:
            continue
        if re.search(r"(?:https?://|@|\d{5,})",form):
            continue
        if not re.search(r"[一-龥ぁ-ゖァ-ヺ]",form):
            continue
        cross=min(row["work_count"],5)
        # Distinctive features often combine *recurrence* and nonstandard forms.
        expressive=bool(re.search(r"[っッぁぃぅぇぉャュョー～〜!?！？♡♪☆]",form))
        key=keyness_rows.get(form)
        score=(cross*2 + min(row["count"],20)/10 + (2 if expressive else 0)
               + (min(max(key["log2_relative_frequency"],0),6)*2 if key else 0))
        candidates.append(dict(row,primary_pos=major,keyness_log2=key["log2_relative_frequency"] if key else None,
                               selection_score=round(score,3)))
    ordered=sorted(candidates,key=lambda x:(-x["selection_score"],-x["work_count"],-x["count"],x["form"]))
    return ordered[:limit] if limit else ordered


def interpretation_candidates(profile:dict[str,Any], limit:int=128)->list[dict[str,Any]]:
    """Create a compact, diverse model input from the *full* local inventory.

    The model gets short lexical forms, not source passages. The entire inventory
    remains on disk. A zero limit deliberately selects all eligible forms and can
    require many slow requests, so this is NOT the default.
    """
    if limit<0:
        raise ValueError("candidate limit must be >= 0")
    candidates=[]
    seen=set()
    def insert(kind:str,form:str,count:int,work_count:int,primary_pos:str="",score:float=0.0):
        key=(kind,form)
        if not form or key in seen or len(form)>40 or count<1:
            return
        seen.add(key)
        candidates.append({"kind":kind,"form":form,"count":int(count),
                           "work_count":int(work_count),"primary_pos":primary_pos,
                           "selection_score":round(float(score),3)})

    # Reserve slots for every expression family. Otherwise a frequent-token
    # ranking can drown out distinctive symbols, endings and colloquial variants.
    for row in profile.get("variant_pairs",[])[:10]:
        for i,form in enumerate(row["variants"]):
            insert("variant",form,row["counts"][i],row["work_counts"][i],score=30)
    for row in profile.get("symbol_runs",[])[:8]:
        insert("symbol_run",row["symbol"]*row["repetitions"],row["count"],row["work_count"],score=28)
    for row in profile.get("kaomoji_candidates",[])[:6]:
        insert("kaomoji",row["form"],row["count"],row["work_count"],score=27)
    for row in profile.get("symbols",[])[:10]:
        insert("symbol",row["form"],row["count"],row["work_count"],score=25)
    for row in profile.get("sentence_endings",[])[:10]:
        insert("ending",row["form"],row["count"],row["work_count"],score=22)
    for row in profile.get("collocations",[])[:8]:
        insert("collocation",row["form"],row["count"],row["work_count"],score=21)
    token_budget=max(limit-len(candidates),0) if limit else 0
    for row in lexical_candidates(profile,limit=token_budget):
        insert("token",row["form"],row["count"],row["work_count"],row["primary_pos"],row["selection_score"])

    if limit:
        # Keep samples from every representation category even if token count
        # is huge, and prioritize wider document coverage within each family.
        candidates=candidates[:limit]
    return [dict(row,id=f"L{i:04d}") for i,row in enumerate(candidates,1)]


def keyness(profile: dict[str,Any], ref: dict[str,Any], top: int = 50) -> list[dict[str,Any]]:
    # Compare full counters BEFORE the top-K export truncation. Relative effect
    # sizes are descriptive, NOT significance or authorship attribution.
    if not profile["token_total"] or not ref["token_total"]:
        return []
    n1,n2=profile["token_total"],ref["token_total"]
    a=profile["_internal_token_counts"]
    b=ref["_internal_token_counts"]
    docs=profile["_internal_token_docs"]
    parts=profile["_internal_token_pos"]
    out=[]
    for form,n in a.items():
        # Compare content word choices, not simply ubiquitous function words.
        pos = max(parts[form].items(), key=lambda kv: kv[1])[0] if parts[form] else ""
        if n < 3 or docs[form] < 2 or pos.startswith(("助詞", "助動詞", "記号", "補助記号")) or ":固有名詞" in pos:
            continue
        r=b[form]
        logratio=math.log2(((n+.5)/(n1+1))/((r+.5)/(n2+1)))
        if logratio > 0:
            out.append({"form":form,"target_count":n,"reference_count":r,
                        "log2_relative_frequency":round(logratio,3),
                        "target_work_count":docs[form]})
    return sorted(out,key=lambda x:(-x["log2_relative_frequency"],-x["target_work_count"],-x["target_count"]))[:top]


def private_guide(profile:dict[str,Any], include_words:bool=False) -> str:
    lines=["# Lexical / Orthographic Profile (local analysis)","",
           "This document contains exact short forms and should be reviewed before use with a remote model.",
           "Treat frequencies as preferences, not mandatory repetition quotas. Do not reproduce source passages.","",
           "## Exact typography and symbols"]
    for item in profile.get("symbols",[])[:20]:
        lines.append(f"- `{item['form']}`: {item['count']} uses in {item['work_count']}/{profile['works']} works; dialogue={item['contexts']['dialogue']}, terminal={item['contexts']['terminal']}.")
    lines.append("\n## Repeated marks")
    for item in profile.get("symbol_runs",[])[:20]:
        lines.append(f"- `{item['symbol']}` × {item['repetitions']}: {item['count']} runs across {item['work_count']} works")
    lines.append("\n## Observed exact variants")
    for pair in profile.get("variant_pairs",[]):
        if sum(pair["counts"]):
            lines.append(f"- `{pair['variants'][0]}` vs `{pair['variants'][1]}`: {pair['counts'][0]} / {pair['counts'][1]} occurrences; document frequency {pair['work_counts'][0]} / {pair['work_counts'][1]}.")
    if profile.get("kaomoji_candidates"):
        lines.append("\n## Kaomoji candidates (heuristic)")
        for row in profile["kaomoji_candidates"][:12]:
            lines.append(f"- `{row['form']}`: {row['count']} instances")
    if include_words and profile.get("words"):
        lines.append("\n## Frequent short lexical forms (optional; contextual use only)")
        for row in profile["words"][:25]:
            lines.append(f"- `{row['form']}`: {row['count']} instances, {row['work_count']} works")
    return "\n".join(lines)+"\n"


def json_write(out:Path, data:dict[str,Any]) -> None:
    out.parent.mkdir(parents=True,exist_ok=True)
    tmp=out.with_name(out.name+".tmp")
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    tmp.replace(out)


def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser(description="Exact Unicode/style expression profiler (local only; no LLM)")
    parser.add_argument("corpus",type=Path)
    parser.add_argument("--out",type=Path,default=Path(".pastiche-private/profile"))
    parser.add_argument("--variants",type=Path,help="JSON array of exact two-item expression comparison pairs")
    parser.add_argument("--reference",type=Path,help="optional genre-matched comparison corpus for descriptive keyness")
    parser.add_argument("--morphology",choices=("auto","none","fugashi"),default="auto")
    parser.add_argument("--min-chars",type=int,default=80)
    parser.add_argument("--include-words-in-guide",action="store_true",help="export frequent short tokens into private lexical guide")
    parser.add_argument("--inventory-limit",type=int,default=0,help="0=ALL morphological word forms in private inventory; positive limits rows")
    parser.add_argument("--candidate-limit",type=int,default=96,help="max forms for optional LLM impression review; 0=all eligible (slow)")
    args=parser.parse_args(argv)
    try:
        if args.min_chars<1 or args.inventory_limit<0 or args.candidate_limit<0:
            raise ValueError("--min-chars positive; --inventory-limit/--candidate-limit >= 0")
        pairs=select_pairs(args.variants)
        tagger=morph_tagger(args.morphology)
        stats=collect(args.corpus,pairs,tagger,args.min_chars,retain_internal=True)
        if args.reference:
            reference=collect(args.reference,pairs,tagger,args.min_chars,retain_internal=True)
            stats["distinctive_words"] = keyness(stats,reference)
            stats["reference_works"] = reference["works"]
            stats["distinctiveness_note"] = "log2 relative frequency with additive smoothing; no significance claim"
        else:
            stats["distinctive_words"] = []
            stats["distinctiveness_note"] = "needs --reference and fugashi; frequency alone is not distinctiveness"
        shortlist = interpretation_candidates(stats,limit=args.candidate_limit)
        inventory = make_inventory(stats)
        if args.inventory_limit:
            inventory = inventory[:args.inventory_limit]
        stats["full_inventory_entries"] = len(stats["_internal_token_counts"])
        stats["full_inventory_file"] = "lexical_inventory.jsonl" if tagger else None
        stats.pop("_internal_token_counts")
        stats.pop("_internal_token_docs")
        stats.pop("_internal_token_pos")
        out=args.out
        json_write(out/"lexical_profile.json",stats)
        json_write(out/"lexical_candidates.json",{
            "schema":"pastiche.lexical.candidates.v2", "all_forms_examined":stats["full_inventory_entries"],
            "candidate_count":len(shortlist), "reference_available":bool(args.reference),
            "note":"All token forms stored privately; short-form ranking is a triage step, not a distinctiveness verdict.",
            "candidates":shortlist})
        if tagger:
            # Private, full unnormalized vocabulary. No source passages or filenames.
            inventory_path = out / "lexical_inventory.jsonl"
            with inventory_path.open("w",encoding="utf-8") as f:
                for row in inventory:
                    f.write(json.dumps(row,ensure_ascii=False)+"\n")
        else:
            (out / "lexical_inventory.jsonl").unlink(missing_ok=True)
            print("[lexical] Vocabulary inventory unavailable: install pip install 'fugashi-plus[unidic-lite]'",file=sys.stderr)
        (out/"lexical_bible.md").write_text(private_guide(stats,args.include_words_in_guide),encoding="utf-8")
        print(f"Lexical analysis complete: {stats['works']} works; tokenizer={stats['morphology']}")
        print("Full vocabulary inventory is PRIVATE; small exact forms are present in profiles. Review before sharing.")
        return 0
    except (OSError,ValueError,TypeError) as exc:
        print(f"error: {exc}",file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
