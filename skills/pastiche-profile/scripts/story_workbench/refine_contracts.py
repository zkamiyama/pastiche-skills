"""Closed contracts for evidence-bound style diagnosis, edits and blind comparison.

Only current-draft spans are editable. Profile compatibility is not an author
identity score, and these validators cannot prove natural-language equivalence.
"""
from __future__ import annotations
import hashlib
import re
from .store import dump, segments, text

VERSION = '1.0.0'
DIMENSIONS = {'lexicon', 'emotion', 'voice', 'dialogue', 'sensory', 'pacing'}
ASSESSMENTS = {'aligned', 'underused', 'overused', 'not_applicable', 'uncertain'}
ACTIONABLE = {'underused', 'overused'}
PREFERENCES = {'A', 'B', 'tie', 'uncertain'}
MAX_DRAFT = 16000


def digest(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def record(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError('missing or unexpected refine fields')
    return value


def rows(value, maximum):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError('refine array exceeds contract')
    return value


def refs(value, allowed, *, empty=False):
    values = rows(value, 64)
    if ((not empty and not values) or any(not isinstance(v, str) or v not in allowed for v in values)
            or len(values) != len(set(values))):
        raise ValueError('unknown, duplicate or missing refine evidence')
    return list(values)


def catalog(raw: dict, selected: list[str] | None = None) -> list[dict]:
    if raw.get('schema') != 'pastiche.qualitative.v1':
        raise ValueError('expected qualitative_patterns.json, not free-form style instructions')
    supported, seen = [], set()
    for p in rows(raw.get('patterns'), 10000):
        if not isinstance(p, dict) or not isinstance(p.get('pattern_id'), str):
            raise ValueError('invalid style pattern')
        pid = p['pattern_id']
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', pid) or pid in seen:
            raise ValueError('ambiguous style pattern ID')
        seen.add(pid)
        if p.get('status') != 'model_supported':
            continue
        if not isinstance(p.get('dimension'), str) or p['dimension'] not in DIMENSIONS:
            continue
        if (type(p.get('support_work_count')) is not int or p['support_work_count'] < 2
                or type(p.get('validation_passes')) is not int or p['validation_passes'] < 2):
            raise ValueError('supported style pattern lacks source-check provenance')
        evidence = p.get('evidence_ids')
        if (not isinstance(evidence, list) or not evidence
                or any(not isinstance(e, str) or not e for e in evidence)):
            raise ValueError('style pattern has no source evidence IDs')
        anchors = rows(p.get('anchors', []), 3)
        if any(not isinstance(a, str) or not 1 <= len(a) <= 12 for a in anchors):
            raise ValueError('lexical anchors must be short exact forms')
        supported.append({'pattern_id': pid, 'dimension': p['dimension'],
                          **{k: text(p.get(k), 400) for k in ('trigger', 'mechanism', 'effect')},
                          'anchors': anchors if p['dimension'] == 'lexicon' else [],
                          'support_work_count': p['support_work_count']})
    if selected is not None:
        refs(selected, {p['pattern_id'] for p in supported})
        supported = [p for p in supported if p['pattern_id'] in selected]
    if len(supported) > 32:
        raise ValueError('select at most 32 patterns with --pattern-id; never truncate silently')
    return supported


def review(raw, body, patterns):
    record(raw, ('draft_sha256', 'assessments'))
    if raw['draft_sha256'] != digest(body):
        raise ValueError('style diagnosis is for a different draft')
    allowed = {p['pattern_id'] for p in patterns}
    evidence = {u['id'] for u in segments(body)}
    result, seen = [], set()
    for row in rows(raw['assessments'], len(allowed)):
        record(row, ('pattern_id', 'status', 'evidence_ids', 'reason', 'instruction'))
        pid, status = row['pattern_id'], row['status']
        if (not isinstance(pid, str) or pid not in allowed or pid in seen
                or not isinstance(status, str) or status not in ASSESSMENTS):
            raise ValueError('invalid or repeated style assessment')
        seen.add(pid)
        ids = refs(row['evidence_ids'], evidence, empty=status in {'not_applicable', 'uncertain'})
        instruction = text(row['instruction'], 600, empty=status not in ACTIONABLE)
        if status not in ACTIONABLE and instruction:
            raise ValueError('aligned/inapplicable/uncertain patterns cannot request edits')
        result.append({'pattern_id': pid, 'status': status, 'evidence_ids': ids,
                       'reason': text(row['reason'], 600), 'instruction': instruction})
    if seen != allowed:
        raise ValueError('every selected pattern needs an assessment')
    return {'draft_sha256': raw['draft_sha256'], 'assessments': result}


def edits(raw, body, diagnosis):
    record(raw, ('draft_sha256', 'edits'))
    if raw['draft_sha256'] != digest(body) or diagnosis['draft_sha256'] != digest(body):
        raise ValueError('patch input hash mismatch')
    units = {u['id']: u for u in segments(body)}
    actionable = {a['pattern_id']: a for a in diagnosis['assessments'] if a['status'] in ACTIONABLE}
    result, spans, changed = [], [], 0
    for row in rows(raw['edits'], 12):
        record(row, ('segment_id', 'before', 'after', 'pattern_ids', 'reason'))
        sid = row['segment_id']
        if not isinstance(sid, str) or sid not in units:
            raise ValueError('patch refers to an unknown draft segment')
        ids = refs(row['pattern_ids'], set(actionable))
        if any(sid not in actionable[pid]['evidence_ids'] for pid in ids):
            raise ValueError('patch lacks an actionable diagnosis for this segment')
        before, after = row['before'], row['after']
        if (not isinstance(before, str) or not before or not isinstance(after, str)
                or len(before) > 1000 or len(after) > 2000 or before == after
                or any(ord(c) < 32 and c not in '\n\t' for c in after)):
            raise ValueError('invalid or unchanged patch text')
        unit = units[sid]
        if unit['text'].count(before) != 1:
            raise ValueError('patch must match one exact, unambiguous span in its segment')
        start = unit['start'] + unit['text'].index(before)
        stop = start + len(before)
        if any(start < b and a < stop for a, b in spans):
            raise ValueError('overlapping edits are not allowed')
        spans.append((start, stop)); changed += len(before)
        result.append({'segment_id': sid, 'before': before, 'after': after,
                       'pattern_ids': ids, 'reason': text(row['reason'], 600)})
    if changed > max(40, int(len(body) * .35)):
        raise ValueError('local refinement exceeds the 35% edited-span budget')
    clean = {'draft_sha256': raw['draft_sha256'], 'edits': result}
    candidate = apply_edits(body, clean)
    if (not candidate.strip() or len(candidate) > MAX_DRAFT
            or abs(len(candidate) - len(body)) > max(40, int(len(body) * .25))):
        raise ValueError('candidate violates the bounded length-change contract')
    return clean


def apply_edits(body: str, patch: dict) -> str:
    """Apply validated patches in reverse offset order, never by global replace."""
    if patch['draft_sha256'] != digest(body):
        raise ValueError('cannot apply patch to another draft version')
    units = {u['id']: u for u in segments(body)}
    spans = []
    for e in patch['edits']:
        unit = units[e['segment_id']]
        if unit['text'].count(e['before']) != 1:
            raise ValueError('patch target no longer matches')
        a = unit['start'] + unit['text'].index(e['before'])
        spans.append((a, a + len(e['before']), e['after']))
    spans.sort()
    if any(left[1] > right[0] for left, right in zip(spans, spans[1:])):
        raise ValueError('overlapping patch spans')
    for a, b, replacement in reversed(spans):
        body = body[:a] + replacement + body[b:]
    return body


def pair_id(a: str, b: str) -> str:
    return digest(dump([a, b]))


def comparison(raw, a, b, patterns):
    record(raw, ('pair_sha256', 'style_preference', 'quality_preference', 'meaning_preserved',
                 'pattern_ids', 'evidence_a', 'evidence_b', 'reason'))
    if raw['pair_sha256'] != pair_id(a, b):
        raise ValueError('comparison is for a different pair')
    for key in ('style_preference', 'quality_preference'):
        if not isinstance(raw[key], str) or raw[key] not in PREFERENCES:
            raise ValueError('invalid pairwise preference')
    if type(raw['meaning_preserved']) is not bool:
        raise ValueError('meaning preservation must be an explicit model judgment')
    return {**{k: raw[k] for k in ('pair_sha256', 'style_preference', 'quality_preference', 'meaning_preserved')},
            'pattern_ids': refs(raw['pattern_ids'], {p['pattern_id'] for p in patterns}),
            'evidence_a': refs(raw['evidence_a'], {u['id'] for u in segments(a)}),
            'evidence_b': refs(raw['evidence_b'], {u['id'] for u in segments(b)}),
            'reason': text(raw['reason'], 1000)}


def improves(diagnosis, revised):
    """Applicable patterns must improve without worsening another judgment."""
    previous = {r['pattern_id']: r['status'] for r in diagnosis['assessments']}
    current = {r['pattern_id']: r['status'] for r in revised['assessments']}
    if set(previous) != set(current):
        return False
    gains = any(old in ACTIONABLE and current[p] == 'aligned' for p, old in previous.items())
    no_regressions = all(current[p] == old or (old in ACTIONABLE and current[p] == 'aligned')
                         for p, old in previous.items())
    return gains and no_regressions


def accept_pair(results, candidate_labels):
    """Strict improvements on both orderings, with no quality/meaning regression."""
    return len(results) == 2 and candidate_labels in (['A', 'B'], ['B', 'A']) and all(
        r['style_preference'] == label and r['quality_preference'] in {label, 'tie'}
        and r['meaning_preserved'] is True for r, label in zip(results, candidate_labels))
