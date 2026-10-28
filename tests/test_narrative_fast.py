import json
from pathlib import Path
import re
import sys
import threading
import time
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/pastiche-profile/scripts'))
import narrative_fast
import pastiche

class FakeModel:
    backend='openai-compatible'
    model='fixture'
    endpoint='http://127.0.0.1:1/v1/chat/completions'
    def __init__(self,fail=False):
        self.calls=[]
        self.fail=fail
    def ask(self,prompt):
        self.calls.append(prompt.split('.')[0])
        ids=re.findall(r'\[(W\d{4}:S\d{4,})\]',prompt)
        if prompt.startswith('TASK=DISCOVER'):
            if self.fail:
                raise ValueError('fake model unavailable')
            return {'patterns':[{'trigger':'人物の信念に反する知らせが入る',
                                 'technique':'解釈をすぐに確定せず別の証拠を先に置く',
                                 'effect':'読者が後から前の判断を捉え直す',
                                 'category':'information','basis':'rhetorical',
                                 'certainty':'explicit','evidence_ids':ids[:2]}]}
        return {'verdict':'supported','basis':'rhetorical','evidence_ids':ids[:2]}

class FastNarrativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.corpus=self.root/'corpus';self.corpus.mkdir()
        for i in range(12):
            text=('その知らせを聞いた。次に道を歩いていた。\n'+'遠くで小さな音が聞こえた。\n')*100
            (self.corpus/f'story_{i:03}.txt').write_text(text,encoding='utf-8')
        self.out=self.root/'out'
    def tearDown(self):self.tmp.cleanup()

    def test_stratified_sampling_and_cache(self):
        fake=FakeModel()
        audit=narrative_fast.analyze(self.corpus,self.out,fake,max_works=4,chunks_per_work=2,
                                     max_verification_groups=2,retries=0,progress_interval=0)
        self.assertEqual(audit['sampled_works'],4)
        self.assertEqual(audit['selected_chunks'],8)
        self.assertEqual(audit['actual_llm_calls_this_run'],10)
        self.assertGreater(audit['baseline_max_llm_calls'],10)
        rules=json.loads((self.out/'narrative_rules.json').read_text(encoding='utf-8'))['rules']
        self.assertEqual(rules[0]['status'],'model_supported')
        self.assertTrue(pastiche.is_guide_eligible(rules[0]))
        for output in (self.out/'narrative_rules.json',self.out/'narrative_audit.json'):
            self.assertNotIn('その知らせを聞いた',output.read_text(encoding='utf-8'))
        second=FakeModel()
        after=narrative_fast.analyze(self.corpus,self.out,second,max_works=4,chunks_per_work=2,
                                    max_verification_groups=2,retries=0,progress_interval=0)
        self.assertEqual(after['actual_llm_calls_this_run'],0)
        self.assertEqual(after['restored_extract_chunks'],8)

    def test_sampling_includes_ends_and_rotates_middle(self):
        self.assertEqual(narrative_fast.stratified_indices(91,5),[0,22,45,68,90])
        chunks=list(range(8))
        self.assertEqual([a for a,_ in narrative_fast.select_chunks(chunks,2,0)],[5,8])
        self.assertEqual([a for a,_ in narrative_fast.select_chunks(chunks,2,1)],[1,8])
        self.assertEqual([a for a,_ in narrative_fast.select_chunks(chunks,2,2)],[1,5])

    def test_does_not_persist_source_or_fake_evidence(self):
        chunk=[('W0001:S0001','具体的な出来事の説明'),('W0001:S0002','もう一つの出来事')]
        item={'trigger':'具体的な出来事の説明','technique':'結果が変化する仕組みを出す',
              'effect':'読者が別の見方を獲得する','category':'belief','basis':'causal',
              'certainty':'explicit','evidence_ids':['W0001:S0001','W0001:S0002']}
        self.assertEqual(narrative_fast.clean_patterns({'patterns':[item]},chunk),[])
        item['trigger']='人物の考えが変わる'
        item['evidence_ids']=['W0001:S0001','W0001:S9999']
        self.assertEqual(narrative_fast.clean_patterns({'patterns':[item]},chunk),[])
        item['evidence_ids']=[['bad'],{'bad':1}]
        self.assertEqual(narrative_fast.clean_patterns({'patterns':[item]},chunk),[])

    def test_partial_restarts_from_checkpoint(self):
        first=FakeModel(fail=True)
        with self.assertRaises(ValueError):
            narrative_fast.analyze(self.corpus,self.out,first,max_works=2,
                                   chunks_per_work=1,progress_interval=0,retries=0)
        report=json.loads((self.out/'narrative_progress.json').read_text(encoding='utf-8'))
        self.assertEqual(report['state'],'failed')
        good=FakeModel()
        audit=narrative_fast.analyze(self.corpus,self.out,good,max_works=2,
                                     chunks_per_work=1,progress_interval=0,retries=0)
        self.assertEqual(audit['selected_chunks'],2)


class SlowModel(FakeModel):
    """Synthetic endpoint with measurable in-flight concurrency."""
    def __init__(self):
        super().__init__()
        self.lock=threading.Lock()
        self.in_flight=0
        self.peak=0

    def ask(self,prompt):
        with self.lock:
            self.in_flight+=1
            self.peak=max(self.peak,self.in_flight)
        try:
            time.sleep(0.02)
            return super().ask(prompt)
        finally:
            with self.lock:
                self.in_flight-=1


class ParallelFastNarrativeTests(FastNarrativeTests):
    def test_parallel_matches_serial_output_and_resumes(self):
        serial_out=self.root/'serial'
        expected=narrative_fast.analyze(
            self.corpus,serial_out,FakeModel(),max_works=6,
            chunks_per_work=2,max_verification_groups=2,
            retries=0,progress_interval=0)
        probe=SlowModel()
        parallel=narrative_fast.analyze(
            self.corpus,self.out,probe,max_works=6,
            chunks_per_work=2,max_verification_groups=2,
            llm_workers=3,retries=0,progress_interval=0)
        self.assertGreater(probe.peak,1)
        self.assertLessEqual(probe.peak,3)
        self.assertEqual(parallel['actual_llm_calls_this_run'],
                         expected['actual_llm_calls_this_run'])
        for filename in ('narrative_rules.json','narrative_audit.json'):
            self.assertEqual((serial_out/filename).read_text(encoding='utf-8'),
                             (self.out/filename).read_text(encoding='utf-8'))

        replay=narrative_fast.analyze(
            self.corpus,self.out,FakeModel(),max_works=6,
            chunks_per_work=2,max_verification_groups=2,
            llm_workers=4,retries=0,progress_interval=0)
        self.assertEqual(replay['actual_llm_calls_this_run'],0)
        self.assertEqual(replay['restored_extract_chunks'],12)

    def test_rejects_zero_workers(self):
        with self.assertRaisesRegex(ValueError,'llm-workers'):
            narrative_fast.analyze(self.corpus,self.out,FakeModel(),llm_workers=0)

if __name__=='__main__':unittest.main()
