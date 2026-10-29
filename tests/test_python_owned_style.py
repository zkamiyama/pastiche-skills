"""Executable contracts for ID-free requests; synthetic data only."""
from __future__ import annotations
import copy
from contextlib import redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1]/'skills/pastiche-profile/scripts'
sys.path.insert(0,str(SCRIPTS))
import llm_runtime
import llm_style
import llm_style_core as core
import prose_contracts as pc
from prose_runtime import call_prose, fingerprint
from python_cluster import call_cluster, validate_panel, SHORTLIST_LIMIT
from prose_observe_recovery import call_observe
from task_transport import SubagentModel, TasksPending, list_tasks, submit
from test_llm_style import FixtureModel

CHUNK=[('W1234:S0001','朝の古い駅で赤い手袋を見つけた。'),
       ('W1234:S0002','夕方の広場には青い時計が残された。')]

def observed():
    return {'observations':[{'dimension':'emotion','trigger':'予想外の出来事を受け止める場面',
        'technique':'小さな行動の連鎖で変化を段階的に示す','effect':'ためらいを間接的に伝える',
        'evidence_quotes':['古い駅で赤い手袋','広場には青い時計'],'anchors':[]}]}

def node(i):
    return {'id':f'O{i:05d}','members':[f'O{i:05d}'],'dimension':'lexicon',
        'trigger':f'合成の場面を表す記述{i}','technique':f'言葉の反復で間を示す操作{i}',
        'effect':'感情の動きを間接的に伝える','label':''}

def yes():
    return {'compatible':True,'mechanism':'反復によって感情の間を示す','reason':'表現方法と条件が一致しています'}

def no():
    return {'compatible':False,'mechanism':'','reason':'共通するのは題材だけです'}

class Model:
    backend='fixture'; model='fixture'; endpoint='fixture'; max_output_tokens=4096
    def __init__(self,replies): self.replies=iter(replies); self.prompts=[]
    def ask(self,prompt):
        self.prompts.append(prompt); value=next(self.replies)
        if isinstance(value,BaseException): raise value
        return copy.deepcopy(value)

