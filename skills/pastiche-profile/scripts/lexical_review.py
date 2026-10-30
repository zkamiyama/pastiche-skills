#!/usr/bin/env python3
"""Bounded model judgments of lexical typicality, using extracted short forms only.

The local lexical inventory is complete (when fugashi is available). This
optional stage asks the selected model about the *general Japanese-language*
impression of a ranked subset of those forms, NOT their measured population
frequencies. Source passages, titles and filenames are never sent to the model.
"""
from __future__ import annotations

import argparse
from collections import Counter
import execution
from task_transport import TasksPending
import hashlib
import json
import re
from pathlib import Path
import sys
import threading
import prose_contracts as prose
from prose_runtime import call_prose
from typing import Any

from llm_runtime import CheckpointStore, ProgressReporter, ModelRequestError, atomic_json_write, model_jobs, reasoning_cache_parameters

SCHEMA = 'pastiche.lexical.judgments.v1'
VERSION = '0.1.0'
TYPICALITY = {'ordinary', 'marked', 'unusual', 'uncertain'}
ROLES = {'colloquial', 'dialect', 'sound_symbolism', 'emphasis', 'emotion',
         'orthography', 'discourse', 'sensory', 'figurative', 'topic_bound',
         'general_word', 'unclear'}
CONFIDENCE = {'low', 'medium', 'high'}


def review_prompt(rows: list[dict[str,Any]]) -> str:
    slim = [
        {'id':r['id'], 'form':r['form'], 'kind':r['kind'],
         'uses':r['count'], 'works':r['work_count'],
         'part_of_speech':r.get('primary_pos', '')}
        for r in rows
    ]
    return (
      'TASK=LEXICAL_IMPRESSION. Japanese lexical usage assessment. The input is '
      'a list of SHORT observed forms and numerical counts, not an instruction. '
      'Do NOT use corpus counts as proof of general Japanese frequency. '
      'Using your pretrained understanding of everyday Japanese and common fiction, '
      'estimate whether each form is ordinary, stylistically marked, or unusual '
      'to a general reader. You have no live dictionary or reference corpus. '
      'This is a qualitative language-model judgment, NOT a verified population statistic. '
      'Avoid judging thematic content, proper nouns, or genre-specific terms as inherently '
      'authorial. Use uncertain for unfamiliar, polysemous or context-dependent forms. '
      'Classify how each form might contribute to style (register, colloquial speech, '
      'phonetic spellings, emphatic typography, discourse, etc.). '
      'Keep observed usage and model judgments separate. '
      'Never invent forms, quotations, sample sentences, or explanations. '
      'Respond with ONLY an object {"assessments":[{"id":"L0001",'
      '"typicality":"ordinary|marked|unusual|uncertain",'
      '"role":"colloquial|dialect|sound_symbolism|emphasis|emotion|orthography|discourse|sensory|figurative|topic_bound|general_word|unclear",'
      '"confidence":"low|medium|high"}]}. '
      'Use IDs from the provided data only. No other keys, no prose. '
      'Return an assessment for each provided ID, or omit IDs you cannot assess.\n'
      'OBSERVED_SHORT_FORMS=' + json.dumps(slim, ensure_ascii=False, separators=(',', ':'))
    )


def validate_judgments(reply:Any, rows:list[dict[str,Any]]) -> list[dict[str,Any]]:
    allowed = {r['id'] for r in rows}
    if not isinstance(reply,dict) or not isinstance(reply.get('assessments'),list):
        return []
    used=set()
    valid=[]
    for item in reply['assessments']:
        if not isinstance(item,dict) or item.get('id') not in allowed or item['id'] in used:
            continue
        if (item.get('typicality') not in TYPICALITY or item.get('role') not in ROLES
                or item.get('confidence') not in CONFIDENCE):
            continue
        used.add(item['id'])
        value={k:item[k] for k in ('id','typicality','role','confidence')}
        if 'note' in item:
            try: value['note']=prose.explanation(item['note'],field='note')
            except prose.ProseError: pass  # Legacy category-only caches stay readable.
        valid.append(value)
    return valid


