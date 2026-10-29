"""Synthetic fixtures only: reasoning defaults, forwarding and cache isolation."""
from __future__ import annotations
import argparse
from contextlib import contextmanager, redirect_stderr
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/pastiche-profile/scripts'
sys.path.insert(0, str(SCRIPTS))
import execution
import lexical_review
import llm_runtime
import llm_style_core as core
import run
from story_workbench import VERSION
from story_workbench.engine import Engine, Task
from story_workbench.store import Store, uid, dump
from story_workbench import style_refine


def model(effort='none', *, backend='openai-compatible', template=True, stream=True, url=None):
    url = url or ('http://127.0.0.1/api/generate' if backend == 'ollama' else 'http://127.0.0.1/llm/v1')
    return llm_runtime.HTTPModel(backend, 'fixture', url, stream=stream,
                                max_output_tokens=4096, reasoning_effort=effort,
                                thinking_template=template)


def arguments(*extra):
    parser = argparse.ArgumentParser()
    execution.add_model_flags(parser)
    return parser.parse_args(list(extra))


class RequestControlTests(unittest.TestCase):
    def test_default_constructor_disables_reasoning(self):
        client = llm_runtime.HTTPModel('openai-compatible', 'fixture', 'http://127.0.0.1/v1')
        body = client._request_body('synthetic JSON task')
        self.assertEqual(body['reasoning_effort'], 'none')
        self.assertEqual(body['chat_template_kwargs'], {'enable_thinking': False})
        self.assertNotIn('reasoning_format', body)

    def test_levels_enable_template_and_are_forwarded_exactly(self):
        for level in ('low', 'medium', 'high'):
            with self.subTest(level=level):
                body = model(level)._request_body('synthetic')
                self.assertEqual(body['reasoning_effort'], level)
                self.assertEqual(body['chat_template_kwargs'], {'enable_thinking': True})

    def test_auto_omits_both_fields(self):
        body = model('auto')._request_body('synthetic')
        self.assertNotIn('reasoning_effort', body)
        self.assertNotIn('chat_template_kwargs', body)

    def test_strict_server_opt_out_retains_effort(self):
        for effort in ('none', 'high'):
            body = model(effort, template=False)._request_body('synthetic')
            self.assertEqual(body['reasoning_effort'], effort)
            self.assertNotIn('chat_template_kwargs', body)

    def test_ollama_has_native_think_field_only(self):
        for effort, expected in [('none', False), ('low', 'low'), ('medium', 'medium'), ('high', 'high')]:
            with self.subTest(effort=effort):
                body = model(effort, backend='ollama')._request_body('synthetic')
                self.assertEqual(body['think'], expected)
                self.assertNotIn('reasoning_effort', body)
                self.assertNotIn('chat_template_kwargs', body)
                self.assertNotIn('think', body['options'])

    def test_ollama_auto_retains_server_default(self):
        self.assertNotIn('think', model('auto', backend='ollama')._request_body('synthetic'))

    def test_bad_values_are_rejected(self):
        for invalid in ('off', 'NONE', '', 'high\n', None, True, 1, {}):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                model(invalid)
        for invalid in ('false', 0, None):
            with self.subTest(template=invalid), self.assertRaises(ValueError):
                model(template=invalid)

    def test_existing_request_parameters_are_unchanged(self):
        for stream in (False, True):
            body = model(stream=stream)._request_body('synthetic')
            self.assertEqual(body['max_tokens'], 4096)
            self.assertEqual(body['temperature'], 0)
            self.assertEqual(body['stream'], stream)
            self.assertEqual(body['response_format'], {'type': 'json_object'})
            self.assertEqual(body['messages'][-1]['content'], 'synthetic')

    def test_worker_copy_preserves_controls_without_mutable_aliasing(self):
        parent = model('medium')
        child = parent.worker_copy()
        self.assertIsNot(parent, child)
        self.assertEqual(parent.reasoning_options(), child.reasoning_options())
        body = child._request_body('synthetic')
        body['chat_template_kwargs']['enable_thinking'] = False
        self.assertTrue(parent._request_body('synthetic')['chat_template_kwargs']['enable_thinking'])
        self.assertTrue(child._request_body('synthetic')['chat_template_kwargs']['enable_thinking'])


