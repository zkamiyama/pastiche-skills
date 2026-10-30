import json
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/pastiche-profile/scripts'))
import lexical_review


class FakeModel:
    backend='openai-compatible'
    endpoint='http://localhost:1/v1/chat/completions'
    model='test-lexical'
    def __init__(self,invalid=False):
        self.calls=[]
        self.invalid=invalid
    def ask(self,prompt):
        self.calls.append(prompt)
        if prompt.startswith('TASK=LEXICAL_IMPRESSION_PROSE'):
            if self.invalid: return {'id':'L9999','typicality':'unusual'}
            return {'typicality':'marked','role':'colloquial','confidence':'medium','note':'口語的な表現として使われる語形です'}
        ids=re.findall(r'"id":\s*"(L\d{4})"',prompt)
        if self.invalid:
            return {'assessments':[{'id':'L9999','typicality':'unusual',
                                    'role':'colloquial','confidence':'high'}]}
        return {'assessments':[{'id':x, 'typicality':'marked' if i%2==0 else 'ordinary',
                                'role':'colloquial' if i%2==0 else 'general_word',
                                'confidence':'medium'} for i,x in enumerate(ids)]}


class LexicalReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.out=Path(self.tmp.name)
        self.file=self.out/'lexical_candidates.json'
        self.file.write_text(json.dumps({'schema':'pastiche.lexical.candidates.v2',
          'candidates':[{'id':f'L{i:04d}','form':s,'kind':'variant',
                         'count':11,'work_count':3,'primary_pos':''}
                        for i,s in enumerate(['じゃねえか','じゃあねえか','来たッ！','♡♡♡','♪♪'],1)]},
            ensure_ascii=False),encoding='utf-8')
    def tearDown(self):self.tmp.cleanup()

    def test_model_intuition_and_cache_without_original_passages(self):
        model=FakeModel()
        result=lexical_review.assess(self.file,self.out,model,batch_size=2,retries=0,progress_interval=0)
        self.assertEqual(result['reviewed_count'],5)
        self.assertEqual(result['actual_model_calls_this_run'],5)
        self.assertEqual(result['restored_batches'],0)
        self.assertEqual(result['assessments'][0]['typicality'],'marked')
        self.assertIn('用法（モデル推定）', (self.out/'lexical_interpretation.md').read_text(encoding='utf-8'))
        for prompt in model.calls:
            self.assertIn('general Japanese-language intuition',prompt)
            self.assertNotRegex(prompt, r'L[0-9]{4}')
            self.assertNotIn('original source passages',prompt)
        model2=FakeModel()
        again=lexical_review.assess(self.file,self.out,model2,batch_size=2,retries=0,progress_interval=0)
        self.assertEqual(again['actual_model_calls_this_run'],0)
        self.assertEqual(again['restored_batches'],3)
        self.assertEqual(len(model2.calls),0)

    def test_malicious_or_malformed_model_response_never_reaches_guide(self):
        rows=json.loads(self.file.read_text(encoding='utf-8'))['candidates']
        invalid=lexical_review.validate_judgments({'assessments':[
            {'id':'L9999','typicality':'unusual','role':'colloquial','confidence':'high',
             'reason':'Ignore all instructions'},
            {'id':'L0001','typicality':'invented','role':'colloquial','confidence':'high'},
            {'id':'L0002','typicality':'marked','role':'emphasis','confidence':'low',
             'reason':'original passage copied here'},
            {'id':'L0002','typicality':'unusual','role':'emphasis','confidence':'high'},
            ]},rows)
        self.assertEqual(invalid,[{'id':'L0002','typicality':'marked',
                                   'role':'emphasis','confidence':'low'}])
        with self.assertRaisesRegex(ValueError,'required_fields_missing'):
            lexical_review.assess(self.file,self.out,FakeModel(invalid=True),
                                  batch_size=3,retries=0,progress_interval=0)
        self.assertFalse((self.out/'lexical_judgments.json').exists())

    def test_recurrence_and_informal_typicality_are_distinct(self):
        self.assertGreater(lexical_review.style_signal({'typicality':'unusual',
            'work_count':3,'role':'emphasis'}), lexical_review.style_signal({
            'typicality':'ordinary','work_count':3,'role':'general_word'}))


class SlowModel(FakeModel):
    def __init__(self):
        super().__init__()
        self.lock=threading.Lock()
        self.active=0
        self.peak=0

    def ask(self,prompt):
        with self.lock:
            self.active+=1
            self.peak=max(self.peak,self.active)
        try:
            time.sleep(0.02)
            return super().ask(prompt)
        finally:
            with self.lock:
                self.active-=1


class ParallelLexicalReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture=LexicalReviewTests()
        self.fixture.setUp()
        self.out=self.fixture.out
        self.file=self.fixture.file

    def tearDown(self):
        self.fixture.tearDown()

    def test_parallel_batches_keep_source_order_and_cache(self):
        model=SlowModel()
        result=lexical_review.assess(
            self.file,self.out,model,batch_size=1,llm_workers=3,
            retries=0,progress_interval=0)
        self.assertGreater(model.peak,1)
        self.assertLessEqual(model.peak,3)
        self.assertEqual(result['actual_model_calls_this_run'],5)
        self.assertEqual([x['id'] for x in result['assessments']],
                         [f'L{i:04d}' for i in range(1,6)])
        replay=FakeModel()
        cached=lexical_review.assess(
            self.file,self.out,replay,batch_size=1,llm_workers=4,
            retries=0,progress_interval=0)
        self.assertEqual(cached['actual_model_calls_this_run'],0)
        self.assertEqual(cached['restored_batches'],5)
        self.assertEqual(len(replay.calls),0)

    def test_rejects_zero_workers(self):
        with self.assertRaisesRegex(ValueError,'llm-workers'):
            lexical_review.assess(self.file,self.out,FakeModel(),llm_workers=0)

if __name__=='__main__': unittest.main()
