#!/usr/bin/env python3
"""Full-story analysis, character research, local review and writing contracts."""
from __future__ import annotations
import argparse
import json
from json_boundary import read_object
from pathlib import Path
import sys
from execution import add_model_flags, model_from
from task_transport import TasksPending, add_task_commands, task_command

from story_workbench import VERSION
from story_workbench.store import Store, segments, atomic_json, dump


def demo(out):
    out.mkdir(parents=True, exist_ok=True)
    if (out / 'character_graph.sqlite3').exists():
        raise ValueError('demo requires a new output folder; existing data is never overwritten')
    p = out / 'demo-fiction.txt'
    p.write_text('ミナは手紙を読んだ。レンの説明を参考にすることにした。\n'
                 'レンはミナに問い返した。「まだ決めていないの？」\n'
                 'ミナは短く答えた。「うん、もう少しだけ」\n'
                 '二人は静かな駅で返事を待った。\n'
                 '白灯市は東湾に面している。ミナは航路組合で働く。\n'
                 '航路組合は白灯市の港を管理する。\n'
                 '浮航術には充電した潮石が必要だ。ただし灯台の中では予備電源を使える。\n' * 3, encoding='utf-8')
    store = Store(out / 'character_graph.sqlite3')
    try:
        source, body = store.register_source(p, 'demo:original', source_type='synthetic')
        units = segments(body)
        a = store.entity(source['id'], 'ミナ', source['scope'])
        b = store.entity(source['id'], 'レン', source['scope'])
        for subject, obj, kind, predicate, value, at in (
            (a, b, 'relationship', 'trusts', '相手の説明を判断材料にする', 0),
            (b, a, 'relationship', 'asks', '判断の理由を問い返す', 1),
            (a, None, 'voice', 'speaks', '短い応答でためらいを示す', 2)):
            q = store.evidence_for(source, units, [units[at]['id']], len(body))
            store.add_claim({'subject': subject, 'object': obj, 'kind': kind, 'predicate': predicate,
                             'value': value, 'epistemic': 'inferred', 'scope': source['scope'],
                             'observed_at': units[at]['end']/len(body), 'evidence_ids': [q]})
        world = {name: store.entity(source['id'], name, source['scope'], entity_type=typ)
                 for name, typ in [('白灯市', 'place'), ('東湾', 'place'), ('航路組合', 'organization'),
                                   ('浮航術', 'system'), ('潮石', 'artifact')]}
        for subject, obj, kind, predicate, value, at in (
                (world['白灯市'], world['東湾'], 'geography', 'faces', '湾に面する都市', 4),
                (a, world['航路組合'], 'affiliation', 'member_of', '組合で勤務している', 4),
                (world['航路組合'], world['白灯市'], 'institution', 'manages_port', '港の運営主体', 5),
                (world['浮航術'], world['潮石'], 'magic', 'requires', '充填された資源を必要とする', 6)):
            q = store.evidence_for(source, units, [units[at]['id']], len(body))
            store.add_claim({'subject': subject, 'object': obj, 'domain': 'world', 'kind': kind,
                             'predicate': predicate, 'value': value, 'scope': source['scope'],
                             'epistemic': 'explicit', 'polarity': 'affirmed',
                             'modality': 'capability' if kind == 'magic' else 'descriptive',
                             'conditions': ['潮石が充電されている'] if kind == 'magic' else [],
                             'exceptions': ['灯台内では予備電源を使用できる'] if kind == 'magic' else [],
                             'observed_at': units[at]['end']/len(body), 'evidence_ids': [q]})
        atomic_json(out / 'character_graph.json', store.graph())
        from story_workbench.world import export_world
        export_world(store, out, source['scope'])
    finally:
        store.close()
    return {'status': 'synthetic_demo', 'out': str(out)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--version', action='version', version=VERSION)
    commands = p.add_subparsers(dest='command', required=True)
    a = commands.add_parser('analyze', help='read all eligible story text; extract story hierarchy and character claims')
    a.add_argument('corpus', type=Path)
    a.add_argument('--out', type=Path, required=True)
    a.add_argument('--canon-scope', default='corpus:original')
    a.add_argument('--chunk-chars', type=int, default=3800)
    a.add_argument('--analysis-works', type=int, default=0)
    a.add_argument('--story-fanout', type=int, default=24)
    a.add_argument('--story-panel-size', type=int, default=12)
    a.add_argument('--story-rounds', type=int, default=3)
    a.add_argument('--restart', action='store_true')
    add_model_flags(a)
    r = commands.add_parser('research', help='follow approved public source links, then extract pending character claims')
    r.add_argument('--out', type=Path, required=True)
    r.add_argument('--seed', action='append', required=True)
    r.add_argument('--domain', action='append', required=True)
    r.add_argument('--character', required=True)
    r.add_argument('--universe', required=True)
    r.add_argument('--canon-scope', required=True)
    r.add_argument('--source-type', choices=('official', 'creator', 'licensed', 'fan', 'unclassified'), default='unclassified')
    r.add_argument('--max-pages', type=int, default=8)
    r.add_argument('--depth', type=int, default=1)
    r.add_argument('--delay', type=float, default=2)
    r.add_argument('--allow-http', action='store_true')
    add_model_flags(r)
    wr = commands.add_parser('world-research', help='bounded public source research for world rules, geography and institutions')
    wr.add_argument('--out', type=Path, required=True)
    wr.add_argument('--seed', action='append', required=True)
    wr.add_argument('--domain', action='append', required=True)
    wr.add_argument('--topic', required=True)
    wr.add_argument('--universe', required=True)
    wr.add_argument('--canon-scope', required=True)
    wr.add_argument('--source-type', choices=('official', 'creator', 'licensed', 'fan', 'unclassified'), default='unclassified')
    wr.add_argument('--max-pages', type=int, default=8)
    wr.add_argument('--depth', type=int, default=1)
    wr.add_argument('--delay', type=float, default=2)
    wr.add_argument('--allow-http', action='store_true')
    add_model_flags(wr)
    wx = commands.add_parser('world-export', help='refresh scoped world graph/bible from current reviewed or auto-accepted claims')
    wx.add_argument('--out', type=Path, required=True)
    wx.add_argument('--canon-scope', required=True)
    wx.add_argument('--cutoff', type=float, default=1)
    s = commands.add_parser('serve', help='local token-protected review editor; no inference or external network')
    s.add_argument('--out', type=Path, required=True)
    s.add_argument('--port', type=int, default=8765)
    e = commands.add_parser('export', help='export current human-reviewed character claims and grounded plot context')
    e.add_argument('--out', type=Path, required=True)
    e.add_argument('--canon-scope', required=True)
    e.add_argument('--cutoff', type=float, default=1)
    e.add_argument('--character-mode', choices=('original', 'canon'), default='original')
    e.add_argument('--character-id', action='append')
    e.add_argument('--world-mode', choices=('original', 'canon'), default='original')
    e.add_argument('--world-entity-id', action='append')
    w = commands.add_parser('plan', help='generate and validate a hierarchical new-story plan from an exported context')
    w.add_argument('--out', type=Path, required=True)
    w.add_argument('--context', type=Path, required=True)
    w.add_argument('--brief', type=Path, required=True)
    add_model_flags(w)
    v = commands.add_parser('audit-plan', help='offline validation of tree coverage, declared chronology and knowledge')
    v.add_argument('--plan', type=Path, required=True)
    v.add_argument('--context', type=Path, required=True)
    v.add_argument('--out', type=Path, required=True)
    d = commands.add_parser('audit-draft', help='LLM review with citations to actual new-draft passages')
    for flag in ('out', 'draft', 'plan', 'context'):
        d.add_argument('--'+flag, type=Path, required=True)
    add_model_flags(d)
    d = commands.add_parser('demo', help='make a new private synthetic editor fixture, without network access')
    d.add_argument('--out', type=Path, required=True)
    ep = commands.add_parser('perspectives', help='simulate a declared plan without an LLM')
    ep.add_argument('--plan', type=Path, required=True)
    ep.add_argument('--out', type=Path, required=True)
    view = commands.add_parser('scene-context', help='export a private viewpoint or an explicit director view')
    view.add_argument('--plan', type=Path, required=True)
    view.add_argument('--scene-id', required=True)
    view.add_argument('--character-id', help='required except for explicit --director')
    view.add_argument('--director', action='store_true')
    view.add_argument('--event-id', help='private state before a particular event in the scene')
    view.add_argument('--out', type=Path, required=True)
    add_task_commands(commands.add_parser('tasks', help='private host-subagent JSON handoff'))
    args = p.parse_args(argv)
    try:
        if args.command in {'perspectives', 'scene-context'}:
            from story_workbench.epistemic import simulate, scene_view
            plan = read_object(args.plan)
            if args.command == 'perspectives':
                result = simulate(plan['epistemic'], plan['scenes'], plan['characters'])
            else:
                if bool(args.character_id) == args.director:
                    raise ValueError('select --character-id OR --director')
                result = scene_view(plan, args.scene_id, args.character_id, event_id=args.event_id)
            atomic_json(args.out, result)
        elif args.command == 'tasks':
            result = task_command(args)
        elif args.command == 'analyze':
            from story_workbench.pipeline import analyze
            result = analyze(args.corpus, args.out, model_from(args), scope=args.canon_scope,
                chunk_chars=args.chunk_chars, workers=args.llm_workers, retries=args.retries, interval=args.progress_interval,
                works_limit=args.analysis_works, fanout=args.story_fanout, panel_size=args.story_panel_size,
                rounds=args.story_rounds, restart=args.restart)
        elif args.command in {'research', 'world-research'}:
            from story_workbench.research import research
            result = research(args.out, args.seed, {d.lower().rstrip('.') for d in args.domain}, model_from(args),
                character=args.character if args.command == 'research' else args.topic, universe=args.universe, scope=args.canon_scope, source_type=args.source_type,
                max_pages=args.max_pages, depth=args.depth, delay=args.delay, workers=args.llm_workers,
                retries=args.retries, interval=args.progress_interval, allow_http=args.allow_http,
                target_kind='world' if args.command == 'world-research' else 'character')
        elif args.command == 'world-export':
            from story_workbench.world import export_world
            store = Store(args.out / 'character_graph.sqlite3')
            try:
                result = export_world(store, args.out, args.canon_scope, args.cutoff)
            finally:
                store.close()
        elif args.command == 'serve':
            from story_workbench.server import serve
            serve(args.out, args.port)
            return 0
        elif args.command == 'export':
            from story_workbench.writing import prepare
            result = prepare(args.out, args.canon_scope, args.cutoff, mode=args.character_mode, character_ids=args.character_id,
                             world_mode=args.world_mode, world_entity_ids=args.world_entity_id)
        elif args.command in {'plan', 'audit-plan', 'audit-draft'}:
            from story_workbench import writing
            context = read_object(args.context)
            if args.command == 'plan':
                result = writing.make_plan(args.out, context, args.brief.read_text(encoding='utf-8'), model_from(args),
                                           retries=args.retries, interval=args.progress_interval)
            else:
                plan = read_object(args.plan)
                if args.command == 'audit-plan':
                    writing.validate_plan(plan, context)
                    result = {'status': 'declared_plan_valid', 'note': 'Checks declared facts and dependencies, not literary truth or draft prose.'}
                    atomic_json(args.out, result)
                else:
                    result = writing.audit_draft(args.out, args.draft, plan, context, model_from(args),
                                                 retries=args.retries, interval=args.progress_interval)
        else:
            result = demo(args.out)
        print(dump(result))
        return 0
    except TasksPending:
        print("Waiting for host subagent results; rerun after submission.", file=sys.stderr)
        return TasksPending.exit_code
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
