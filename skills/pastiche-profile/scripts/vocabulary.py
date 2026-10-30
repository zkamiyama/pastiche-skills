"""Readable lexical units, separate from lossless typography measurements.

UniDic orthBase groups inflections, NOT arbitrary spelling normalization.
Expressions are exact contiguous source spans, never space-joined token strings.
No passages, paths or names are retained. Ranking is descriptive triage only.
"""
from __future__ import annotations
from collections import Counter, defaultdict, deque
import math
import re
from typing import Any

VERSION = 'readable-vocabulary.v1'
CONTENT_POS = {'名詞', '動詞', '形容詞', '形状詞', '副詞', '感動詞'}
SENTENCE = re.compile(r'[^。！？!?\n「」『』]+')
JAPANESE = re.compile(r'[一-龥ぁ-ゖァ-ヺ]')
BUCKETS = ('副詞', '形容詞', '形状詞', '動詞', '連語', '名詞')
# Function-like lemmas remain in the measured inventory, not the writing shortlist.
FUNCTION_LIKE = frozenset({'よう','みたい','そう','もう','また','こと','もの','ため','ところ','これ','それ','あれ','どれ','ここ','そこ'})


def feature(token: Any, key: str) -> str:
    value = getattr(token.feature, key, '')
    return value if isinstance(value, str) and value != '*' else ''


def eligible(token: Any) -> bool:
    pos, sub, detail = (feature(token, k) for k in ('pos1', 'pos2', 'pos3'))
    return (pos in CONTENT_POS and '固有名詞' not in (sub, detail)
            and '非自立可能' not in (sub, detail)
            and '数詞' not in (sub, detail)
            and '助動詞語幹' not in (sub, detail))


def located_tokens(text: str, tagger: Any):
    """Each chunk has a fresh boundary; never bridge tokenizer batches."""
    for match in SENTENCE.finditer(text):
        sentence = match.group()
        for start in range(0, len(sentence), 800):
            piece = sentence[start:start+800]; cursor = 0; rows = []
            for token in tagger(piece):
                surface = str(token.surface)
                if not surface: continue
                position = piece.find(surface, cursor)
                if position < 0:
                    # An unlocatable token breaks the expression chain.
                    if rows: yield piece, rows
                    rows = []; continue
                rows.append((token, position, position+len(surface)))
                cursor = position+len(surface)
            if rows: yield piece, rows


class Vocabulary:
    def __init__(self, tagger: Any):
        self.tagger = tagger
        self.counts = Counter(); self.docs = Counter()
        self.forms = defaultdict(Counter)

    def add(self, text: str) -> None:
        seen = set()
        def put(kind, form, pos, surface):
            if (not form or len(form)>28 or not JAPANESE.search(form)
                    or any(c.isspace() for c in form) or not surface): return
            key=(kind,form,pos)
            self.counts[key]+=1; seen.add(key); self.forms[key][surface]+=1
        for piece, rows in located_tokens(text, self.tagger):
            for i,(token,a,b) in enumerate(rows):
                pos=feature(token,'pos1'); surface=piece[a:b]
                if eligible(token):
                    base=feature(token,'orthBase')
                    # Unknown dictionary features do not license guessing a stem.
                    if not base and feature(token,'cForm'): continue
                    base=base or surface
                    if not (pos=='感動詞' and len(base)<3):
                        put('lemma',base,pos,surface)
                else: continue
                # Adjacent common nouns: retain exact source spelling, including
                # suffixes. Whitespace or a proper noun always breaks the span.
                if pos=='名詞':
                    end=b
                    for j in range(i+1,min(i+3,len(rows))):
                        nxt,left,right=rows[j]
                        if left!=end or not eligible(nxt) or feature(nxt,'pos1')!='名詞':break
                        end=right;put('expression',piece[a:end],'連語',piece[a:end])
                # Noun + case particle + independent predicate, including its
                # contiguous auxiliaries, is a measured phrase, not a bag of words.
                if pos=='名詞' and i+2<len(rows):
                    particle,pa,pb=rows[i+1]; verb,va,vb=rows[i+2]
                    if (pa==b and va==pb and feature(particle,'pos1')=='助詞'
                            and feature(particle,'pos2')=='格助詞'
                            and feature(verb,'pos1') in {'動詞','形容詞'} and eligible(verb)):
                        end=vb; last=verb
                        for nxt,left,right in rows[i+3:i+6]:
                            if left!=end or feature(nxt,'pos1')!='助動詞':break
                            end=right;last=nxt
                        cform=feature(last,'cForm')
                        if not cform or cform.startswith(('終止形','連体形','命令形','意志推量形')):
                            put('expression',piece[a:end],'連語',piece[a:end])
        self.docs.update(seen)

    def rows(self, works: int) -> list[dict]:
        result=[]
        for (kind,form,pos), count in self.counts.items():
            key=(kind,form,pos)
            result.append({'kind':kind,'form':form,'count':count,'work_count':self.docs[key],
                'work_share':round(self.docs[key]/max(works,1),4),'primary_pos':pos,
                'observed_forms':[{'form':f,'count':n} for f,n in self.forms[key].most_common(4)],
                'count_basis':'orthographic_base' if kind=='lemma' else 'exact_contiguous_span',
                'headword_attested':form in self.forms[key],
                'headword_surface_count':self.forms[key].get(form,0),
                'selection_version':VERSION})
        return sorted(result,key=lambda r:(-r['work_count'],-r['count'],r['form'],r['kind']))


def candidates(rows: list[dict], limit: int=96) -> list[dict]:
    """POS-diverse recurrence shortlist; neither rarity nor author uniqueness."""
    if limit<0:raise ValueError('candidate limit must be >= 0')
    queues={b:[] for b in BUCKETS}
    for row in rows:
        if row['count']<2 or row['work_count']<2:continue
        form=row['form']
        if row.get('headword_attested') is False and row['kind']=='lemma':continue
        if form in FUNCTION_LIKE:continue
        if re.fullmatch(r'[ぁ-ゖァ-ヺ]+',form) and len(form)<3:continue
        if form.endswith(('っ','ッ')) or re.match(r'^[ぁぃぅぇぉっゃゅょァィゥェォッャュョ]',form):continue
        if not JAPANESE.search(row['form']) or any(c.isspace() for c in row['form']):continue
        pos=row['primary_pos']
        if pos not in queues:continue
        score=2*math.log1p(row['work_count'])+math.log1p(row['count'])
        queues[pos].append(dict(row,selection_score=round(score,6)))
    queues={b:deque(sorted(v,key=lambda r:(-r['selection_score'],r['form']))) for b,v in queues.items()}
    result=[];seen=set()
    while any(queues.values()) and (not limit or len(result)<limit):
        for bucket in BUCKETS:
            q=queues[bucket]
            while q and q[0]['form'] in seen:q.popleft()
            if not q:continue
            row=q.popleft();seen.add(row['form']);result.append(row)
            if limit and len(result)>=limit:break
    return [dict(row,id=f'L{i:04d}') for i,row in enumerate(result,1)]
