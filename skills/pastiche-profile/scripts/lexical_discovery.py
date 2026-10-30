"""Deterministic lexical triage: measure everything, review diverse shortlists.

Scores are discovery heuristics, not tests of author identity or population rarity.
No dependencies, network, normalization, model calls or corpus passages here.
"""
from __future__ import annotations
from collections import Counter, defaultdict, deque
import math
import re
from typing import Any

VERSION = 'lexical-discovery.v2'
LANES = ('contrast', 'nonstandard', 'phrase', 'low_frequency', 'recurrent')
POS_ORDER = ('副詞', '形容詞', '形状詞', '動詞', '連語', '名詞', '感動詞', '未知語')
FUNCTION_LIKE = frozenset({'よう','みたい','そう','もう','また','こと','もの','ため','ところ','これ','それ','あれ','どれ','ここ','そこ'})
JAPANESE = re.compile(r'[一-龥ぁ-ゖァ-ヺ]')
AUTO_MIN = 128
AUTO_MAX = 384


def identity(row: dict) -> tuple[str, str, str]:
    return row['kind'], row['form'], row['primary_pos']


def contrast(rows: list[dict], reference: list[dict], target_chars: int,
             reference_chars: int) -> list[dict]:
    """Compare the SAME units per character; smoothed effect size, not p-value."""
    if target_chars <= 0 or reference_chars <= 0:
        raise ValueError('positive corpus sizes required for lexical contrast')
    refs = {identity(row): row for row in reference}
    result = []
    for row in rows:
        other = refs.get(identity(row), {})
        n, r = row['count'], other.get('count', 0)
        ratio = math.log2(((n + .5) / (target_chars + 1)) /
                          ((r + .5) / (reference_chars + 1)))
        result.append(dict(row, reference_count=r,
            reference_work_count=other.get('work_count', 0),
            reference_log2_ratio=round(ratio, 6),
            reference_rate_basis='same_unit_per_nonwhitespace_character',
            # This attenuation is a ranking heuristic, NOT a confidence interval.
            reference_rank_score=round(max(0, ratio) * n / (n + 5), 6)))
    return result


def phrase_scores(rows: list[dict]) -> dict[tuple, float]:
    """C-value for bounded multi-token candidates; nesting follows token boundaries."""
    terms = {r['form']: r for r in rows if r['kind'] == 'expression'
             and r['count'] >= 2 and r.get('token_count', 1) >= 2}
    parents: dict[str, list[int]] = defaultdict(list)
    for long_form, row in terms.items():
        components = row.get('components', [])
        contained = set()
        for start in range(len(components)):
            for end in range(start + 2, len(components) + 1):
                short = ''.join(components[start:end])
                if short != long_form and short in terms:
                    contained.add(short)
        for short in contained:
            parents[short].append(min(row['count'], terms[short]['count']))
    result = {}
    for form, row in terms.items():
        nested = parents.get(form, [])
        independent = row['count'] - (sum(nested) / len(nested) if nested else 0)
        result[identity(row)] = max(0.0, math.log2(row['token_count']) * independent)
    return result


def rejection(row: dict) -> str | None:
    form = row.get('form', '')
    if (not isinstance(form, str) or not 2 <= len(form) <= 28
            or not JAPANESE.search(form) or any(c.isspace() for c in form)):
        return 'not_short_lexical_unit'
    if row.get('primary_pos') not in POS_ORDER or form in FUNCTION_LIKE:
        return 'functional_or_excluded_pos'
    if row.get('count', 0) < 2:
        return 'single_occurrence'
    if row.get('work_count', 0) < 2:
        # Repeated unusual spellings in one work can be inspected, not generalized.
        if row.get('count', 0) < 3 or not row.get('nonstandard_signals'):
            return 'single_work_without_form_signal'
    if row.get('headword_attested') is False and row.get('kind') == 'lemma':
        return 'unattested_dictionary_headword'
    if re.fullmatch(r'[ぁ-ゖァ-ヺ]+', form) and len(form) < 3:
        return 'short_fragment'
    if (form.endswith(('っ','ッ')) or re.match(r'^[ぁぃぅぇぉっゃゅょァィゥェォッャュョ]', form)):
        # A genuine unknown/sound form needs repeated multi-context evidence.
        if not (row.get('kind') == 'surface' and row.get('work_count', 0) >= 2
                and len(form) >= 3 and row.get('context_diversity', 0) >= 2):
            return 'clipped_fragment'
    return None


def adaptive_limit(eligible: int, requested: int = -1) -> int:
    if type(requested) is not int or requested < -1:
        raise ValueError('candidate limit must be -1 (auto), 0 (all), or positive')
    if requested == 0:
        return eligible
    if requested > 0:
        return min(requested, eligible)
    # Operational budget, not a statistical optimum. Expose actual truncation.
    return min(eligible, max(AUTO_MIN, min(AUTO_MAX, 32 + 8 * math.ceil(math.sqrt(eligible)))))


