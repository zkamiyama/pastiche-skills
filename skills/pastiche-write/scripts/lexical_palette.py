#!/usr/bin/env python3
"""Select a small lexical palette for a NEW scene; optionally audit exact forms.

No source corpus, tokenizer, model call or network. Selection is lexical retrieval,
not proof of semantic suitability. The writer makes the final contextual choice.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

SCHEMA = 'pastiche.lexical.repertoire.v1'
ROLES = {'colloquial','dialect','sound_symbolism','emphasis','emotion','orthography','discourse','sensory','figurative'}
HINTS = {'sensory':('手触り','感触','質感','匂い','温度','料理','飲み物','触覚'),
         'emotion':('緊張','不安','喜び','悲しみ','感情','動揺'),
         'sound_symbolism':('音','響き','雨','動き','擬音'),
         'colloquial':('会話','台詞','雑談','口語'),
         'figurative':('比喩','たとえ','象徴'),
         'emphasis':('強調','激しい','強い'),
         'discourse':('転換','対比','独白')}


def read(path: Path) -> str:
    raw = path.read_bytes()
    if len(raw) > 8_000_000: raise ValueError('input too large')
    return raw.decode('utf-8-sig')


def load(path: Path) -> dict:
    value = json.loads(read(path))
    if not isinstance(value,dict) or value.get('schema') != SCHEMA:
        raise ValueError('invalid lexical repertoire schema')
    if not isinstance(value.get('entries'),list):raise ValueError('entries required')
    seen=set()
    for row in value['entries']:
        if (not isinstance(row,dict) or not isinstance(row.get('entry_id'),str)
                or row['entry_id'] in seen or not isinstance(row.get('form'),str)
                or not 1<=len(row['form'])<=28 or row.get('role') not in ROLES
                or not isinstance(row.get('note'),str) or len(row['note'])>16384):
            raise ValueError('invalid or duplicate lexical entry')
        seen.add(row['entry_id'])
    return value


def grams(text: str) -> set[str]:
    pieces = re.findall(r'[一-龥ぁ-ゖァ-ヺA-Za-z]+',text)
    return {p[i:i+n] for p in pieces for n in (2,3) for i in range(max(0,len(p)-n+1))}


def select(value: dict, brief: str, limit: int = 16) -> list[dict]:
    if not 1<=limit<=48:raise ValueError('palette limit must be 1..48')
    query=grams(brief); scored=[]
    for row in value['entries']:
        words=grams(row['form']+' '+row['note'])
        relevance=len(query & words)/max(1,len(query))
        relevance+=sum(h in brief for h in HINTS.get(row['role'],()))*.2
        if row['form'] in brief:relevance+=2
        scored.append((relevance,float(row.get('priority',0)),row))
    scored.sort(key=lambda v:(-v[0],-v[1],v[2]['form']))
    selected=[];roles=Counter();lanes=Counter()
    # Prefer matching scenes, then retain some discovery diversity on ties.
    while scored and len(selected)<limit:
        eligible=[x for x in scored if roles[x[2]['role']] < max(2,(limit+3)//4)]
        if not eligible:break
        best=max(eligible,key=lambda x:(x[0],x[1]-.4*lanes[x[2].get('discovery_lane','recurrent')]))
        scored.remove(best);row=best[2]
        keep=('entry_id','form','preferred_surface','kind','role','note','observed_forms',
              'count','work_count','count_basis','evidence_scope','discovery_lane')
        item={k:row[k] for k in keep if k in row}
        item['brief_match']=round(best[0],4)
        selected.append(item);roles[row['role']]+=1;lanes[row.get('discovery_lane','recurrent')]+=1
    return selected


def exact_usage(entries: list[dict], draft: str) -> list[dict]:
    results=[]
    for row in entries:
        # Orthographic-base stems may not be standalone words. Report substring
        # hits only; never equate these counts with morphological word counts.
        forms={v['form'] for v in row.get('observed_forms',[]) if isinstance(v.get('form'),str)
               and 2<=len(v['form'])<=28}
        if row.get('kind') in {'expression','surface'}:forms.add(row['form'])
        if not forms:
            results.append({'entry_id':row['entry_id'],'form':row['form'],'hits':None,'status':'no_attested_surfaces'})
            continue
        pattern=re.compile('|'.join(re.escape(f) for f in sorted(forms,key=lambda s:(-len(s),s))))
        counts=Counter(m.group() for m in pattern.finditer(draft))
        results.append({'entry_id':row['entry_id'],'form':row['form'],'hits':sum(counts.values()),
                        'matched_forms':dict(counts),'status':'observed' if counts else 'not_observed'})
    return results


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repertoire',type=Path,required=True)
    parser.add_argument('--brief',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--draft',type=Path)
    parser.add_argument('--limit',type=int,default=16)
    args=parser.parse_args(argv)
    try:
        value=load(args.repertoire);brief=read(args.brief);entries=select(value,brief,args.limit)
        result={'schema':'pastiche.lexical.palette.v1',
            'repertoire_sha256':hashlib.sha256(args.repertoire.read_bytes()).hexdigest(),
            'brief_sha256':hashlib.sha256(brief.encode()).hexdigest(),'entries':entries,
            'instruction':'Use only context-appropriate entries. Preserve attested spelling instead of generic synonyms. No insertion quotas; absence is not failure.'}
        if args.draft:
            draft=read(args.draft)
            result['usage']=exact_usage(entries,draft)
            result['audit_basis']='longest-first exact substrings, not morphology or semantic judgment'
        args.out.parent.mkdir(parents=True,exist_ok=True)
        temp=args.out.with_name(args.out.name+'.tmp')
        temp.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        temp.replace(args.out)
        print(json.dumps({'selected':len(entries),'output':str(args.out)},ensure_ascii=False))
        return 0
    except (OSError,ValueError,TypeError,KeyError) as exc:
        print('lexical palette failed: '+type(exc).__name__,file=sys.stderr)
        return 2


if __name__=='__main__':raise SystemExit(main())
