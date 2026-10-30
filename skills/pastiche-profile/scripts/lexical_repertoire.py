"""Small contextual lexical cards for writing; all forms are Python-bound."""
from __future__ import annotations
from collections import Counter, deque
import hashlib
import json
import math

SCHEMA = 'pastiche.lexical.repertoire.v1'
USEFUL_ROLES = frozenset({'colloquial','dialect','sound_symbolism','emphasis','emotion',
                         'orthography','discourse','sensory','figurative'})


def usable(row: dict) -> bool:
    return (row.get('work_count', 0) >= 1 and row.get('count', 0) >= 2
            and row.get('role') in USEFUL_ROLES and row.get('confidence') in {'medium','high'}
            and row.get('typicality') != 'uncertain' and bool(row.get('note'))
            and (row.get('selection_version') != 'lexical-discovery.v2'
                 or row.get('context_count', 0) > 0))


def preference_score(row: dict) -> float:
    # Evidence relevance and recurrence, not a claim of corpus uniqueness.
    return (row.get('style_signal', 0) + math.log1p(row['work_count'])
            + min(4, max(0, row.get('reference_log2_ratio', 0)))
            + (1 if row.get('nonstandard_signals') else 0))


def useful_rows(judgments: dict, limit: int = 0) -> list[dict]:
    groups = {}; seen = set()
    for row in sorted(judgments['assessments'],key=lambda r:(-preference_score(r),r['form'])):
        if not usable(row) or row['form'] in seen:continue
        seen.add(row['form'])
        groups.setdefault(row.get('discovery_lane','recurrent'),deque()).append(row)
    order = ('contrast','nonstandard','phrase','low_frequency','recurrent')
    result=[]
    while any(groups.values()) and (not limit or len(result)<limit):
        for lane in (*order, *sorted(set(groups)-set(order))):
            if groups.get(lane):
                result.append(groups[lane].popleft())
                if limit and len(result)>=limit:break
    return result


def build(judgments: dict, discovery: dict | None = None) -> dict:
    entries=[]
    for row in useful_rows(judgments):
        allowed=('form','kind','primary_pos','count','work_count','count_basis',
                 'observed_forms','typicality','role','confidence','note',
                 'discovery_lane','discovery_reasons','nonstandard_signals',
                 'dispersion_dp','max_work_share','context_count','evidence_scope',
                 'reference_count','reference_work_count','reference_log2_ratio')
        item={key:row[key] for key in allowed if key in row}
        item['entry_id']='V'+hashlib.sha256(json.dumps(
            [row.get('kind'),row['form'],row.get('primary_pos')],ensure_ascii=False).encode()).hexdigest()[:16]
        forms=[v['form'] for v in row.get('observed_forms',[]) if isinstance(v.get('form'),str)]
        item['preferred_surface']=row['form'] if row.get('kind') in {'lemma','surface','expression'} or row['form'] in forms else (forms[0] if forms else row['form'])
        item['priority']=round(preference_score(row),6)
        entries.append(item)
    return {'schema':SCHEMA,'reference_owner':'python',
        'reference_available':bool((discovery or {}).get('reference_available')),
        'reviewed_candidates':judgments.get('reviewed_count',len(judgments['assessments'])),
        'eligible_entries':len(entries), 'roles':dict(Counter(r['role'] for r in entries)),
        'entries':entries,
        'usage':'Select a small palette for the new scene; preserve attested spelling. No quotas, no inferred synonyms, no author-identity claim.'}
