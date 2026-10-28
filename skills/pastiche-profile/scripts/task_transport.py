"""Provider-neutral, resumable task handoff to a host agent.

The application never pretends to spawn subagents. A host with subagent tools
reads a private request, delegates its prompt, then imports a JSON answer using
`workbench tasks submit`. Execution resumes with the same validators as HTTP.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable

SCHEMA = 'pastiche.agent-task.v1'
MAX_BYTES = 8_000_000


class TasksPending(Exception):
    """A resumable suspension, never an inference failure or a negative finding."""
    exit_code = 75
    def __init__(self, task_dir: Path):
        self.task_dir = Path(task_dir)
        super().__init__('Subagent tasks are pending; complete private requests and rerun the same command.')


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _read(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError('missing, linked or oversized task file')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('task/response must be a JSON object')
    return value


def _atomic(path: Path, value: dict) -> None:
    if path.is_symlink():
        raise ValueError('symlink task file is not allowed')
    content = _json(value).encode('utf-8')
    if len(content) > MAX_BYTES:
        raise ValueError('task exceeds size limit')
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.task-')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class SubagentModel:
    backend = 'subagent'
    endpoint = 'private-file-handoff'
    max_output_tokens = 0

    def __init__(self, task_dir: Path, profile: str = 'default'):
        self.task_dir = Path(task_dir).absolute()
        if self.task_dir.is_symlink():
            raise ValueError('task directory must not be a symlink')
        self.task_dir = self.task_dir.resolve()
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', profile):
            raise ValueError('agent profile must be a short non-secret identifier')
        self.model = profile
        self.task_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        for sub in ('requests', 'responses'):
            p = self.task_dir/sub
            if p.is_symlink():
                raise ValueError('linked task directory')
            p.mkdir(parents=True, exist_ok=True, mode=0o700)

    def worker_copy(self):
        return self  # Stateless per request; atomic files, no callbacks.

    def ask(self, prompt: str) -> dict:
        task_id = hashlib.sha256(_json([SCHEMA, self.model, prompt]).encode('utf-8')).hexdigest()
        request = {'schema': SCHEMA, 'task_id': task_id, 'profile': self.model,
                   'input_sha256': hashlib.sha256(prompt.encode('utf-8')).hexdigest(),
                   'prompt': prompt, 'output_contract': 'JSON object; application validates domain schema on resume',
                   'privacy': 'Contains private source data. No web search, external tools or onward sharing.'}
        req_path = self.task_dir/'requests'/f'{task_id}.json'
        if req_path.exists():
            if _read(req_path) != request:
                raise ValueError('task request differs from the expected input')
        else:
            _atomic(req_path, request)
        output = self.task_dir/'responses'/f'{task_id}.json'
        if not output.exists():
            raise TasksPending(self.task_dir)
        response = _read(output)
        if (response.get('schema') != SCHEMA or response.get('task_id') != task_id
                or response.get('input_sha256') != request['input_sha256']
                or not isinstance(response.get('result'), dict)):
            raise ValueError('subagent result does not match task input/JSON contract')
        return response['result']


class CallableModel:
    """In-process integration for a host that can dispatch its own subagents.

callback receives a prompt and returns a JSON dict; all content validation still
belongs to the task runner. Thread safety is the host callback's responsibility.
"""
    backend = 'callback'
    endpoint = 'host-subagent'
    max_output_tokens = 0
    def __init__(self, callback: Callable[[str], dict], identity: str):
        self.callback, self.model = callback, identity
    def ask(self, prompt: str) -> dict:
        result = self.callback(prompt)
        if not isinstance(result, dict):
            raise ValueError('host callback must return a JSON object')
        return result


def task_id_arg(value: str) -> str:
    if not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('invalid task ID')
    return value


def list_tasks(directory: Path) -> list[dict]:
    model = SubagentModel(directory)
    rows = []
    for path in sorted((model.task_dir/'requests').glob('*.json')):
        task_id_arg(path.stem)
        r = _read(path)
        if r.get('task_id') != path.stem:
            raise ValueError('invalid task filename')
        rows.append({'task_id': path.stem, 'profile': r['profile'],
                     'state': 'response_available' if (model.task_dir/'responses'/path.name).exists() else 'pending',
                     'input_sha256': r['input_sha256'], 'prompt_characters': len(r['prompt'])})
    return rows


def submit(directory: Path, task_id: str, result: dict, *, replace: bool = False) -> None:
    model = SubagentModel(directory)
    task_id = task_id_arg(task_id)
    request = _read(model.task_dir/'requests'/f'{task_id}.json')
    expected = hashlib.sha256(_json([SCHEMA, request['profile'], request['prompt']]).encode('utf-8')).hexdigest()
    if expected != task_id or request['input_sha256'] != hashlib.sha256(request['prompt'].encode('utf-8')).hexdigest():
        raise ValueError('modified request cannot be answered')
    if not isinstance(result, dict):
        raise ValueError('result must be an object')
    target = model.task_dir/'responses'/f'{task_id}.json'
    if target.exists() and not replace:
        raise ValueError('response exists; explicit --replace required for repair')
    _atomic(target, {'schema': SCHEMA, 'task_id': task_id,
                     'input_sha256': request['input_sha256'], 'result': result})


def add_task_commands(parser):
    parser.add_argument('operation', choices=['list', 'show', 'submit'])
    parser.add_argument('--task-dir', type=Path, required=True)
    parser.add_argument('--task-id')
    parser.add_argument('--response-file', type=Path)
    parser.add_argument('--replace', action='store_true')


def task_command(args):
    if args.operation == 'list':
        return {'tasks': list_tasks(args.task_dir)}
    task_id = task_id_arg(args.task_id or '')
    model = SubagentModel(args.task_dir)
    if args.operation == 'show':
        return _read(model.task_dir/'requests'/f'{task_id}.json')
    if not args.response_file:
        raise ValueError('--response-file is required')
    submit(args.task_dir, task_id, _read(args.response_file), replace=args.replace)
    return {'task_id': task_id, 'state': 'response_available',
            'note': 'Domain validation occurs on pipeline resume, not at file submission.'}
