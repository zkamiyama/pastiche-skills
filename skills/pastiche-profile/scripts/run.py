#!/usr/bin/env python3
"""Current profiling pipeline; no legacy extraction modes."""
from __future__ import annotations
import argparse
from pathlib import Path
import subprocess
import sys
import execution
from task_transport import TasksPending
HERE = Path(__file__).resolve().parent

def main(argv=None):
    parser = argparse.ArgumentParser(description="作風プロファイルを作成: 統計・表記・全文構造・人物視点")
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--out", type=Path, default=Path(".pastiche-private/profile"))
    parser.add_argument("--mode", choices=("llm","none"), default="llm",
                        help="llm=grounded analysis (default); none=local measurements")
    parser.add_argument("--chunk-chars", type=int, default=3800)
    parser.add_argument("--analysis-works", type=int, default=0,
                        help="llm mode: all eligible works by default (0); positive N limits selected works")
    parser.add_argument("--cluster-batch-size", type=int, default=30,
                        help="LLM grouping panel size (default 30)")
    parser.add_argument("--cluster-rounds", type=int, default=3,
                        help="maximum LLM semantic-grouping rounds (default 3)")
    parser.add_argument("--verify-works", type=int, default=4,
                        help="max independent works rechecked per group (default 4)")
    parser.add_argument("--chunks-per-work", type=int, default=2,help="sampled passages per work (default 2)")
    parser.add_argument("--verify-groups", type=int, default=16)
    parser.add_argument("--min-works", type=int, default=2)
    parser.add_argument("--variants",type=Path,help="exact variant pairs: JSON array of [A,B]")
    parser.add_argument("--morphology",choices=("auto","none","fugashi"),default="auto")
    parser.add_argument("--no-auto-install", action="store_true",
                        help="skip first-run Japanese dictionary setup; full vocabulary may be unavailable")
    parser.add_argument("--reference",type=Path,help="genre-matched reference TXT corpus for descriptive lexical contrast")
    parser.add_argument("--include-words-in-guide",action="store_true",help="also include frequent short lexical forms in private guide")
    parser.add_argument("--lexical-review-limit",type=int,default=96,
                        help="model-judged typicality candidates (default 96, 0=all qualifying; can be very slow)")
    parser.add_argument("--lexical-batch-size",type=int,default=24)
    parser.add_argument("--no-lexical-review",action="store_true",
                        help="skip model language-impression review; exact statistics still run")
    parser.add_argument("--skip-story", action="store_true", help="LLM mode: skip full-text story/character analysis (style sampling only)")
    parser.add_argument("--canon-scope", default="corpus:original", help="explicit universe/edition namespace for character facts")
    parser.add_argument("--story-fanout", type=int, default=24)
    parser.add_argument("--story-panel-size", type=int, default=12)
    parser.add_argument("--story-rounds", type=int, default=3)
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--examples-per-pattern",type=int,default=2,help="new neutral illustrations per supported pattern; 0 disables")
    parser.add_argument("--check", action="store_true", help="synthetic API check only, without reading corpus")
    execution.add_model_flags(parser)
    args = parser.parse_args(argv)
    try:
        if args.llm_workers < 1 or args.retries < 0 or not 0 <= args.examples_per_pattern <= 4:
            raise ValueError('invalid concurrency/retry limits')
        if args.mode == 'llm' and not args.skip_story and (not 4 <= args.story_fanout <= 32
                or args.story_panel_size < 2 or args.story_rounds < 1):
            raise ValueError('invalid story analysis limits')
        if args.lexical_review_limit < 0 or not 1 <= args.lexical_batch_size <= 48:
            raise ValueError('invalid lexical review limits')
        if args.check:
            execution.model_from(args).ask('Return JSON only: {"ok":true}. Synthetic connection check.')
            print('Connection OK; deployment identifiers withheld; corpus not read.')
            return 0
        if args.executor == 'subagent' and (args.api_base or args.model or args.api_key_env):
            raise ValueError('subagent execution takes no endpoint, model ID or API key')
        flags = execution.forwarding_args(args) if args.mode == 'llm' else []
        if args.restart:
            flags.append('--restart')
        def invoke(script, extra):
            return subprocess.run([sys.executable, '-u', str(HERE/script), *map(str, extra)]).returncode
        def stop(code):
            if code == TasksPending.exit_code:
                print('Waiting for host subagent results. Rerun this command after task submission.', file=sys.stderr)
            return code
        # The only public modes are the current pipeline and offline measurements.
        code = invoke('pastiche.py', ['profile', args.corpus, '--out', args.out])
        if code: return code
        lex = [args.corpus, '--out', args.out, '--morphology', args.morphology, '--candidate-limit', args.lexical_review_limit]
        if args.variants: lex += ['--variants', args.variants]
        if args.reference: lex += ['--reference', args.reference]
        if args.include_words_in_guide: lex += ['--include-words-in-guide']
        code = invoke('lexical.py', lex)
        if code: return code
        review_ok = False
        if args.mode == 'llm':
            if not args.no_lexical_review:
                code = invoke('lexical_review.py', ['--candidates', args.out/'lexical_candidates.json', '--corpus', args.corpus, '--out', args.out, '--batch-size', args.lexical_batch_size, *flags])
                if code == TasksPending.exit_code: return stop(code)
                review_ok = code == 0
                if code: print('Lexical model impressions unavailable; deterministic measurements remain.', file=sys.stderr)
            code = invoke('llm_style.py', [args.corpus, '--out', args.out, '--chunk-chars', args.chunk_chars,
                '--min-works', args.min_works, '--analysis-works', args.analysis_works,
                '--chunks-per-work', args.chunks_per_work, '--cluster-batch-size', args.cluster_batch_size,
                '--cluster-rounds', args.cluster_rounds, '--verify-groups', args.verify_groups,
                '--verify-works', args.verify_works, *flags])
            if code: return stop(code)
            if not args.skip_story:
                code = invoke('workbench.py', ['analyze', args.corpus, '--out', args.out,
                    '--canon-scope', args.canon_scope, '--chunk-chars', args.chunk_chars,
                    '--analysis-works', args.analysis_works, '--story-fanout', args.story_fanout,
                    '--story-panel-size', args.story_panel_size, '--story-rounds', args.story_rounds, *flags])
                if code: return stop(code)
        if args.mode == 'llm':
            code = invoke('guide_examples.py', ['--profile', args.out, '--corpus', args.corpus,
                '--examples-per-pattern', args.examples_per_pattern, *execution.forwarding_args(args)])
            if code: return stop(code)
        guide = ['guide', '--profile', args.out/'style_profile.json', '--lexical', args.out/'lexical_bible.md',
                 '--out', args.out/'style_bible.md']
        if args.mode == 'llm':
            guide += ['--notes', args.out/'narrative_rules.json', '--qualitative', args.out/'qualitative_bible.md']
            if not args.skip_story: guide += ['--narrative-structure', args.out/'narrative_bible.md']
        if review_ok: guide += ['--lexical-review', args.out/'lexical_interpretation.md']
        return invoke('pastiche.py', guide)
    except TasksPending:
        return TasksPending.exit_code
    except (OSError, ValueError, TypeError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2

if __name__ == "__main__":
    # Skills CLI only copies files. First real use can initialize an isolated
    # Python environment; avoid modifying system and agent Python packages.
    from bootstrap import bootstrap_entrypoint
    try:
        bootstrap_entrypoint(Path(__file__).resolve(), sys.argv[1:])
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"error: first-run dependency setup failed: {exc}. "
              "Use --no-auto-install --morphology none for a reduced analysis.",
              file=sys.stderr, flush=True)
        raise SystemExit(2)
    raise SystemExit(main())
