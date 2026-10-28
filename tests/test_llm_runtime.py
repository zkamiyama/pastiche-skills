"""Synthetic-data tests: mock model responses and loopback protocol fixtures."""
from __future__ import annotations

import io
import json
import time
from contextlib import contextmanager, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from unittest.mock import patch
import sqlite3
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'pastiche-profile' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import llm_runtime  # noqa: E402
import run  # noqa: E402


class RuntimeSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.corpus = self.root / 'corpus'
        self.corpus.mkdir()
        for n in range(3):
            # Vary invented names and genres; shared macro-level narrative device.
            story = (
                f'彼女は古い時計を信じていた。けれども針は突然止まった。{n}\n'
                '「まちがいない」と告げた。\n'
                '新たな証拠が現れ、これまでの推測と矛盾した。\n'
                '主人公はしばらく否定し、その後で判断を変えた。\n'
                '別の選択をしてみると、これまでと違う結果が出た。\n'
            ) * 3
            (self.corpus / f'SAMPLE_STORY_TITLE_{n}.txt').write_text(story, encoding='utf-8')
        self.out = self.root / 'profile'
    def tearDown(self):
        self.temp.cleanup()
    def test_endpoint_rejects_unsafe_url_parts(self):
        for bad in (
            'http://127.0.0.1:11434/api/generate?q=leak',
            'http://user@localhost:11434/api/generate',
            'http://localhost:11434/evil',
            'http://localhost:11434/../api/generate',
            'file:///v1/chat/completions',
            'http://127.0.0.1:11434/api/generate#fragment',
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    llm_runtime.validate_endpoint(bad, 'ollama')
        self.assertEqual(llm_runtime.validate_endpoint('https://127.0.0.1:11434/api/generate', 'ollama'),
                         'https://127.0.0.1:11434/api/generate')
        self.assertEqual(llm_runtime.validate_endpoint('http://127.0.0.1:11434/api/generate', 'ollama'),
                         'http://127.0.0.1:11434/api/generate')
        self.assertEqual(llm_runtime.validate_endpoint('http://127.0.0.1:8080/v1/chat/completions', 'openai-compatible'),
                         'http://127.0.0.1:8080/v1/chat/completions')
        self.assertEqual(llm_runtime.validate_endpoint('http://example.test/llm/v1', 'openai-compatible'),
                         'http://example.test/llm/v1/chat/completions')
        self.assertEqual(llm_runtime.validate_endpoint('http://example.test/llm/v1/chat/completions', 'openai-compatible'),
                         'http://example.test/llm/v1/chat/completions')
        self.assertEqual(llm_runtime.select_backend('http://example.test/llm/v1'), 'openai-compatible')
    def test_wait_heartbeat_without_stream_tokens(self):
        class Slow:
            def ask(self, prompt):
                time.sleep(0.09)
                return {}
        reporter = llm_runtime.ProgressReporter(self.out, interval=0.02)
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            self.assertEqual(reporter.call(Slow(), 'synthetic prompt', 'W0001 EXTRACT'), {})
        self.assertIn('waiting W0001 EXTRACT', buffer.getvalue())
        status = json.loads((self.out / 'llm_progress.json').read_text(encoding='utf-8'))
        self.assertGreaterEqual(status['elapsed_seconds'], 0)
        self.assertNotIn('synthetic prompt', buffer.getvalue())
    def test_non_loopback_explicit_url_and_api_key_transport(self):
        local = 'http://127.0.0.1:11434/api/generate'
        lan = 'http://192.168.1.42:11434/api/generate'
        web_http = 'http://8.8.8.8:11434/api/generate'
        web_https = 'https://203.0.113.42/v1/chat/completions'
        self.assertEqual(llm_runtime.validate_endpoint(local, 'ollama'), local)
        for url in (lan, web_https):
            self.assertEqual(llm_runtime.validate_endpoint(url,
                'ollama' if url == lan else 'openai-compatible'), url)
        self.assertEqual(llm_runtime.validate_endpoint(lan, 'ollama'), lan)
        self.assertEqual(llm_runtime.validate_endpoint(web_https, 'openai-compatible'), web_https)
        self.assertEqual(llm_runtime.validate_endpoint(web_http, 'ollama'), web_http)
        with patch.dict('os.environ', {'EXAMPLE_TEST_TOKEN': 'secret'}, clear=False):
            with self.assertRaisesRegex(ValueError, 'HTTPS'):
                llm_runtime.HTTPModel('ollama', 'example', lan,
                                     api_key_env='EXAMPLE_TEST_TOKEN')
            model = llm_runtime.HTTPModel('openai-compatible', 'example', web_https,
                                         api_key_env='EXAMPLE_TEST_TOKEN')
            self.assertEqual(model.api_key, 'secret')
        self.assertIsNone(llm_runtime.HTTPModel('ollama', 'example', local).timeout)
    def test_model_selection_uses_matching_pi_config_without_network(self):
        config_path = self.root / 'models.json'
        config_path.write_text(json.dumps({'providers': {
            'internal-llm': {'baseUrl': 'http://example.test/llm/v1',
                             'api': 'openai-completions', 'apiKey': 'unused',
                             'models': [{'id': 'model-placeholder'}]},
            'unrelated': {'baseUrl': 'http://other.test/v1',
                          'models': [{'id': 'something-else'}]}}}), encoding='utf-8')
        full = llm_runtime.validate_endpoint('http://example.test/llm/v1', 'openai-compatible')
        self.assertEqual(llm_runtime.resolve_model_name(None, full, 'openai-compatible',
                                                     pi_config_path=config_path), ('model-placeholder', 'pi-config'))
        self.assertEqual(llm_runtime.resolve_model_name('manual-id', full, 'openai-compatible',
                                                     pi_config_path=config_path), ('manual-id', 'explicit'))


class ProtocolTests(unittest.TestCase):
    @contextmanager
    def server(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return
            def do_GET(self):
                if self.path != '/llm/v1/models':
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'data': [{'id': 'model-placeholder'}]}).encode('utf-8'))
            def do_POST(self):
                if self.path not in ('/api/generate', '/v1/chat/completions',
                                     '/llm/v1/chat/completions'):
                    self.send_error(404)
                    return
                req = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                value = '{"events":[],"links":[],"patterns":[]}'
                head = req.get('stream', False)
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-ndjson' if self.path == '/api/generate' else 'text/event-stream')
                self.end_headers()
                if not head:
                    wrapped = ({'response': value} if self.path == '/api/generate' else
                               {'choices': [{'message': {'content': value}}]})
                    self.wfile.write(json.dumps(wrapped).encode('utf-8'))
                    return
                if self.path == '/api/generate':
                    for part, done in ((value[:15], False), (value[15:], False), ('', True)):
                        self.wfile.write(json.dumps({'response': part, 'done': done}).encode('utf-8') + b'\n')
                else:
                    for part in (value[:15], value[15:]):
                        e = {'choices': [{'delta': {'content': part}}]}
                        self.wfile.write(b'data: ' + json.dumps(e).encode('utf-8') + b'\n\n')
                    self.wfile.write(b'data: [DONE]\n\n')
                self.wfile.flush()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address[1]
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_ollama_stream_json(self):
        with self.server() as port:
            for stream in (True, False):
                m = llm_runtime.HTTPModel('ollama', 'fixture', f'http://127.0.0.1:{port}/api/generate', stream=stream)
                chunks = []
                m.on_chunk = chunks.append
                self.assertEqual(m.ask('dummy'), {'events': [], 'links': [], 'patterns': []})
                if stream:
                    self.assertEqual(len(chunks), 3)

    def test_chat_completions_sse(self):
        with self.server() as port:
            for stream in (True, False):
                m = llm_runtime.HTTPModel('openai-compatible', 'fixture',
                    f'http://127.0.0.1:{port}/v1/chat/completions', stream=stream)
                self.assertEqual(m.ask('dummy'), {'events': [], 'links': [], 'patterns': []})

    def test_prefixed_api_base_and_auto_discovery(self):
        with self.server() as port:
            base = f'http://127.0.0.1:{port}/llm/v1'
            url = llm_runtime.validate_endpoint(base, 'openai-compatible')
            self.assertEqual(url, base + '/chat/completions')
            # No Pi config; the metadata endpoint advertises exactly one model.
            chosen = llm_runtime.resolve_model_name(None, url, 'openai-compatible',
                                                  pi_config_path=Path('/does-not-exist/models.json'))
            self.assertEqual(chosen, ('model-placeholder', 'server-models'))
            model = llm_runtime.HTTPModel('openai-compatible', chosen[0], base)
            self.assertEqual(model.ask('dummy'), {'events': [], 'links': [], 'patterns': []})
