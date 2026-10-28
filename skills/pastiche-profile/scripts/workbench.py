#!/usr/bin/env python3
"""Full-story analysis, character research, local review and writing contracts."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

from story_workbench import VERSION
from story_workbench.store import Store, segments, atomic_json, dump


def model_flags(p):
    p.add_argument('--api-base', required=True)
    p.add_argument('--backend', choices=('auto', 'ollama', 'openai-compatible'), default='auto')
    p.add_argument('--model')
    p.add_argument('--api-key-env')
    p.add_argument('--timeout', type=int, default=0)
    p.add_argument('--retries', type=int, default=2)
    p.add_argument('--llm-workers', type=int, default=1)
    p.add_argument('--progress-interval', type=float, default=15)
    p.add_argument('--max-output-tokens', type=int, default=0)
    p.add_argument('--no-stream', action='store_true')


def model_from(args):
    import run
    import narrative_fast
    backend, endpoint, base, metadata = run.resolve_url(args.api_base, args.backend)
    name = run.model_id(args.model, base, backend, metadata)
    print('[workbench] Selected passages/abstract records will be sent only to the configured model endpoint.', file=sys.stderr)
    return narrative_fast.LimitedModel(backend, name, endpoint, args.timeout, allow_remote=True,
                                      allow_insecure_http=True, api_key_env=args.api_key_env,
                                      stream=not args.no_stream, max_output_tokens=args.max_output_tokens)


def demo(out):
    out.mkdir(parents=True, exist_ok=True)
    if (out / 'character_graph.sqlite3').exists():
        raise ValueError('demo requires a new output folder; existing data is never overwritten')
    p = out / 'demo-fiction.txt'
    p.write_text('ミナは手紙を読んだ。レンの説明を参考にすることにした。\n'
                 'レンはミナに問い返した。「まだ決めていないの？」\n'
                 'ミナは短く答えた。「うん、もう少しだけ」\n'
                 '二人は静かな駅で返事を待った。\n' * 3, encoding='utf-8')
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
        atomic_json(out / 'character_graph.json', store.graph())
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
    model_flags(a)
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
    model_flags(r)
    s = commands.add_parser('serve', help='local token-protected review editor; no inference or external network')
    s.add_argument('--out', type=Path, required=True)
    s.add_argument('--port', type=int, default=8765)
    e = commands.add_parser('export', help='export current human-reviewed character claims and grounded plot context')
    e.add_argument('--out', type=Path, required=True)
    e.add_argument('--canon-scope', required=True)
    e.add_argument('--cutoff', type=float, default=1)
    e.add_argument('--character-mode', choices=('original', 'canon'), default='original')
    e.add_argument('--character-id', action='append')
    w = commands.add_parser('plan', help='generate and validate a hierarchical new-story plan from an exported context')
    w.add_argument('--out', type=Path, required=True)
    w.add_argument('--context', type=Path, required=True)
    w.add_argument('--brief', type=Path, required=True)
    model_flags(w)
    v = commands.add_parser('audit-plan', help='offline validation of tree coverage, declared chronology and knowledge')
    v.add_argument('--plan', type=Path, required=True)
    v.add_argument('--context', type=Path, required=True)
    v.add_argument('--out', type=Path, required=True)
    d = commands.add_parser('audit-draft', help='LLM review with citations to actual new-draft passages')
    for flag in ('out', 'draft', 'plan', 'context'):
        d.add_argument('--'+flag, type=Path, required=True)
    model_flags(d)
    d = commands.add_parser('demo', help='make a new private synthetic editor fixture, without network access')
    d.add_argument('--out', type=Path, required=True)
    args = p.parse_args(argv)
    try:
        if args.command == 'analyze':
            from story_workbench.pipeline import analyze
            result = analyze(args.corpus, args.out, model_from(args), scope=args.canon_scope,
                chunk_chars=args.chunk_chars, workers=args.llm_workers, retries=args.retries, interval=args.progress_interval,
                works_limit=args.analysis_works, fanout=args.story_fanout, panel_size=args.story_panel_size,
                rounds=args.story_rounds, restart=args.restart)
        elif args.command == 'research':
            from story_workbench.research import research
            result = research(args.out, args.seed, {d.lower().rstrip('.') for d in args.domain}, model_from(args),
                character=args.character, universe=args.universe, scope=args.canon_scope, source_type=args.source_type,
                max_pages=args.max_pages, depth=args.depth, delay=args.delay, workers=args.llm_workers,
                retries=args.retries, interval=args.progress_interval, allow_http=args.allow_http)
        elif args.command == 'serve':
            from story_workbench.server import serve
            serve(args.out, args.port)
            return 0
        elif args.command == 'export':
            from story_workbench.writing import prepare
            result = prepare(args.out, args.canon_scope, args.cutoff, mode=args.character_mode, character_ids=args.character_id)
        elif args.command in {'plan', 'audit-plan', 'audit-draft'}:
            from story_workbench import writing
            context = json.loads(args.context.read_text(encoding='utf-8'))
            if args.command == 'plan':
                result = writing.make_plan(args.out, context, args.brief.read_text(encoding='utf-8'), model_from(args),
                                           retries=args.retries, interval=args.progress_interval)
            else:
                plan = json.loads(args.plan.read_text(encoding='utf-8'))
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
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