class ProseGroundingTests(unittest.TestCase):
    def test_prompt_contains_no_generated_segment_identifiers(self):
        prompt=pc.observation_prompt(CHUNK)
        self.assertNotIn('W1234',prompt); self.assertNotIn('S0001',prompt)
        self.assertIn(CHUNK[0][1],prompt)

    def test_python_resolves_exact_quotes_and_discards_them(self):
        clean,report=pc.bind_observations(observed(),CHUNK)
        self.assertEqual(clean[0]['evidence_ids'],[s for s,_ in CHUNK])
        self.assertNotIn('evidence_quotes',clean[0])
        self.assertEqual(report['accepted_count'],1)
        self.assertNotIn('古い駅で赤い手袋',json.dumps(clean,ensure_ascii=False))

    def test_quotes_in_two_passages_are_not_guessed(self):
        duplicate=CHUNK+ [('W1234:S0003',CHUNK[0][1])]
        with self.assertRaises(core.ObservationValidationError) as cm:
            pc.bind_observations(observed(),duplicate)
        self.assertIn('evidence_quote_ambiguous',str(cm.exception))

    def test_same_passage_twice_is_not_two_sources(self):
        r=observed();r['observations'][0]['evidence_quotes']=['古い駅で赤い手袋','赤い手袋を見つけた']
        with self.assertRaisesRegex(core.ObservationValidationError,'two_distinct_source_passages'):
            pc.bind_observations(r,CHUNK)

    def test_unseen_quote_is_rejected_without_fuzzy_correction(self):
        r=observed();r['observations'][0]['evidence_quotes'][0]='古い駅で赤い手ぶくろ'
        with self.assertRaisesRegex(core.ObservationValidationError,'evidence_quote_not_found'):
            pc.bind_observations(r,CHUNK)

    def test_unicode_is_not_normalized_for_evidence(self):
        r=observed();r['observations'][0]['evidence_quotes'][0]='ここでは♥を示す'
        chunk=[(CHUNK[0][0],'ここでは♡を示す。'),CHUNK[1]]
        with self.assertRaisesRegex(core.ObservationValidationError,'evidence_quote_not_found'):
            pc.bind_observations(r,chunk)

    def test_injected_evidence_ids_are_never_used(self):
        r=observed();r['observations'][0]['evidence_ids']=[s for s,_ in CHUNK]
        with self.assertRaisesRegex(core.ObservationValidationError,'unexpected_fields'):
            pc.bind_observations(r,CHUNK)

    def test_partial_valid_results_survive_with_rejection_diagnostic(self):
        r=observed();bad=copy.deepcopy(r['observations'][0]);bad['evidence_quotes'][0]='PRIVATE_UNKNOWN_PHRASE'
        r['observations'].append(bad)
        clean,d=pc.bind_observations(r,CHUNK)
        self.assertEqual((len(clean),d['rejected_count']),(1,1))
        self.assertNotIn('PRIVATE_UNKNOWN_PHRASE',json.dumps(d))

    def test_copied_source_description_still_fails(self):
        r=observed();r['observations'][0]['technique']=CHUNK[0][1]
        with self.assertRaisesRegex(core.ObservationValidationError,'source_verbatim_overlap'):
            pc.bind_observations(r,CHUNK)

    def test_duplicate_python_source_ids_fail(self):
        with self.assertRaisesRegex(ValueError,'duplicate source'):
            pc.bind_observations(observed(),[CHUNK[0],(CHUNK[0][0],CHUNK[1][1])])

    def test_bad_source_verdict_types_are_safe_errors(self):
        for basis in (None,[],{},'invented'):
            with self.subTest(basis=basis),self.assertRaises(pc.ProseError):
                pc.parse_evidence({'supported':True,'basis':basis,'reason':'説明の文章です'})

    def test_model_cannot_return_membership_or_boolean_as_string(self):
        for r in [dict(yes(),ids=['O00139']),dict(yes(),compatible='true')]:
            with self.subTest(r=r),self.assertRaises(pc.ProseError): pc.parse_comparison(r)

    def test_reference_free_prompts_and_scope(self):
        ob=pc.bind_observations(observed(),CHUNK)[0][0]
        ob.update(id='O00139',work_id='W1234',chunk_index=1)
        pattern={'pattern_id':'P0001','status':'model_supported','dimension':'emotion',
                 'trigger':ob['trigger'],'mechanism':ob['technique'],'effect':ob['effect'],'support_work_count':2}
        prompts=[pc.comparison_prompt('lexicon',node(139),[node(3)]),
                 pc.review_prompt('emotion',ob,'共通する操作です'),pc.evidence_prompt(CHUNK,ob,'共通する操作です'),
                 pc.synthesis_prompt(pattern),pc.lexical_prompt({'id':'L0001','form':'合成語','kind':'word','count':2,'work_count':2})]
        for prompt in prompts:
            self.assertNotRegex(prompt,r'\b(?:O[0-9]{5,}|W[0-9]{4}:S[0-9]+|P[0-9]{4}|L[0-9]{4})\b')

    def test_python_rejects_wrong_source_before_prompt_generation(self):
        ob=pc.bind_observations(observed(),CHUNK)[0][0];ob['evidence_ids'][0]='UNKNOWN'
        with self.assertRaisesRegex(ValueError,'invalid Python source binding'):
            pc.evidence_prompt(CHUNK,ob,'表現の操作です')

class BoundRequestTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.out=Path(self.tmp.name)
        self.reporter=llm_runtime.ProgressReporter(self.out,interval=0)
    def tearDown(self):self.tmp.cleanup()
    def call(self,m,**kw):
        options=dict(kind='comparison',binding={'input':'fixed'},prompt='TASK=synthetic',
                     parser=pc.parse_comparison,label='SYNTHETIC',retries=2,retry_delay=0)
        options.update(kw)
        with redirect_stderr(io.StringIO()):return call_prose(m,self.reporter,**options)

    def test_bounded_transport_and_validation_share_budget(self):
        m=Model([llm_runtime.ModelRequestError('private server body'),{'ids':['UNKNOWN']},yes()])
        self.assertEqual(self.call(m),yes());self.assertEqual(len(m.prompts),3)
        d=''.join(p.read_text() for p in (self.out/'style_binding_diagnostics').glob('*.json'))
        self.assertNotIn('private server body',d);self.assertNotIn('UNKNOWN',d)

    def test_invalid_never_becomes_empty_success(self):
        m=Model([{'ids':['UNKNOWN']},{},{}])
        with self.assertRaises(pc.ProseError):self.call(m)
        self.assertEqual(len(m.prompts),3)
        self.assertFalse(list((self.out/'style_binding_cache').glob('*.json')))

    def test_nonretryable_transport_stops_once(self):
        m=Model([llm_runtime.ModelRequestError('safe',transient=False),yes()])
        with self.assertRaises(llm_runtime.ModelRequestError):self.call(m)
        self.assertEqual(len(m.prompts),1)

    def test_binding_cache_reuses_validated_result(self):
        self.call(Model([yes()]));m=Model([])
        self.assertEqual(self.call(m),yes());self.assertEqual(m.prompts,[])

    def test_restart_bypasses_private_task_cache_without_deleting_it(self):
        self.call(Model([yes()]));self.reporter.reuse_prose_cache=False
        m=Model([no()]);self.assertEqual(self.call(m),no())
        self.assertEqual(len(m.prompts),1)
        self.assertEqual(len(list((self.out/'style_binding_cache').glob('*.json'))),1)

    def test_different_binding_cannot_reuse_result(self):
        self.call(Model([yes()]));m=Model([no()])
        self.assertEqual(self.call(m,binding={'input':'different'}),no())
        self.assertEqual(len(m.prompts),1)

    def test_corrupt_cache_is_revalidated_not_trusted(self):
        self.call(Model([yes()]));cache=next((self.out/'style_binding_cache').glob('*.json'))
        cache.write_text('[]');m=Model([no()])
        self.assertEqual(self.call(m),no());self.assertEqual(len(m.prompts),1)

    def test_source_reason_is_discarded_from_cache(self):
        m=Model([{'supported':True,'basis':'rhetorical','reason':'PRIVATE_SOURCE_EXCERPT'}])
        kwargs=dict(kind='source',parser=pc.evidence_verdict,cache_validator=pc.cached_evidence)
        self.assertEqual(self.call(m,**kwargs),{'supported':True,'basis':'rhetorical'})
        files=''.join(p.read_text() for p in self.out.rglob('*.json'))
        self.assertNotIn('PRIVATE_SOURCE_EXCERPT',files)
        self.assertEqual(self.call(Model([]),**kwargs),{'supported':True,'basis':'rhetorical'})

    def test_effort_and_version_affect_task_identity(self):
        off=llm_runtime.HTTPModel('openai-compatible','fixture','http://localhost/v1')
        auto=llm_runtime.HTTPModel('openai-compatible','fixture','http://localhost/v1',reasoning_effort='auto')
        first=fingerprint('task',{},off)
        self.assertNotEqual(first,fingerprint('task',{},auto))
        with patch.object(pc,'VERSION','test-next-version'):
            self.assertNotEqual(first,fingerprint('task',{},off))

    def test_host_handoff_resume_never_requires_model_ids(self):
        m=SubagentModel(self.out/'queue','fixture')
        with self.assertRaises(TasksPending):self.call(m)
        pending=list_tasks(m.task_dir);self.assertEqual(len(pending),1)
        submit(m.task_dir,pending[0]['task_id'],yes())
        self.assertEqual(self.call(m),yes())

    def test_observe_repair_does_not_accept_empty_after_failure(self):
        m=Model([{'observations':[{'id':'made-up'}]},{'observations':[]}])
        with redirect_stderr(io.StringIO()),self.assertRaisesRegex(core.ObservationValidationError,'empty_after'):
            call_observe('W1234',CHUNK,m,self.reporter,retries=1,chunk_index=1,retry_delay=0)

    def test_observe_repair_discards_quotes_and_retains_bound_ids(self):
        m=Model([{'observations':[{'id':'made-up'}]},observed()])
        with redirect_stderr(io.StringIO()):
            value=call_observe('W1234',CHUNK,m,self.reporter,retries=1,chunk_index=1,retry_delay=0)
        self.assertEqual(value['observations'][0]['evidence_ids'],[e for e,_ in CHUNK])
        self.assertNotIn('evidence_quotes',json.dumps(value))
        self.assertNotIn('古い駅で赤い手袋',''.join(p.read_text() for p in self.out.rglob('*.json')))

    def test_loopback_http_preserves_off_and_rejects_model_ids(self):
        bodies=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));bodies.append(body)
                reply=dict(yes(),ids=['UNKNOWN']) if len(bodies)==1 else yes()
                blob=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(reply)}}]}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(blob)));self.end_headers();self.wfile.write(blob)
        srv=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        t=threading.Thread(target=srv.serve_forever,daemon=True);t.start()
        try:
            m=llm_runtime.HTTPModel('openai-compatible','fixture',f'http://localhost:{srv.server_port}/v1',stream=False)
            self.assertEqual(self.call(m),yes());self.assertEqual(len(bodies),2)
            for body in bodies:
                self.assertEqual(body['reasoning_effort'],'none')
                self.assertFalse(body['chat_template_kwargs']['enable_thinking'])
                self.assertNotIn('UNKNOWN',body['messages'][-1]['content'])
        finally:srv.shutdown();srv.server_close();t.join(2)

