"""One model/executor configuration shared by every LLM entry point."""
from __future__ import annotations
import os
from pathlib import Path
import llm_runtime
from task_transport import SubagentModel

def resolve_url(raw: str | None, backend: str):
    raw = raw or 'http://127.0.0.1:11434/api/generate'
    backend = llm_runtime.select_backend(raw, backend)
    endpoint = llm_runtime.validate_endpoint(raw, backend)
    base = llm_runtime.inference_base_url(endpoint, backend)
    metadata = base + ('/api/tags' if backend == 'ollama' else '/models')
    return backend, endpoint, base, metadata


def model_id(manual: str | None, base: str, backend: str, models_url: str, api_key: str | None = None) -> str:
    endpoint = base + ('/api/generate' if backend == 'ollama' else '/chat/completions')
    return llm_runtime.resolve_model_name(manual, endpoint, backend, api_key=api_key)[0]


def add_transport_flags(p):
    p.add_argument('--executor', choices=('http', 'subagent'), default='http')
    p.add_argument('--task-dir', type=Path)
    p.add_argument('--agent-profile', default='default', help='non-secret dispatch/cache label for host subagents')


def add_model_flags(p):
    add_transport_flags(p)
    p.add_argument('--api-base')
    p.add_argument('--backend', choices=('auto', 'ollama', 'openai-compatible'), default='auto')
    p.add_argument('--model', default=os.environ.get('PASTICHE_MODEL'))
    p.add_argument('--api-key-env')
    p.add_argument('--timeout', type=int, default=0)
    p.add_argument('--retries', type=int, default=2)
    p.add_argument('--llm-workers', type=int, default=1)
    p.add_argument('--progress-interval', type=float, default=15)
    p.add_argument('--max-output-tokens', type=int, default=0)
    p.add_argument('--no-stream', action='store_true')


def model_from(args):
    if args.executor == 'subagent':
        if args.api_base or args.model or args.api_key_env:
            raise ValueError('subagent execution does not use --api-base/--model/--api-key-env')
        if not args.task_dir:
            raise ValueError('--executor subagent requires a private --task-dir')
        return SubagentModel(args.task_dir, args.agent_profile)
    backend, endpoint, base, metadata = resolve_url(args.api_base, args.backend)
    name = model_id(args.model, base, backend, metadata, os.environ.get(args.api_key_env) if args.api_key_env else None)
    return llm_runtime.HTTPModel(backend, name, endpoint, args.timeout,
        api_key_env=args.api_key_env,
        stream=not args.no_stream, max_output_tokens=args.max_output_tokens)


def forwarding_args(args):
    flags = ['--executor', args.executor, '--timeout', str(args.timeout),
             '--retries', str(args.retries), '--llm-workers', str(args.llm_workers),
             '--progress-interval', str(args.progress_interval), '--max-output-tokens', str(args.max_output_tokens)]
    if args.executor == 'subagent':
        if not args.task_dir:
            raise ValueError('subagent execution requires --task-dir')
        flags += ['--task-dir', str(args.task_dir), '--agent-profile', args.agent_profile]
    else:
        flags += ['--backend', args.backend]
        if args.api_base: flags += ['--api-base', args.api_base]
        if args.model: flags += ['--model', args.model]
        if args.api_key_env: flags += ['--api-key-env', args.api_key_env]
        if args.no_stream: flags += ['--no-stream']
    return flags
