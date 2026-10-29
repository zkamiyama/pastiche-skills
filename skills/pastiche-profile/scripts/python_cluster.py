"""Python-owned clustering: bound candidate/group requests, never model IDs.

Text similarity only ranks a bounded shortlist. Each attachment requires a
semantic verdict against ALL listed members. No transitive union of pair edges.
"""
from __future__ import annotations
from typing import Any, Callable
import llm_runtime
import llm_style_core as core
import prose_contracts as prose
from prose_runtime import call_prose
from task_transport import TasksPending

SHORTLIST_LIMIT = 3

def _features(item: dict) -> set[str]:
    text = ''.join(''.join(str(v) for v in prose.descriptions(item).values()).split())
    return {text[i:i+2] for i in range(max(0, len(text)-1))}

def _similarity(candidate: dict, group: list[dict]) -> float:
    a = _features(candidate); b = set().union(*(_features(item) for item in group))
    return len(a & b) / max(1, len(a | b))

def validate_panel(dimension: str, panel: list[dict]) -> None:
    if dimension not in core.DIMENSIONS or not isinstance(panel, list) or len(panel) < 2:
        raise ValueError('invalid Python clustering panel')
    seen, sources = set(), set()
    for item in panel:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id']:
            raise ValueError('invalid Python node identifier')
        if item['id'] in seen or item.get('dimension') != dimension:
            raise ValueError('duplicate node or mismatched dimension')
        if not all(isinstance(item.get(k), str) for k in ('trigger', 'technique', 'effect')):
            raise ValueError('invalid Python node descriptions')
        members = item.get('members')
        if (not isinstance(members, list) or not members or
                not all(isinstance(v, str) and v for v in members) or
                len(set(members)) != len(members) or sources.intersection(members)):
            raise ValueError('overlapping or invalid original members')
        seen.add(item['id']); sources.update(members)

def call_cluster(dimension: str, panel: list[dict[str, Any]], model: Any,
                 reporter: llm_runtime.ProgressReporter, *, retries: int,
                 round_number: int, panel_number: int,
                 record_status: bool = True, retry_delay: float = 2.0,
                 on_request: Callable[[], None] | None = None) -> dict:
    validate_panel(dimension, panel)
    if type(retries) is not int or retries < 0: raise ValueError('invalid clustering retry budget')
    key = core.digest('cluster-python-owned', {'contract': prose.VERSION,
                       'dimension': dimension, 'panel': panel}, model)
    diagnostic = reporter.out / 'style_cluster_diagnostics' / (key + '.json')
    comparisons = 0; calls = 0; groups = []
    label = f'STYLE CLUSTER {dimension} round={round_number} panel={panel_number}'
    def requested():
        nonlocal calls
        calls += 1
        if on_request: on_request()
    def record(state: str):
        llm_runtime.atomic_json_write(diagnostic, {
            'schema': 'pastiche.cluster.python-owned.v1', 'contract': prose.VERSION,
            'dimension': dimension, 'round': round_number, 'panel': panel_number,
            'panel_size': len(panel), 'state': state, 'comparisons': comparisons,
            'model_requests': calls, 'maximum_comparisons': SHORTLIST_LIMIT*(len(panel)-1),
            'maximum_attempts_per_comparison': retries+1, 'shortlist_limit': SHORTLIST_LIMIT,
            'note': 'Python owns identities/membership. Shortlist ranking is not a semantic verdict.'})
    record('running')
    try:
        for node in panel:
            ranked = sorted(range(len(groups)),
                key=lambda i: (-_similarity(node, groups[i]['nodes']), i))[:SHORTLIST_LIMIT]
            joined = False
            for gi in ranked:
                group = groups[gi]; comparisons += 1
                answer = call_prose(model, reporter, kind='cluster-attachment',
                    binding={'dimension': dimension, 'candidate': node, 'group': group['nodes']},
                    prompt=prose.comparison_prompt(dimension, node, group['nodes']),
                    parser=prose.parse_comparison, label=f'{label} comparison={comparisons}',
                    retries=retries, on_request=requested, retry_delay=retry_delay)
                if answer['compatible']:
                    group['nodes'].append(node); group['label'] = answer['mechanism']
                    joined = True; break
            if not joined: groups.append({'nodes': [node], 'label': ''})
            record('running')
        result = {'groups': [{'ids': [item['id'] for item in group['nodes']],
            'label': group['label']} for group in groups if len(group['nodes']) >= 2]}
        merges = core.parse_clusters(result, {n['id'] for n in panel})
        rebuilt = core.regroup(panel, merges)
        original = sorted(v for n in panel for v in n['members'])
        retained = sorted(v for n in rebuilt for v in n['members'])
        if original != retained or len(retained) != len(set(retained)):
            raise ValueError('Python clustering lost or duplicated membership')
        record('completed')
        reporter.log(f'validated {label}: groups={len(merges)} comparisons={comparisons} requests={calls}')
        return result
    except TasksPending:
        record('waiting_for_tasks'); raise
    except BaseException as exc:
        record('interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed'); raise