class PythonMembershipTests(unittest.TestCase):
    setUp = BoundRequestTests.setUp
    tearDown = BoundRequestTests.tearDown
    def cluster(self,rows,m):
        with redirect_stderr(io.StringIO()):
            return call_cluster('lexicon',rows,m,self.reporter,retries=0,round_number=1,panel_number=1)

    def test_membership_is_assembled_only_from_python_nodes(self):
        rows=[node(1),node(139),node(945)];m=Model([yes(),yes()])
        result=self.cluster(rows,m)
        self.assertEqual(result['groups'][0]['ids'],['O00001','O00139','O00945'])
        for prompt in m.prompts:self.assertNotRegex(prompt,r'O[0-9]{5,}')

    def test_no_transitive_union_without_group_compatibility(self):
        rows=[node(1),node(2),node(3)];m=Model([yes(),no()])
        result=self.cluster(rows,m)
        self.assertEqual(result['groups'][0]['ids'],['O00001','O00002'])
        payload=json.loads(m.prompts[-1].split('COMPARISON=',1)[1])
        self.assertEqual(len(payload['group']),2)
        self.assertEqual(len(core.regroup(rows,core.parse_clusters(result,{v['id'] for v in rows}))),2)

    def test_all_negative_retains_singletons(self):
        rows=[node(i) for i in range(1,9)];m=Model([no()]*30)
        result=self.cluster(rows,m);self.assertEqual(result,{'groups':[]})
        self.assertLessEqual(len(m.prompts),SHORTLIST_LIMIT*(len(rows)-1))
        self.assertEqual(core.regroup(rows,[]),rows)

    def test_overlap_in_python_inputs_rejected_before_llm(self):
        rows=[node(1),node(2)];rows[1]['members']=['O00001'];m=Model([])
        with self.assertRaisesRegex(ValueError,'overlapping'):self.cluster(rows,m)
        self.assertEqual(m.prompts,[])

    def test_foreign_model_memberships_are_not_corrected(self):
        m=Model([dict(yes(),ids=['O000139'])])
        with self.assertRaisesRegex(pc.ProseError,'unexpected_fields'):
            self.cluster([node(1),node(139)],m)

    def test_failed_panel_reuses_completed_bound_comparisons(self):
        rows=[node(1),node(2),node(3)]
        with self.assertRaises(pc.ProseError):self.cluster(rows,Model([yes(),{}]))
        m=Model([yes()]);r=self.cluster(rows,m)
        self.assertEqual(len(m.prompts),1);self.assertEqual(len(r['groups'][0]['ids']),3)

    def test_summary_binding_survives_out_of_order_completion(self):
        patterns=[{'pattern_id':f'P{i:04d}','status':'model_supported','dimension':'emotion',
            'trigger':f'場面の条件{i}','mechanism':f'描写操作{i}','effect':'感情を間接的に示す','support_work_count':2} for i in range(1,5)]
        class Notes(Model):
            def ask(self,prompt):
                payload=json.loads(prompt.split('PATTERN=',1)[1]);n=int(payload['mechanism'][-1])
                time.sleep((5-n)*0.005)
                return {'summary':f'合成の描写操作{n}に限定した要約です','writing_use':'別の場面で同じ操作を用います'}
        store=llm_runtime.CheckpointStore(self.out/'main.sqlite3')
        try:
            with redirect_stderr(io.StringIO()):
                rows,calls,_=llm_style._synthesize(patterns,Notes([]),store,self.reporter,4,0)
            self.assertEqual(calls,4)
            for i,row in enumerate(rows,1):
                self.assertEqual(row['pattern_ids'],[f'P{i:04d}']);self.assertIn(f'操作{i}',row['summary'])
        finally:store.close()

if __name__=='__main__':unittest.main()