def _pos_interleave(rows: list[dict], score: str) -> deque:
    buckets = {p: deque(sorted((r for r in rows if r['primary_pos'] == p),
                    key=lambda r: (-r[score], -r['work_count'], r['form'], r['kind'])))
               for p in POS_ORDER}
    ordered = deque()
    while any(buckets.values()):
        for pos in POS_ORDER:
            if buckets[pos]:
                ordered.append(buckets[pos].popleft())
    return ordered


def select(rows: list[dict], limit: int = -1) -> tuple[list[dict], dict, list[dict]]:
    """All rows get a disposition; bounded selection is balanced across discoveries."""
    reviewed = []; exclusions = Counter(); decisions = []
    cvalues = phrase_scores(rows)
    for raw in rows:
        reason = rejection(raw)
        if reason:
            exclusions[reason] += 1
            decisions.append({'form': raw.get('form'), 'kind': raw.get('kind'),
                              'state': 'screened_out', 'reason': reason})
            continue
        row = dict(raw)
        n, d = row['count'], row['work_count']
        spread = max(.05, 1 - row.get('dispersion_dp', 0))
        recurrence = (2 * math.log1p(d) + math.log1p(n)) * (.4 + .6 * spread)
        signals = row.get('nonstandard_signals', [])
        diversity = min(4, row.get('context_diversity', 0))
        scores = {'recurrent': recurrence}
        if row.get('reference_log2_ratio', 0) >= 1 and n >= 3 and d >= 2:
            scores['contrast'] = row['reference_rank_score'] * math.log1p(d)
        if signals:
            scores['nonstandard'] = math.log1p(n) + math.log1p(d) + diversity + len(signals)
        if row['kind'] == 'expression':
            scores['phrase'] = math.log1p(cvalues.get(identity(row), n)) + math.log1p(d)
        if 2 <= n <= 12 and d >= 2:
            scores['low_frequency'] = math.log1p(d) + diversity + len(signals) + min(len(row['form']), 8)/8
        row.update(discovery_scores={k: round(v, 6) for k, v in scores.items()},
                   selection_score=round(max(scores.values()), 6),
                   evidence_scope='cross_work' if d >= 2 else 'single_work_candidate',
                   selection_version=VERSION)
        reviewed.append(row)
    # A displayed spelling is considered once; incompatible frequencies are NEVER added.
    unique = {}
    for row in sorted(reviewed, key=lambda r:(-len(r['discovery_scores']),
                       -r['selection_score'], r['kind'] != 'expression', r['kind'], r['form'])):
        if row['form'] in unique:
            exclusions['duplicate_representation'] += 1
            decisions.append({'form':row['form'],'kind':row['kind'],
                              'state':'screened_out','reason':'duplicate_representation'})
        else:
            unique[row['form']] = row
    pool = list(unique.values())
    budget = adaptive_limit(len(pool), limit)
    queues = {}
    for lane in LANES:
        members = [dict(r, lane_score=r['discovery_scores'][lane]) for r in pool
                   if lane in r['discovery_scores']]
        queues[lane] = _pos_interleave(members, 'lane_score')
    chosen = []; seen = set(); selected_lanes = Counter()
    while any(queues.values()) and len(chosen) < budget:
        for lane in LANES:
            q = queues[lane]
            while q and q[0]['form'] in seen:
                q.popleft()
            if not q: continue
            row = q.popleft(); row.pop('lane_score', None)
            row['discovery_lane'] = lane
            row['discovery_reasons'] = [k for k in LANES if k in row['discovery_scores']]
            seen.add(row['form']); chosen.append(row); selected_lanes[lane] += 1
            if len(chosen) >= budget: break
    chosen = [dict(row, id=f'L{i:04d}') for i,row in enumerate(chosen, 1)]
    for row in pool:
        decisions.append({'form':row['form'],'kind':row['kind'],
            'state':'selected' if row['form'] in seen else 'deferred_budget',
            'reasons':list(row['discovery_scores'])})
    audit = {'schema': VERSION, 'all_units_examined':len(rows), 'eligible_unique':len(pool),
        'screened_out':dict(exclusions), 'requested_limit':limit, 'effective_budget':budget,
        'selected':len(chosen), 'deferred_budget':len(pool)-len(chosen),
        'selected_by_lane':dict(selected_lanes),
        'reference_available':any('reference_log2_ratio' in r for r in rows),
        'method':'stratified deterministic triage; not exhaustive rarity detection or significance testing'}
    return chosen, audit, decisions
