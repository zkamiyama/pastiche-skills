"""Only synthetic, neutral Japanese fiction is used in these tests."""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CLI = Path(__file__).resolve().parents[1] / 'skills' / 'pastiche-profile' / 'scripts' / 'pastiche.py'
spec = importlib.util.spec_from_file_location('pastiche_offline', CLI)
lib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lib)

class OfflinePasticheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.corpus = self.root / 'corpus'
        self.corpus.mkdir()
        for n in range(6):
            work = (
                'あの日、わたしは駅に着いた。ところが、列車は止まっていた。\n'
                '「遅れたの？」と友人は聞いた。\n\n'
                'しかし、駅長はまだ何も言わなかった。\n'
                '翌朝、ふたりは手紙を読んだ。どうやら、道が変わったらしい。\n'
            ) * (n + 3)
            (self.corpus / f'SAMPLE_WORK_{n}.txt').write_text(work, encoding='utf-8')
        self.out = self.root / 'out'

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args):
        return subprocess.run([sys.executable, str(CLI), *map(str, args)], capture_output=True, text=True, cwd=self.root)

    def make_profile(self):
        run = self.cli('profile', self.corpus, '--out', self.out)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads((self.out / 'style_profile.json').read_text(encoding='utf-8'))

    def test_profile_no_source_names_or_raw_passages(self):
        profile = self.make_profile()
        self.assertEqual(profile['schema'], lib.SCHEMA)
        self.assertEqual(profile['works'], 6)
        self.assertEqual(profile['confidence'], 'moderate_descriptive_only')
        exported = '\n'.join(f.read_text(encoding='utf-8') for f in self.out.glob('*'))
        self.assertNotIn('SAMPLE_WORK_', exported)
        self.assertNotIn('駅長はまだ何も言わなかった', exported)
        self.assertGreater(profile['metrics']['dialogue_char_ratio']['median'], 0)
        self.assertIn('opening', profile['phase_medians'])

    def test_hypothesis_excluded_and_verified_included(self):
        self.make_profile()
        notes = {'schema': 'pastiche.narrative.v1', 'rules': [
            {'status': 'hypothesis', 'mechanism': 'DO_NOT_INCLUDE'},
            {'status': 'verified', 'mechanism': '行動の結果から前提を再考する', 'trigger': '前提に矛盾', 'effect': '選択が変わる'}]}
        f = self.root / 'notes.json'
        f.write_text(json.dumps(notes, ensure_ascii=False), encoding='utf-8')
        out = self.root / 'generated_guide.md'
        r = self.cli('guide', '--profile', self.out / 'style_profile.json', '--notes', f, '--out', out)
        self.assertEqual(r.returncode, 0, r.stderr)
        guide = out.read_text(encoding='utf-8')
        self.assertIn('行動の結果から前提を再考する', guide)
        self.assertNotIn('DO_NOT_INCLUDE', guide)

    def test_evaluation_and_overlap_only_counts(self):
        self.make_profile()
        original = (self.corpus / 'SAMPLE_WORK_0.txt').read_text(encoding='utf-8')
        draft = self.root / 'draft.txt'
        draft.write_text('独自の冒頭。\n' + original[:160], encoding='utf-8')
        result = self.root / 'eval.json'
        r = self.cli('evaluate', '--profile', self.out / 'style_profile.json', '--draft', draft,
                     '--corpus', self.corpus, '--overlap-chars', '40', '--out', result)
        self.assertEqual(r.returncode, 0, r.stderr)
        output = result.read_text(encoding='utf-8')
        self.assertNotIn('駅長はまだ', output)
        self.assertGreater(json.loads(output)['overlap']['matching_windows'], 0)
        self.assertIn('style_comparisons', json.loads(output))

    def test_empty_and_symlink_fail_safely(self):
        empty = self.root / 'empty'
        empty.mkdir()
        run = self.cli('profile', empty, '--out', self.out)
        self.assertNotEqual(run.returncode, 0)
        if hasattr(Path, 'symlink_to'):
            symlink = self.root / 'link.txt'
            symlink.symlink_to(self.corpus / 'SAMPLE_WORK_0.txt')
            self.assertRaises(ValueError, lib.read_text, symlink)

    def test_cp932_and_low_confidence(self):
        one = self.root / 'one.txt'
        one.write_bytes(('東京の夜に、電車は静かに到着した。' * 10).encode('cp932'))
        profile = lib.aggregate([lib.measure(lib.read_text(one))], [lib.phases(lib.read_text(one))], {}, 'none')
        self.assertEqual(profile['confidence'], 'low')
        self.assertTrue(profile['metrics']['characters']['median'] >= 80)

    def test_short_draft_no_originality_proof(self):
        profile = self.make_profile()
        evaluation = lib.evaluate(profile, '今日は晴れだ。')
        self.assertTrue(evaluation['draft_too_short_for_stable_comparison'])
        self.assertIn('not a quality', evaluation['note'])

if __name__ == '__main__':
    unittest.main()
