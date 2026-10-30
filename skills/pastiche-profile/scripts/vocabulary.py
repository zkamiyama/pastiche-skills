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

VERSION = 'readable-vocabulary.v2'
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
        self.work_sizes = []
        self.per_work = defaultdict(Counter)
        self.signals = defaultdict(set)
        self.contexts = defaultdict(set)
        self.components = {}
        self.token_lengths = {}

    def add(self, text: str) -> None:
        seen = set(); local = Counter()
        work_index = len(self.work_sizes)
        self.work_sizes.append(max(1, sum(not c.isspace() for c in text)))
        def put(kind, form, pos, surface, *, components=None, signals=(), frame=''):
            if (not form or len(form)>28 or not JAPANESE.search(form)
                    or any(c.isspace() for c in form) or not surface): return
            key=(kind,form,pos)
            self.counts[key]+=1; seen.add(key); self.forms[key][surface]+=1
            local[key]+=1; self.signals[key].update(signals)
            if frame and len(self.contexts[key])<8:self.contexts[key].add(frame)
            if components:
                self.components.setdefault(key,list(components))
                self.token_lengths[key]=max(self.token_lengths.get(key,1),len(components))
        for piece, rows in located_tokens(text, self.tagger):
            for i,(token,a,b) in enumerate(rows):
                pos=feature(token,'pos1'); surface=piece[a:b]
                if eligible(token):
                    base=feature(token,'orthBase')
                    # Unknown dictionary features do not license guessing a stem.
                    if not base and feature(token,'cForm'): continue
                    base=base or surface
                    if not (pos=='感動詞' and len(base)<3):
                        signals=[]
                        if getattr(token,'is_unk',False):signals.append('dictionary_unknown')
                        lemma=feature(token,'lemma')
                        if lemma and lemma!=base and feature(token,'cForm')=='':
                            signals.append('orthographic_variant')
                        if re.fullmatch(r'(.{2,4})\1',surface):signals.append('reduplication')
                        if 'ー' in surface and re.search(r'[ぁ-ゖ]',surface):signals.append('extended_sound')
                        frame=piece[max(0,a-8):a]+'|'+piece[b:b+8]
                        put('lemma',base,pos,surface,signals=signals,frame=frame)
                        # Keep an unknown spelling even if the dictionary proposes a
                        # misleading/unattested headword. No invented replacement.
                        if getattr(token,'is_unk',False) and 3<=len(surface)<=16:
                            put('surface',surface,'未知語',surface,signals=['dictionary_unknown'],frame=frame)
                else: continue
                # Adjacent common nouns: retain exact source spelling, including
                # suffixes. Whitespace or a proper noun always breaks the span.
                if pos=='名詞':
                    end=b
                    for j in range(i+1,min(i+5,len(rows))):
                        nxt,left,right=rows[j]
                        if left!=end or not eligible(nxt) or feature(nxt,'pos1')!='名詞':break
                        end=right;put('expression',piece[a:end],'連語',piece[a:end], components=[str(t.surface) for t,_,_ in rows[i:j+1]], frame=piece[max(0,a-8):a]+'|'+piece[end:end+8])
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
                            put('expression',piece[a:end],'連語',piece[a:end], components=[str(t.surface) for t,left,right in rows[i:] if left>=a and right<=end], frame=piece[max(0,a-8):a]+'|'+piece[end:end+8])
                # Short modifiers plus nouns/predicates are measured exact spans.
                if pos in {'副詞','形容詞','形状詞'} and i+1<len(rows):
                    nxt,left,right=rows[i+1]
                    if (left==b and eligible(nxt) and feature(nxt,'pos1') in {'名詞','動詞','形容詞'}):
                        end=right;last=nxt
                        for aux,lft,rgt in rows[i+2:i+5]:
                            if lft!=end or feature(aux,'pos1')!='助動詞':break
                            end=rgt;last=aux
                        cf=feature(last,'cForm')
                        if not cf or cf.startswith(('終止形','連体形','命令形','意志推量形')):
                            put('expression',piece[a:end],'連語',piece[a:end],
                                components=[str(t.surface) for t,lft,rgt in rows[i:] if lft>=a and rgt<=end],
                                frame=piece[max(0,a-8):a]+'|'+piece[end:end+8])
                # Joined unknown content tokens recover some dictionary-split coins.
                end=b; unknown=bool(getattr(token,'is_unk',False))
                for nxt,left,right in rows[i+1:i+3]:
                    if left!=end or not eligible(nxt):break
                    end=right;unknown=unknown or bool(getattr(nxt,'is_unk',False))
                    form=piece[a:end]
                    if unknown and 3<=len(form)<=16:
                        put('surface',form,'未知語',form,signals=['unknown_span'],
                            frame=piece[max(0,a-8):a]+'|'+piece[end:end+8])
        self.docs.update(seen)
        for key,n in local.items():self.per_work[key][work_index]=n

    def dispersion(self, key, count: int) -> float:
        # DP = half the absolute difference between observed occurrence shares
        # and expected shares from document sizes, including absent documents.
        total=max(1,sum(self.work_sizes)); seen=self.per_work[key]
        expected_seen=sum(self.work_sizes[i]/total for i in seen)
        distance=sum(abs(n/count-self.work_sizes[i]/total) for i,n in seen.items())
        return round(max(0.0,min(1.0,.5*(distance+1-expected_seen))),6)

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
                'selection_version':VERSION,
                'nonstandard_signals':sorted(self.signals[key]),
                'context_diversity':len(self.contexts[key]),
                'token_count':self.token_lengths.get(key,1),
                'components':self.components.get(key,[]),
                'max_work_share':round(max(self.per_work[key].values(),default=0)/count,6),
                'dispersion_dp':self.dispersion(key,count)})
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
