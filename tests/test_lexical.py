import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
SCRIPTS = HERE / 'skills/pastiche-profile/scripts'
sys.path.insert(0, str(SCRIPTS))
import lexical

class SimpleTagger:
    class Word:
        def __init__(self, s):
            self.surface=s
            self.feature=type('Feature',(),{'pos1':'名詞','pos2':'一般'})()
    def __call__(self, text):
        import re
        return [self.Word(x) for x in re.findall(r'[一-龥ぁ-ゖァ-ヺ]+',text)]

class LexicalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.corpus=self.root/'corpus';self.corpus.mkdir()
        self.story=('「じゃねえか♡♡♡」\n「来たッ！♪♪」\n'
                    '「じゃあねえか♡」\n来たっ！☆\n'
                    '「(´・ω・`)」\nおや、そうだった。\n')*5
        (self.corpus/'fixture_work_1.txt').write_text(self.story,encoding='utf-8')
        (self.corpus/'fixture_work_2.txt').write_text(self.story.replace('来たッ！','来たっ！'),encoding='utf-8')

    def tearDown(self):self.tmp.cleanup()

    def test_glyphs_repetition_and_pair_frequency(self):
        profile=lexical.collect(self.corpus,lexical.select_pairs(None),None)
        rows={tuple(x['variants']):x for x in profile['variant_pairs']}
        self.assertEqual(rows[('じゃねえか','じゃあねえか')]['counts'],[10,10])
        self.assertEqual(rows[('来たっ！','来たッ！')]['counts'],[15,5])
        self.assertEqual(rows[('来たっ！','来たッ！')]['work_counts'],[2,1])
        self.assertEqual(rows[('じゃねえか','じゃあねえか')]['share_within_pair'],[.5,.5])
        runs={(r['symbol'],r['repetitions']):r for r in profile['symbol_runs']}
        self.assertEqual(runs[('♡',3)]['count'],10)
        self.assertEqual(runs[('♪',2)]['count'],10)
        self.assertTrue(any(x['form']=='(´・ω・`)' for x in profile['kaomoji_candidates']))
        symbols={r['form']:r for r in profile['symbols']}
        self.assertEqual(symbols['♡']['count'],40)
        self.assertEqual(symbols['♡']['work_count'],2)
        self.assertEqual(symbols['♡']['contexts']['dialogue'],40)
        self.assertNotIn('fixture_work',json.dumps(profile,ensure_ascii=False))

    def test_metadata_is_not_a_kaomoji(self):
        sample = ("（user/54483384）（illust/87089555）"
                  "（https://example.org/path）（よとぎ・よみ）"
                  "（´・ω・）")
        faces = lexical.kaomoji(sample)
        self.assertEqual(list(faces), ["（´・ω・）"])

    def test_unicode_forms_not_normalized(self):
        raw='「来たっ！」と「来たッ！」。\n！！と!!。\n♡ ♥ ❤ ♪ ☆'
        (self.corpus/'fixture_work_1.txt').write_text(raw*5,encoding='utf-8')
        profile=lexical.collect(self.corpus,lexical.select_pairs(None),None)
        symbols={r['form']:r['count'] for r in profile['symbols']}
        self.assertNotEqual(symbols['♡'], symbols['❤']+99)
        self.assertEqual(symbols['！'],30)
        self.assertNotEqual(symbols.get('!'),symbols.get('！'))

    def test_reference_keyness_uses_full_counters(self):
        reference=self.root/'reference';reference.mkdir()
        (reference/'a.txt').write_text('山 道 山 道 山 道 山 道 '*25,encoding='utf-8')
        (reference/'b.txt').write_text('山 道 山 道 山 道 山 道 '*25,encoding='utf-8')
        (self.corpus/'fixture_work_1.txt').write_text('海 海 海 海 山 '*30,encoding='utf-8')
        (self.corpus/'fixture_work_2.txt').write_text('海 海 海 海 山 '*30,encoding='utf-8')
        tagger=SimpleTagger()
        target=lexical.collect(self.corpus,lexical.select_pairs(None),tagger,retain_internal=True)
        ref=lexical.collect(reference,lexical.select_pairs(None),tagger,retain_internal=True)
        rows=lexical.keyness(target,ref)
        self.assertEqual(rows[0]['form'],'海')
        self.assertEqual(rows[0]['target_work_count'],2)
        self.assertEqual(rows[0]['reference_count'],0)
        self.assertGreater(rows[0]['log2_relative_frequency'],0)

    def test_run_local_only_produces_combined_bible(self):
        out=self.root/'output'
        proc=subprocess.run([sys.executable,str(SCRIPTS/'run.py'),str(self.corpus),
                             '--mode','none','--morphology','none','--out',str(out)],
                             capture_output=True,text=True)
        self.assertEqual(proc.returncode,0,proc.stderr)
        guide=(out/'style_bible.md').read_text(encoding='utf-8')
        self.assertIn('じゃねえか',guide)
        self.assertIn('♡',guide)
        lex=json.loads((out/'lexical_profile.json').read_text(encoding='utf-8'))
        self.assertEqual(lex['works'],2)
        self.assertFalse((out/'narrative_fast_checkpoint.sqlite3').exists())
        self.assertNotIn('fixture_work',guide)

    def test_custom_pair_validation(self):
        variants=self.root/'pairs.json'
        variants.write_text(json.dumps([['じゃん','じゃーん']],ensure_ascii=False),encoding='utf-8')
        self.assertIn(('じゃん','じゃーん'),lexical.select_pairs(variants))
        variants.write_text(json.dumps([['a','a']]),encoding='utf-8')
        with self.assertRaises(ValueError):lexical.select_pairs(variants)

if __name__=='__main__':unittest.main()
