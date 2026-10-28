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
import narrative  # noqa: E402
import run  # noqa: E402


class FakeLocalModel:
    model = 'synthetic-test-model'
    backend = 'ollama'

    def __init__(self, mode='good'):
        self.mode = mode
        self.prompts = []

    def ask(self, prompt):
        self.prompts.append(prompt)
        ids = list(dict.fromkeys(re.findall(r'\[(W\d{4}:S\d{4,})\]', prompt)))
        if prompt.startswith('TASK=VERIFY'):
            if self.mode == 'bad_verifier':
                basis = 'temporal_only'
            else:
                basis = 'causal'
            return {'checks': [{'index': 0, 'verdict': 'supported', 'basis': basis,
                                'evidence_ids': ids[:2]}]}
        if self.mode == 'bad_json':
            raise ValueError('model offline')
        if len(ids) < 2:
            return {'events': [], 'links': [], 'patterns': []}
        one, two = ids[:2]
        if self.mode == 'fake_ids':
            two = 'W9999:S9999'
        return {
            'events': [
                {'id': 'E1', 'kind': 'observation', 'actor': 'focal', 'evidence_ids': [one]},
                {'id': 'E2', 'kind': 'belief', 'actor': 'focal', 'evidence_ids': [two]},
            ],
            'links': [{'from': 'E1', 'to': 'E2',
                       'type': 'before_only' if self.mode == 'only_sequence' else 'contradicts',
                       'evidence_ids': [one, two]}],
            'patterns': [{
                'trigger': 'belief_conflict', 'operation': 'expose_contradiction',
                'effect': 'character_belief_shift', 'event_ids': ['E1', 'E2'],
                'evidence_ids': [one, two], 'certainty': 'explicit'
            }]
        }


