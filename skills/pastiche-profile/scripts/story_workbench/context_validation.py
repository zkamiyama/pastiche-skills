"""Execution-time provenance validation shared by planning and draft auditing."""
from __future__ import annotations
from json_boundary import read_object
from .store import Conflict, position, read_source, uid


def current_patterns(store, scope: str) -> list[dict]:
    """Only current, persisted model-supported patterns; JSON alone is not proof."""
    path = store.path.parent / 'plot_patterns.json'
    if not path.exists():
        return []
    bundle = read_object(path)
    versions = bundle.get('source_versions')
    work_ids = bundle.get('work_ids')
    if (bundle.get('schema') != 'pastiche.plot.patterns.v1' or bundle.get('scope') != scope
            or not isinstance(versions, dict) or not versions
            or not isinstance(work_ids,list) or not work_ids
            or any(not isinstance(w,str) for w in work_ids)
            or len(set(work_ids)) != len(work_ids)):
        raise Conflict('plot provenance is missing or incompatible; reanalyze the corpus')
    if store.get('artifacts',uid('CORPUS',work_ids)) != bundle:
        raise Conflict('plot snapshot differs from the persisted analysis')
    for sid, expected in versions.items():
        source = store.get('sources',sid)
        if not source or source['scope'] != scope or source['sha256'] != expected:
            raise Conflict('plot source version is no longer registered')
        try:
            from pathlib import Path
            actual = read_source(Path(source['path']))[1]
        except (ValueError,OSError) as exc:
            raise Conflict('plot source is unavailable; reanalyze the corpus') from exc
        if actual != expected:
            raise Conflict('plot source changed; reanalyze the corpus')
    patterns = bundle.get('patterns')
    if not isinstance(patterns,list) or any(not isinstance(p,dict) for p in patterns):
        raise Conflict('invalid persisted plot patterns')
    return [p for p in patterns if p.get('status') == 'model_supported'
            and type(p.get('support_count')) is int and p['support_count'] >= 2]


def _records(values, name):
    if not isinstance(values,list) or any(not isinstance(v,dict) or not isinstance(v.get('id'),str) for v in values):
        raise Conflict('invalid context '+name)
    if len({v['id'] for v in values}) != len(values):
        raise Conflict('duplicate context '+name)
    return values


def validate_snapshot(store, context: dict) -> None:
    if not isinstance(context,dict) or context.get('schema') != 'pastiche.writer.context.v4':
        raise Conflict('writer context schema changed; export current v4 context')
    if any(not isinstance(context.get(k),str) or context[k] not in {'original','canon'} for k in ('mode','world_mode')):
        raise Conflict('invalid context mode; export it again')
    scope = context.get('scope')
    if not isinstance(scope,str) or not scope:
        raise Conflict('context requires a canon scope')
    cutoff = position(context.get('cutoff'))
    if cutoff is None:
        raise Conflict('context requires an explicit cutoff')
    graph = store.graph()
    for key,domain in (('characters','character'),('world','world'),('perspectives','epistemic')):
        section = context.get(key)
        if not isinstance(section,dict):
            raise Conflict('context section missing; export it again')
        current = store._context(scope,cutoff,domain,graph=graph)
        if any(section.get(k) != current[k] for k in ('schema','scope','cutoff','note')):
            raise Conflict('context scope/cutoff or schema differs; export it again')
        selected = _records(section.get('claims'),'claims')
        entities = _records(section.get('entities'),'entities')
        available = {c['id']:c for c in current['claims']}
        known = {e['id']:e for e in current['entities']}
        if any(available.get(c['id']) != c for c in selected):
            raise Conflict('writer context is changed, stale or revoked; export it again')
        if any(known.get(e['id']) != e for e in entities):
            raise Conflict('context entity identity changed; export it again')
        required = {c[k] for c in selected for k in ('display_subject','display_object') if c[k]}
        if domain == 'epistemic':
            required.update(c[k] for c in selected for k in ('holder','proposition_subject','speaker')
                            if c[k] and not c[k].startswith('$'))
        if not required <= {e['id'] for e in entities}:
            raise Conflict('context omits a referenced entity')
        if domain == 'world':
            from .world import conflict_candidates
            if section.get('conflicts') != conflict_candidates(selected):
                raise Conflict('context conflict summary changed; export it again')
    patterns = _records(context.get('plot_patterns'),'plot patterns')
    if patterns:
        available = {p['id']:p for p in current_patterns(store,scope)}
        if any(available.get(p['id']) != p for p in patterns):
            raise Conflict('plot selection changed or is stale; export it again')
