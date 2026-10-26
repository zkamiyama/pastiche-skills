"""Mock-only tests. No live LLM, internet, external document collections."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'pastiche-profile' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import narrative  # noqa: E402


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
        self.assertEqual(rule['source'], 'local_llm_two_pass')
        self.assertTrue(fake.prompts[0].startswith('TASK=EXTRACT'))
        self.assertTrue(any(p.startswith('TASK=VERIFY') for p in fake.prompts))
        # Output must contain template prose only, never the fictional source or filenames.
        exported = ''.join(p.read_text(encoding='utf-8') for p in self.out.iterdir())
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

    def test_endpoint_rejects_external_or_redirect_prone_hosts(self):
        for bad in (
            'https://127.0.0.1:11434/api/generate',
            'http://example.com:11434/api/generate',
            'http://10.0.0.1:11434/api/generate',
            'http://127.0.0.1:11434/api/generate?q=leak',
            'http://user@localhost:11434/api/generate',
            'http://localhost:11434/evil',
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    narrative.validate_endpoint(bad, 'ollama')
        self.assertEqual(narrative.validate_endpoint('http://127.0.0.1:11434/api/generate', 'ollama'),
                         'http://127.0.0.1:11434/api/generate')
        self.assertEqual(narrative.validate_endpoint('http://127.0.0.1:8080/v1/chat/completions', 'openai-compatible'),
                         'http://127.0.0.1:8080/v1/chat/completions')

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


if __name__ == '__main__':
    unittest.main()