class NarrativePipelineTests(unittest.TestCase):
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

    def test_cross_work_supported_and_private(self):
        fake = FakeLocalModel()
        summary = narrative.run_pipeline(self.corpus, self.out, fake, chunk_chars=1800, min_works=2)
        rules = json.loads((self.out / 'narrative_rules.json').read_text(encoding='utf-8'))
        self.assertEqual(rules['schema'], 'pastiche.narrative.v2')
        self.assertEqual(summary['works'], 3)
        self.assertEqual(summary['model_supported_rules_count'], 1)
        rule = rules['rules'][0]
        self.assertEqual(rule['status'], 'model_supported')
        self.assertEqual(rule['support_work_count'], 3)
        self.assertEqual(rule['source'], 'model_two_pass')
        self.assertTrue(fake.prompts[0].startswith('TASK=EXTRACT'))
        self.assertTrue(any(p.startswith('TASK=VERIFY') for p in fake.prompts))
        # Output must contain template prose only, never the fictional source or filenames.
        exported = ''.join(p.read_text(encoding='utf-8') for p in self.out.iterdir() if p.suffix in {'.json', '.md'})
        for sentinel in ('SAMPLE_STORY_TITLE', '古い時計', '主人公はしばらく', 'これまでの推測'):
            self.assertNotIn(sentinel, exported)
        style_cli = SCRIPTS / 'pastiche.py'
        prep = subprocess.run([sys.executable, str(style_cli), 'profile', str(self.corpus), '--out', str(self.out)],
                              capture_output=True, text=True)
        self.assertEqual(prep.returncode, 0, prep.stderr)
        self.assertEqual(json.loads((self.out / 'narrative_rules.json').read_text(encoding='utf-8'))['schema'],
                         'pastiche.narrative.v2', 'reprofiling must not destroy generated rules')
        guide = (self.out / 'style_bible.md').read_text(encoding='utf-8')
        self.assertIn('人物の既存の信念と新しい証拠が衝突する', guide)
        self.assertIn('model-double-checked', guide)

    def test_one_work_rule_is_only_hypothesis(self):
        narrative.run_pipeline(self.corpus, self.out, FakeLocalModel(), max_works=1)
        rules = json.loads((self.out / 'narrative_rules.json').read_text(encoding='utf-8'))['rules']
        self.assertTrue(rules)
        self.assertEqual(rules[0]['status'], 'hypothesis')

    def test_fabricated_ids_or_timing_are_not_promoted(self):
        for mode in ('fake_ids', 'only_sequence', 'bad_verifier'):
            with self.subTest(mode=mode):
                out = self.root / mode
                result = narrative.run_pipeline(self.corpus, out, FakeLocalModel(mode))
                self.assertEqual(result['model_supported_rules_count'], 0)
                self.assertEqual(json.loads((out / 'narrative_rules.json').read_text(encoding='utf-8'))['rules'], [])

    def test_failing_local_model_never_writes_fake_rules(self):
        with self.assertRaisesRegex(ValueError, 'model failed'):
            narrative.run_pipeline(self.corpus, self.out, FakeLocalModel('bad_json'))
        self.assertFalse((self.out / 'narrative_rules.json').exists())

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
                    narrative.validate_endpoint(bad, 'ollama')
        self.assertEqual(narrative.validate_endpoint('https://127.0.0.1:11434/api/generate', 'ollama'),
                         'https://127.0.0.1:11434/api/generate')
        self.assertEqual(narrative.validate_endpoint('http://127.0.0.1:11434/api/generate', 'ollama'),
                         'http://127.0.0.1:11434/api/generate')
        self.assertEqual(narrative.validate_endpoint('http://127.0.0.1:8080/v1/chat/completions', 'openai-compatible'),
                         'http://127.0.0.1:8080/v1/chat/completions')
        self.assertEqual(narrative.validate_endpoint('http://example.test/llm/v1', 'openai-compatible'),
                         'http://example.test/llm/v1/chat/completions')
        self.assertEqual(narrative.validate_endpoint('http://example.test/llm/v1/chat/completions', 'openai-compatible'),
                         'http://example.test/llm/v1/chat/completions')
        self.assertEqual(narrative.select_backend('http://example.test/llm/v1'), 'openai-compatible')

    def test_guide_rejects_tampered_claims(self):
        import pastiche
        base = narrative.build_rules([{
            'trigger': 'belief_conflict', 'operation': 'expose_contradiction',
            'effect': 'character_belief_shift', 'work_id': 'W0001', 'phase': 'opening',
            'evidence_ids': ['W0001:S0001', 'W0001:S0002'], 'basis': 'causal'
        }, {
            'trigger': 'belief_conflict', 'operation': 'expose_contradiction',
            'effect': 'character_belief_shift', 'work_id': 'W0002', 'phase': 'middle',
            'evidence_ids': ['W0002:S0001', 'W0002:S0002'], 'basis': 'causal'
        }], 2, 2, 10)[0]
        self.assertTrue(pastiche.is_guide_eligible(base))
        base['validation_passes'] = 1
        self.assertFalse(pastiche.is_guide_eligible(base))
        self.assertFalse(pastiche.is_guide_eligible({'status': 'hypothesis', 'mechanism': 'FOO'}))


    def test_resume_reuses_successful_chunks(self):
        class FailAtThird(FakeLocalModel):
            def ask(self, prompt):
                if prompt.startswith('TASK=EXTRACT') and 'DATA W0003:' in prompt:
                    raise narrative.ModelRequestError('simulated stall')
                return super().ask(prompt)

        with self.assertRaisesRegex(ValueError, 'W0003 chunk 1 extract'):
            narrative.run_pipeline(self.corpus, self.out, FailAtThird(), progress_interval=0)
        self.assertTrue((self.out / 'narrative_checkpoint.sqlite3').exists())
        progress = json.loads((self.out / 'narrative_progress.json').read_text(encoding='utf-8'))
        self.assertEqual(progress['completed_chunks'], 2)
        self.assertEqual(progress['state'], 'failed')

        fake = FakeLocalModel()
        summary = narrative.run_pipeline(self.corpus, self.out, fake, progress_interval=0)
        self.assertEqual(summary['resumed_chunks'], 2)
        self.assertEqual(len(fake.prompts), 2, 'only third work extraction+verification needed')
        self.assertEqual(summary['model_supported_rules_count'], 1)
        again = FakeLocalModel()
        summary2 = narrative.run_pipeline(self.corpus, self.out, again, progress_interval=0)
        self.assertEqual(summary2['resumed_chunks'], 3)
        self.assertEqual(again.prompts, [])

    def test_resume_after_extract_before_verify(self):
        class FailVerifier(FakeLocalModel):
            def ask(self, prompt):
                if prompt.startswith('TASK=VERIFY'):
                    raise narrative.ModelRequestError('simulated transient verify failure')
                return super().ask(prompt)

        with self.assertRaisesRegex(ValueError, 'verify'):
            narrative.run_pipeline(self.corpus, self.out, FailVerifier(), progress_interval=0)
        con = sqlite3.connect(self.out / 'narrative_checkpoint.sqlite3')
        entry = json.loads(con.execute('SELECT payload FROM chunks WHERE key=?', ('W0001:C00001',)).fetchone()[0])
        con.close()
        self.assertEqual(entry['stage'], 'extracted')
        fake = FakeLocalModel()
        narrative.run_pipeline(self.corpus, self.out, fake, progress_interval=0)
        self.assertTrue(fake.prompts[0].startswith('TASK=VERIFY'), 'extraction should not rerun')

    def test_changed_source_invalidates_matching_chunk_only(self):
        narrative.run_pipeline(self.corpus, self.out, FakeLocalModel(), progress_interval=0)
        (self.corpus / 'SAMPLE_STORY_TITLE_1.txt').write_text(
            (self.corpus / 'SAMPLE_STORY_TITLE_1.txt').read_text(encoding='utf-8') + '新しい情報を確認した。\n',
            encoding='utf-8')
        fake = FakeLocalModel()
        summary = narrative.run_pipeline(self.corpus, self.out, fake, progress_interval=0)
        self.assertEqual(summary['resumed_chunks'], 2)
        self.assertEqual(len(fake.prompts), 2)

    def test_retry_only_transient_error(self):
        class Once(FakeLocalModel):
            def __init__(self):
                super().__init__()
                self.failures = 0
            def ask(self, prompt):
                if self.failures == 0:
                    self.failures += 1
                    raise narrative.ModelRequestError('simulated HTTP 503')
                return super().ask(prompt)
        fake = Once()
        narrative.run_pipeline(self.corpus, self.out, fake, max_works=1,
                               retries=1, retry_delay=0, progress_interval=0)
        self.assertEqual(fake.failures, 1)

    def test_wait_heartbeat_without_stream_tokens(self):
        class Slow:
            def ask(self, prompt):
                time.sleep(0.09)
                return {}
        reporter = narrative.ProgressReporter(self.out, interval=0.02)
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            self.assertEqual(reporter.call(Slow(), 'synthetic prompt', 'W0001 EXTRACT'), {})
        self.assertIn('waiting W0001 EXTRACT', buffer.getvalue())
        status = json.loads((self.out / 'narrative_progress.json').read_text(encoding='utf-8'))
        self.assertGreaterEqual(status['elapsed_seconds'], 0)
        self.assertNotIn('synthetic prompt', buffer.getvalue())

    def test_non_loopback_explicit_url_and_api_key_transport(self):
        local = 'http://127.0.0.1:11434/api/generate'
        lan = 'http://192.168.1.42:11434/api/generate'
        web_http = 'http://8.8.8.8:11434/api/generate'
        web_https = 'https://203.0.113.42/v1/chat/completions'
        self.assertEqual(narrative.validate_endpoint(local, 'ollama'), local)
        for url in (lan, web_https):
            self.assertEqual(narrative.validate_endpoint(url,
                'ollama' if url == lan else 'openai-compatible'), url)
        self.assertEqual(narrative.validate_endpoint(lan, 'ollama', allow_remote=True), lan)
        self.assertEqual(narrative.validate_endpoint(web_https, 'openai-compatible', allow_remote=True), web_https)
        self.assertEqual(narrative.validate_endpoint(web_http, 'ollama'), web_http)
        self.assertEqual(narrative.validate_endpoint(web_http, 'ollama', True, True), web_http)
        with patch.dict('os.environ', {'EXAMPLE_TEST_TOKEN': 'secret'}, clear=False):
            with self.assertRaisesRegex(ValueError, 'HTTPS'):
                narrative.LocalModel('ollama', 'example', lan, allow_remote=True,
                                     api_key_env='EXAMPLE_TEST_TOKEN')
            model = narrative.LocalModel('openai-compatible', 'example', web_https,
                                         allow_remote=True, api_key_env='EXAMPLE_TEST_TOKEN')
            self.assertEqual(model.api_key, 'secret')
        self.assertIsNone(narrative.LocalModel('ollama', 'example', local).timeout)

    def test_model_selection_uses_matching_pi_config_without_network(self):
        config_path = self.root / 'models.json'
        config_path.write_text(json.dumps({'providers': {
            'internal-llm': {'baseUrl': 'http://example.test/llm/v1',
                             'api': 'openai-completions', 'apiKey': 'unused',
                             'models': [{'id': 'model-placeholder'}]},
            'unrelated': {'baseUrl': 'http://other.test/v1',
                          'models': [{'id': 'something-else'}]}}}), encoding='utf-8')
        full = narrative.validate_endpoint('http://example.test/llm/v1', 'openai-compatible')
        self.assertEqual(narrative.resolve_model_name(None, full, 'openai-compatible',
                                                     pi_config_path=config_path), ('model-placeholder', 'pi-config'))
        self.assertEqual(narrative.resolve_model_name('manual-id', full, 'openai-compatible',
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
                m = narrative.LocalModel('ollama', 'fixture', f'http://127.0.0.1:{port}/api/generate', stream=stream)
                chunks = []
                m.on_chunk = chunks.append
                self.assertEqual(m.ask('dummy'), {'events': [], 'links': [], 'patterns': []})
                if stream:
                    self.assertEqual(len(chunks), 3)

    def test_chat_completions_sse(self):
        with self.server() as port:
            for stream in (True, False):
                m = narrative.LocalModel('openai-compatible', 'fixture',
                    f'http://127.0.0.1:{port}/v1/chat/completions', stream=stream)
                self.assertEqual(m.ask('dummy'), {'events': [], 'links': [], 'patterns': []})

    def test_prefixed_api_base_and_auto_discovery(self):
        with self.server() as port:
            base = f'http://127.0.0.1:{port}/llm/v1'
            url = narrative.validate_endpoint(base, 'openai-compatible')
            self.assertEqual(url, base + '/chat/completions')
            # No Pi config; the metadata endpoint advertises exactly one model.
            chosen = narrative.resolve_model_name(None, url, 'openai-compatible',
                                                  pi_config_path=Path('/does-not-exist/models.json'))
            self.assertEqual(chosen, ('model-placeholder', 'server-models'))
            model = narrative.LocalModel('openai-compatible', chosen[0], base)
            self.assertEqual(model.ask('dummy'), {'events': [], 'links': [], 'patterns': []})

    def test_one_command_workflow_and_connection_only_check(self):
        with self.server() as port, tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / 'corpus'
            corpus.mkdir()
            for index in range(2):
                text = ('昔、ある人物は手紙の意味を勘違いした。別の出来事が見方を変えた。\n' * 8)
                (corpus / f'fiction_{index}.txt').write_text(text, encoding='utf-8')
            base = f'http://127.0.0.1:{port}/llm/v1'
            out = root / 'output'
            # A check must not try to read the missing corpus folder.
            self.assertEqual(run.main([str(root / 'missing'), '--api-base', base, '--check']), 0)
            cmd = [str(corpus), '--api-base', base, '--out', str(out), '--progress-interval', '0']
            self.assertEqual(run.main(cmd), 0)
            for name in ('style_profile.json', 'narrative_rules.json', 'style_bible.md',
                         'narrative_fast_checkpoint.sqlite3', 'narrative_progress.json',
                         'lexical_profile.json', 'lexical_candidates.json', 'lexical_bible.md'):
                self.assertTrue((out / name).exists(), name)
            self.assertEqual(run.main(cmd), 0, 'second run should reuse completed chunks')
            rules = json.loads((out / 'narrative_rules.json').read_text(encoding='utf-8'))
            self.assertEqual(rules['model_name'], 'model-placeholder')
            self.assertEqual(json.loads((out / 'narrative_audit.json').read_text(encoding='utf-8'))['restored_extract_chunks'], 2)


if __name__ == '__main__':
    unittest.main()