class SharedCLITests(unittest.TestCase):
    def test_default_flag_is_none(self):
        self.assertEqual(arguments().reasoning_effort, 'none')

    def test_cli_invalid_effort_fails(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            arguments('--reasoning-effort', 'off')

    def test_model_from_and_forwarding_share_controls(self):
        for effort in llm_runtime.REASONING_EFFORTS:
            with self.subTest(effort=effort):
                args = arguments('--model', 'fixture', '--api-base', 'http://127.0.0.1/v1',
                                 '--reasoning-effort', effort, '--no-thinking-template')
                instance = execution.model_from(args)
                self.assertEqual(instance.reasoning_effort, effort)
                self.assertFalse(instance.thinking_template)
                forwarded = arguments(*execution.forwarding_args(args))
                self.assertEqual(forwarded.reasoning_effort, effort)
                self.assertTrue(forwarded.no_thinking_template)

    def test_old_embedded_namespace_uses_off(self):
        args = arguments('--model', 'fixture', '--api-base', 'http://127.0.0.1/v1')
        del args.reasoning_effort
        del args.no_thinking_template
        instance = execution.model_from(args)
        self.assertEqual(instance.reasoning_effort, 'none')
        forwarded = execution.forwarding_args(args)
        self.assertEqual(forwarded[forwarded.index('--reasoning-effort') + 1], 'none')

    def test_all_profile_llm_children_receive_override(self):
        with tempfile.TemporaryDirectory() as td, patch.object(run.subprocess, 'run',
                return_value=SimpleNamespace(returncode=0)) as child:
            self.assertEqual(run.main(['not-read', '--out', td, '--api-base', 'http://127.0.0.1/v1',
                '--model', 'fixture', '--reasoning-effort', 'high', '--no-thinking-template']), 0)
        commands = [c.args[0] for c in child.call_args_list]
        for script in ('lexical_review.py', 'llm_style.py', 'workbench.py'):
            cmd = next(c for c in commands if any(str(v).endswith(script) for v in c))
            self.assertEqual(cmd[cmd.index('--reasoning-effort')+1], 'high')
            self.assertIn('--no-thinking-template', cmd)

    def test_check_uses_off_and_does_not_read_corpus(self):
        with patch.object(execution, 'model_id', return_value='fixture'), \
             patch.object(llm_runtime.HTTPModel, 'ask', return_value={'ok': True}) as ask:
            self.assertEqual(run.main(['missing-corpus', '--check', '--api-base', 'http://127.0.0.1/v1']), 0)
        self.assertEqual(ask.call_count, 1)
        self.assertNotIn('missing-corpus', ask.call_args.args[0])

    def test_subagent_default_is_not_an_http_control(self):
        with tempfile.TemporaryDirectory() as td:
            args = arguments('--executor', 'subagent', '--task-dir', td)
            args.model = None
            obj = execution.model_from(args)
            self.assertEqual(obj.backend, 'subagent')
            self.assertEqual(llm_runtime.reasoning_cache_parameters(obj), {})
            flags = execution.forwarding_args(args)
            self.assertNotIn('--reasoning-effort', flags)
            self.assertNotIn('--no-thinking-template', flags)

    def test_subagent_rejects_http_overrides_before_handoff(self):
        for overrides in [('--reasoning-effort', 'high'), ('--no-thinking-template',)]:
            args = arguments('--executor', 'subagent', '--task-dir', 'unused', *overrides)
            args.model = None
            with self.assertRaisesRegex(ValueError, 'host subagents'):
                execution.model_from(args)
            with self.assertRaisesRegex(ValueError, 'host subagents'):
                execution.forwarding_args(args)

    def test_help_on_direct_and_write_research_entrypoints(self):
        paths = [
            (SCRIPTS/'run.py', []), (SCRIPTS/'llm_style.py', []),
            (SCRIPTS/'lexical_review.py', []),
            (SCRIPTS/'workbench.py', ['plan']), (SCRIPTS/'workbench.py', ['audit-draft']),
            (SCRIPTS/'workbench.py', ['refine']),
            (ROOT/'skills/pastiche-write/scripts/refine.py', []),
            (ROOT/'skills/character-research/scripts/research.py', []),
            (ROOT/'skills/world-research/scripts/research.py', []),
        ]
        for script, prefix in paths:
            with self.subTest(script=script.name, prefix=prefix):
                result = subprocess.run([sys.executable, str(script), *prefix, '--help'],
                    capture_output=True, text=True, encoding='utf-8', timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('--reasoning-effort', result.stdout)
                self.assertIn('--no-thinking-template', result.stdout)


class CacheIdentityTests(unittest.TestCase):
    def engine_key(self, client):
        e = Engine.__new__(Engine)
        e.model = client
        return e.key(Task('job', 'synthetic', lambda r: r), 'stage')

    def keys(self, client):
        return [core.digest('stage', [], client), lexical_review.batch_fingerprint([], client),
                self.engine_key(client), style_refine._model_key(client)]

    def test_each_effort_has_separate_cache_in_all_paths(self):
        keys = [self.keys(model(e)) for e in llm_runtime.REASONING_EFFORTS]
        for component in zip(*keys):
            self.assertEqual(len(set(component)), len(llm_runtime.REASONING_EFFORTS))

    def test_template_override_separates_cache(self):
        for a, b in zip(self.keys(model()), self.keys(model(template=False))):
            self.assertNotEqual(a, b)

    def test_auto_has_exact_legacy_style_and_lexical_keys(self):
        m = model('auto')
        old_style = {'version': core.VERSION, 'stage': 'stage', 'data': [],
                     'model': m.model, 'backend': m.backend, 'endpoint': m.endpoint}
        old_lex = {'version': lexical_review.VERSION, 'rows': [], 'model': m.model,
                   'backend': m.backend, 'endpoint': m.endpoint}
        digest = lambda obj: hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        self.assertEqual(core.digest('stage', [], m), digest(old_style))
        self.assertEqual(lexical_review.batch_fingerprint([], m), digest(old_lex))

    def test_auto_has_exact_legacy_engine_and_refine_keys(self):
        m = model('auto')
        self.assertEqual(self.engine_key(m), uid('task', VERSION, 'stage', m.backend,
            m.model, m.endpoint, m.max_output_tokens, 'synthetic'))
        old_refine = style_refine.c.digest(dump([m.backend, m.model, m.endpoint, m.max_output_tokens]))
        self.assertEqual(style_refine._model_key(m), old_refine)

    def test_fixture_identity_is_unchanged(self):
        m = SimpleNamespace(model='fixture', backend='fixture')
        self.assertEqual(llm_runtime.reasoning_cache_parameters(m), {})
        old = {'version': core.VERSION, 'stage': 'stage', 'data': [],
               'model': m.model, 'backend': m.backend, 'endpoint': 'fixture'}
        self.assertEqual(core.digest('stage', [], m),
            hashlib.sha256(json.dumps(old, ensure_ascii=False, sort_keys=True).encode()).hexdigest())

    def test_stream_and_worker_count_do_not_change_reasoning_keys(self):
        self.assertEqual(self.keys(model(stream=False)), self.keys(model(stream=True)))
        self.assertEqual(self.keys(model()), self.keys(model().worker_copy()))
        self.assertEqual(self.keys(model('auto')), self.keys(model('auto', template=False)))

    def test_new_mode_does_not_read_or_delete_legacy_checkpoint_entry(self):
        with tempfile.TemporaryDirectory() as td:
            store = llm_runtime.CheckpointStore(Path(td)/'cache.sqlite3')
            try:
                old = core.digest('stage', [], model('auto'))
                new = core.digest('stage', [], model())
                store.put(old, {'value': 'legacy'})
                self.assertIsNone(store.get(new))
                store.put(new, {'value': 'off'})
                self.assertEqual(store.get(old), {'value': 'legacy'})
                self.assertEqual(store.get(new), {'value': 'off'})
            finally:
                store.close()


@contextmanager
def server():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            native = self.path.endswith('/api/generate')
            off = body.get('think') is False if native else (
                body.get('reasoning_effort') == 'none' and
                body.get('chat_template_kwargs', {}).get('enable_thinking') is False)
            finish = 'stop' if off else 'length'
            answer = '{"ok":true}' if off else ''
            self.send_response(200)
            self.send_header('Content-Type', 'application/json' if not body['stream'] else
                             ('application/x-ndjson' if native else 'text/event-stream'))
            self.end_headers()
            if native:
                event = {'response': answer, 'done': True, 'done_reason': finish}
                payload = json.dumps(event).encode() + (b'\n' if body['stream'] else b'')
            elif body['stream']:
                payload = b'data: ' + json.dumps({'choices': [{'delta': {'content': answer},
                    'finish_reason': finish}]}).encode() + b'\n\ndata: [DONE]\n\n'
            else:
                payload = json.dumps({'choices': [{'message': {'content': answer},
                    'finish_reason': finish}]}).encode()
            self.wfile.write(payload)
    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{http.server_port}', requests
    finally:
        http.shutdown(); http.server_close(); thread.join(3)


class HTTPWireTests(unittest.TestCase):
    def test_default_off_is_sent_in_json_and_stream_requests(self):
        with server() as (base, requests):
            for backend in ('openai-compatible', 'ollama'):
                for stream in (False, True):
                    with self.subTest(backend=backend, stream=stream):
                        endpoint = base + ('/api/generate' if backend == 'ollama' else '/llm/v1')
                        self.assertEqual(model(backend=backend, stream=stream, url=endpoint).ask('synthetic'), {'ok': True})
            self.assertEqual(len(requests), 4)

    def test_actual_four_workers_inherit_off(self):
        with server() as (base, requests):
            client = model(url=base+'/llm/v1')
            rows = list(llm_runtime.model_jobs(range(4), client, 4, lambda job, m: m.ask('synthetic')))
            self.assertEqual(len(rows), 4)
            self.assertTrue(all(answer == {'ok': True} for _, answer in rows))
            self.assertEqual(len(requests), 4)
            self.assertTrue(all(r['reasoning_effort'] == 'none' for r in requests))

    def test_length_remains_failure_not_a_silent_fallback(self):
        with server() as (base, requests):
            for stream in (False, True):
                with self.assertRaises(llm_runtime.ModelRequestError) as raised:
                    model('auto', stream=stream, url=base+'/llm/v1').ask('synthetic')
                self.assertFalse(raised.exception.transient)
            self.assertEqual(len(requests), 2)
            self.assertTrue(all('reasoning_effort' not in r for r in requests))

    def test_run_check_sends_default_controls_through_real_http(self):
        with server() as (base, requests):
            self.assertEqual(run.main(['not-read', '--api-base', base+'/llm/v1',
                                     '--model', 'fixture', '--check']), 0)
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0]['reasoning_effort'], 'none')


if __name__ == '__main__':
    unittest.main()
