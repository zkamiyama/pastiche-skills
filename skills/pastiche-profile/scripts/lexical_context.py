"""Transient, deterministic multi-work contexts; no passages in persistent caches."""
from __future__ import annotations
import hashlib
import re
from pathlib import Path
from pastiche import input_files, read_text
from vocabulary import located_tokens, feature

VERSION = 'contextual-lexical.v2'


def collect(corpus: Path, rows: list[dict], tagger, limit: int = 3) -> dict[str, list[str]]:
    """Hash-ranked reservoir across the whole corpus, max one context per work.

    This avoids always judging the first two files. Hashes are for selection,
    not anonymity guarantees. Known proper names/URLs are masked in memory.
    """
    if limit < 1: raise ValueError('invalid context limit')
    if not rows: return {}
    slots = {r['id']: [] for r in rows}
    lemma_lookup = {}; exact_lookup = {}
    for row in rows:
        if row.get('kind') == 'lemma':
            lemma_lookup.setdefault((row['form'], row.get('primary_pos','')), []).append(row)
        elif row.get('kind') in {'expression','surface'}:
            exact_lookup.setdefault(row['form'][0], []).append(row)
    for path in input_files(corpus):
        text = read_text(path); local = {}
        work_digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        for piece, tokens in located_tokens(text, tagger):
            proper = [str(t.surface) for t,_,_ in tokens
                      if '固有名詞' in (feature(t,'pos2'), feature(t,'pos3'))]
            def add(row, a, b):
                snippet = piece[max(0,a-36):min(len(piece),b+36)]
                for name in sorted(set(proper), key=len, reverse=True):
                    snippet = snippet.replace(name, '［固有名詞］')
                snippet = re.sub(r'https?://\S+', '［URL］', snippet)
                rank = hashlib.sha256((row['form']+'\0'+work_digest+'\0'+snippet).encode('utf-8')).hexdigest()
                key = row['id']
                if key not in local or rank < local[key][0]:local[key] = (rank, snippet)
            ends = {b for _,_,b in tokens}
            for token,a,b in tokens:
                base = feature(token,'orthBase') or str(token.surface)
                for row in lemma_lookup.get((base,feature(token,'pos1')),[]):add(row,a,b)
                for row in exact_lookup.get(piece[a],[]):
                    end = a+len(row['form'])
                    if end in ends and piece.startswith(row['form'], a):add(row,a,end)
        for key, (rank, snippet) in local.items():
            # Exact duplicate texts/contexts do not pretend to add independent evidence.
            if any(work_digest==d or snippet==s for _,d,s in slots[key]):continue
            slots[key].append((rank,work_digest,snippet))
            slots[key] = sorted(slots[key])[:limit]
    return {key:[snippet for _,_,snippet in value] for key,value in slots.items()}


def no_long_quote(note: str, contexts: list[str], width: int = 24) -> bool:
    note = ''.join(note.split())
    return not any(note[i:i+width] in ''.join(source.split())
                   for i in range(max(0,len(note)-width+1)) for source in contexts)
