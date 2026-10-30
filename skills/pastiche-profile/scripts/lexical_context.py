"""Transient concordances for sense disambiguation; never persisted as passages."""
from __future__ import annotations
import re
from pathlib import Path
from pastiche import input_files, read_text
from vocabulary import located_tokens, feature

VERSION='contextual-lexical.v1'


def collect(corpus: Path, rows: list[dict], tagger, limit: int=2) -> dict[str,list[str]]:
    if limit<1:raise ValueError('invalid context limit')
    result={r['id']:[] for r in rows}; lookup={}
    for row in rows:lookup.setdefault((row['form'],row.get('primary_pos','')),[]).append(row)
    expressions=[row for row in rows if row.get('kind')=='expression']
    for path in input_files(corpus):
        if all(len(v)>=limit for v in result.values()):break
        text=read_text(path);seen=set()
        for piece,tokens in located_tokens(text,tagger):
            proper=[str(t.surface) for t,_,_ in tokens if feature(t,'pos2')=='固有名詞']
            def add(row,a,b):
                key=row['id']
                if key in seen or len(result[key])>=limit:return
                snippet=piece[max(0,a-28):min(len(piece),b+28)]
                for name in sorted(set(proper),key=len,reverse=True):snippet=snippet.replace(name,'［固有名詞］')
                snippet=re.sub(r'https?://\S+','［URL］',snippet)
                result[key].append(snippet);seen.add(key)
            for token,a,b in tokens:
                base=feature(token,'orthBase') or str(token.surface)
                for row in lookup.get((base,feature(token,'pos1')),[]):
                    if row.get('kind')=='lemma':add(row,a,b)
            starts={a for _,a,_ in tokens};ends={b for _,_,b in tokens}
            for row in expressions:
                if row['id'] in seen or len(result[row['id']])>=limit:continue
                start=piece.find(row['form'])
                if start in starts and start+len(row['form']) in ends:add(row,start,start+len(row['form']))
    return result


def no_long_quote(note: str, contexts: list[str], width: int=24) -> bool:
    note=''.join(note.split())
    return not any(note[i:i+width] in ''.join(source.split())
                   for i in range(max(0,len(note)-width+1)) for source in contexts)