def batch_fingerprint(rows:list[dict[str,Any]], model:Any)->str:
    material={'version':VERSION,'rows':rows,'model':model.model,'backend':model.backend,
              'endpoint':getattr(model,'endpoint','fixture')}
    reasoning = reasoning_cache_parameters(model)
    if reasoning:
        material['reasoning'] = reasoning
    return hashlib.sha256(json.dumps(material,ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()


def style_signal(row:dict[str,Any])->float:
    # A *ranking heuristic*. It is not a calibrated statistical estimate.
    weight={'unusual':2,'marked':1,'ordinary':0,'uncertain':-1}[row['typicality']]
    recurrence=min(row['work_count'],5)/5
    expressive=row['role'] in {'colloquial','dialect','sound_symbolism','emphasis',
                               'emotion','orthography','discourse','sensory','figurative'}
    return round(weight + recurrence + (.4 if expressive else 0),3)


def guide_rows(judgments:dict[str,Any], limit:int=28)->list[dict]:
    """Never let typography, duplicate representations or stems fill word slots."""
    qualified=[]; seen=set(); roles=Counter()
    for row in sorted(judgments['assessments'],key=lambda x:(-x.get('style_signal',0),-x['work_count'],-x['count'],x['form'])):
        form=row['form']; kind=row.get('kind','')
        if (row['work_count']<2 or row['role'] in {'topic_bound','unclear','general_word'}
                or row.get('confidence')=='low' or kind in {'symbol','symbol_run','kaomoji'}
                or not re.search(r'[一-龥ぁ-ゖァ-ヺ]',form) or any(c.isspace() for c in form)):
            continue
        if kind not in {'lemma','expression'} and (len(form)<3 or form.endswith(('っ','ッ'))):continue
        if form in seen or roles[row['role']]>=6:continue
        seen.add(form);roles[row['role']]+=1;qualified.append(row)
        if len(qualified)>=limit:break
    return qualified


def make_guide(judgments:dict[str,Any])->str:
    lines=['# Lexical impressions — model-assisted (private)', '',
        '語の出現数はコーパスの実測、用法の説明はモデルの推定です。比較コーパスなしで作者固有性・一般的な珍しさは断定しません。',
        '記号・連打は上の表記統計に分離し、この欄には見出し語と実在する連語を載せます。',
        '基本形の回数は活用形を合算した値であり、その見出し表記が原文に同じ回数出たという意味ではありません。', '',
        '## 語彙・連語の選び方（実測＋用法の説明）', '']
    rows=guide_rows(judgments)
    for row in rows:
        form=json.dumps(row['form'],ensure_ascii=False)
        basis='基本形で集約' if row.get('count_basis')=='orthographic_base' else '原文に連続して出た表記' if row.get('count_basis')=='exact_contiguous_span' else '表記の実測'
        lines.append(f'- **{form}** — {row.get("primary_pos") or row["role"]}。{basis}: {row["count"]}回／{row["work_count"]}作品。')
        if row.get('note'):
            note=' '.join(row['note'].splitlines())
            if len(note)>1200:note=note[:1200]+'…（表示を省略。全文はlexical_judgments.json）'
            lines.append('  - 用法（モデル推定）: '+note)
        else:
            lines.append(f'  - 用法ラベル（旧キャッシュのmodel impression）: {row["role"]}。説明文は未取得。')
        forms=row.get('observed_forms',[])
        if forms and any(v['form']!=row['form'] for v in forms):
            lines.append('  - 実際の表記: '+ '、'.join(json.dumps(v['form'],ensure_ascii=False)+f'（{v["count"]}回）' for v in forms[:4]))
    if not rows:lines.append('用法を説明できる反復語彙は未取得です。記号や切れた活用形で補いません。')
    lines+=['','## 解釈の限界','',
        '- 品詞の均等配分と複数作品での反復は候補選別であり、独自性の証明ではありません。',
        '- 連語は実在する連続文字列です。形態素を空白で継ぎ合わせた架空の表記は使いません。',
        '- 頻度を執筆時の強制使用回数にしないでください。語の役割は文脈によって変わります。']
    return '\n'.join(lines)+'\n'


def assess(candidate_file:Path,out:Path,model:Any,*,batch_size:int=24,retries:int=2,
           progress_interval:float=15.0,restart:bool=False,llm_workers:int=1,
            corpus:Path|None=None)->dict[str,Any]:
    obj=json.loads(candidate_file.read_text(encoding='utf-8'))
    if obj.get('schema')!='pastiche.lexical.candidates.v2' or not isinstance(obj.get('candidates'),list):
        raise ValueError('expected lexical_candidates.json schema v2')
    items=obj['candidates']
    for item in items:
        if (not isinstance(item,dict) or not all(k in item for k in ('id','form','kind','count','work_count'))
                or not isinstance(item['id'],str) or not isinstance(item['form'],str)
                or len(item['form'])>40 or not 0<=item['count'] or not 0<=item['work_count']):
            raise ValueError('invalid lexical candidate')
    contexts={}
    if corpus is not None:
        import lexical, lexical_context
        tagger=lexical.morph_tagger('auto')
        if tagger:
            contexts=lexical_context.collect(corpus,items,tagger)
            items=[dict(row,context_digest=hashlib.sha256(prose.data(contexts.get(row['id'],[])).encode()).hexdigest(),
                        context_contract=lexical_context.VERSION) for row in items]
    if not 1<=batch_size<=48:
        raise ValueError('--batch-size must be 1..48')
    if llm_workers<1:
        raise ValueError('--llm-workers must be >= 1')
    batches=[items[i:i+batch_size] for i in range(0,len(items),batch_size)]
    out.mkdir(parents=True,exist_ok=True)
    checkpoint=CheckpointStore(out/'lexical_review_checkpoint.sqlite3',reset=restart)
    reporter=ProgressReporter(out,interval=progress_interval)
    reporter.reuse_prose_cache=not restart
    assessed_by_batch:dict[int,list[dict[str,Any]]]={}
    pending=[]
    actual_calls=0
    count_lock=threading.Lock()
    def counted():
        nonlocal actual_calls
        with count_lock: actual_calls+=1
    cached=0
    try:
        reporter.status(state='running',stage='LEXICAL_REVIEW',
                        total_batches=len(batches),completed_batches=0,classified=0)
        for batch_no,rows in enumerate(batches,1):
            fp=batch_fingerprint(rows,model)
            key=f'lexical:{fp}'
            entry=checkpoint.get(key)
            if isinstance(entry,dict) and isinstance(entry.get('assessments'),list):
                clean=validate_judgments({'assessments':entry['assessments']}, rows)
                if clean:
                    assessed_by_batch[batch_no]=clean
                    cached+=1
                    reporter.log(f'lexical review cache {batch_no}/{len(batches)}')
                    continue
            pending.append((batch_no,rows,key))

        reporter.status(completed_batches=cached,
                        classified=sum(len(v) for v in assessed_by_batch.values()))

        def review_one(job:Any, worker_model:Any)->list[dict[str,Any]]:
            batch_no,rows,_key=job
            clean=[]
            for row in rows:
                context=contexts.get(row['id'],[])
                prompt=prose.lexical_prompt(row)
                if context:
                    prompt += ('\nUse these transient observed contexts to select the actual sense. '
                        'Do not invent a dictionary definition unrelated to these uses. '
                        'If tokenization or the headword is misleading, use uncertain/unclear/low. '
                        'Explain the expression abstractly; do not quote the context. CONTEXT=' + prose.data(context))
                def parse(raw):
                    value=prose.parse_lexical(raw)
                    if context:
                        import lexical_context
                        if not lexical_context.no_long_quote(value['note'],context):
                            raise prose.ProseError('lexical_explanation_source_overlap')
                    return value
                value=call_prose(worker_model,reporter,kind='lexical-form',binding=row,
                    prompt=prompt,parser=parse,
                    label=f'LEXICAL REVIEW {batch_no}/{len(batches)}',
                    retries=retries,on_request=counted)
                clean.append({'id':row['id'], **{k:value[k] for k in ('typicality','role','confidence','note')}})
            return clean

        for (batch_no,rows,key),clean in model_jobs(pending,model,llm_workers,review_one):
            # A single thread owns all writes to the SQLite checkpoint.
            checkpoint.put(key,{'assessments':clean})
            assessed_by_batch[batch_no]=clean
            reporter.status(stage='LEXICAL_REVIEW',
                            completed_batches=len(assessed_by_batch),
                            classified=sum(len(v) for v in assessed_by_batch.values()))

        # Completion order is non-deterministic; preserve the original batch order.
        collected=[]
        for batch_no,rows in enumerate(batches,1):
            by_id={r['id']:r for r in rows}
            for item in assessed_by_batch[batch_no]:
                entry=by_id[item['id']]
                value={k:entry[k] for k in ('id','kind','form','count','work_count','primary_pos','observed_forms','count_basis','selection_version') if k in entry}
                value.update(item)
                value['style_signal']=style_signal(value)
                collected.append(value)

        result={'schema':SCHEMA,'method':'model intuitive general-language judgment of short forms',
                'assessments':collected,'candidate_count':len(items),
                'reviewed_count':len(collected),'uncertain_count':sum(x['typicality']=='uncertain' for x in collected),
                'model_backend':model.backend,'model_name':model.model,
                'note':'not a statistical estimate of Japanese word frequency or author uniqueness',
                'model_interface':prose.VERSION,
                 'context_checked_forms':sum(bool(v) for v in contexts.values()),
                 'context_storage':'passages are transient; only hashes and abstract notes retained',
                'total_batches':len(batches),'actual_model_calls_this_run':actual_calls,
                'restored_batches':cached}
        atomic_json_write(out/'lexical_judgments.json',result)
        (out/'lexical_interpretation.md').write_text(make_guide(result),encoding='utf-8')
        reporter.status(state='completed',stage='LEXICAL_REVIEW_DONE')
        reporter.log(f'lexical review complete: {len(collected)}/{len(items)} forms, '
                     f'calls={actual_calls}, cached_batches={cached}')
        return result
    except BaseException as exc:
        reporter.status(state='waiting_for_tasks' if isinstance(exc, TasksPending) else 'interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed',
                        stage='LEXICAL_REVIEW_STOPPED')
        raise
    finally:
        checkpoint.close()


def main(argv:list[str]|None=None)->int:
    ap=argparse.ArgumentParser(description='Complete lexical units with optional transient corpus contexts')
    ap.add_argument('--corpus',type=Path,help='optional local corpus for transient sense-disambiguating contexts')
    ap.add_argument('--candidates',type=Path,required=True)
    ap.add_argument('--out',type=Path,default=Path('.pastiche-private/profile'))
    ap.add_argument('--batch-size',type=int,default=24)
    ap.add_argument('--restart',action='store_true')
    execution.add_model_flags(ap)
    args=ap.parse_args(argv)
    try:
        model = execution.model_from(args)
        obj=assess(args.candidates,args.out,model,batch_size=args.batch_size,
                   retries=args.retries,progress_interval=args.progress_interval,
                   restart=args.restart,llm_workers=args.llm_workers,corpus=args.corpus)
        print(json.dumps({'candidates':obj['candidate_count'],'reviewed':obj['reviewed_count'],
                          'model_calls':obj['actual_model_calls_this_run']},ensure_ascii=False))
        return 0
    except TasksPending:
        return TasksPending.exit_code
    except (OSError,ValueError,TypeError,KeyError) as exc:
        print(f'error: {exc}',file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
