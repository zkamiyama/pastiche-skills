"""Free explanation regression tests; synthetic prose and loopback HTTP only."""
from __future__ import annotations
from contextlib import redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/pastiche-profile/scripts'
sys.path.insert(0, str(SCRIPTS))
import llm_runtime
import llm_style
import llm_style_core as core
import prose_contracts as pc
from prose_runtime import call_prose, fingerprint
from task_transport import SubagentModel, TasksPending, list_tasks, submit
from test_python_owned_style import Model, CHUNK, observed, node, yes


def evidence(reason='根拠を説明する合成文です', supported=True, basis='rhetorical'):
    return {'supported': supported, 'basis': basis, 'reason': reason}


class ExplanationContractTests(unittest.TestCase):
    def test_429_character_reason_is_not_rejected_or_truncated(self):
        text = '説明文' * 143
        self.assertEqual(len(text), 429)
        self.assertEqual(pc.parse_evidence(evidence(text))['reason'], text)
        self.assertEqual(pc.evidence_verdict(evidence(text)), {'supported': True, 'basis': 'rhetorical'})

    def test_explanation_fields_have_same_generous_envelope(self):
        for length in (1, 2, 3, 300, 301, pc.EXPLANATION_MAX_CHARS):
            text = '文' * length
            cases = [
                (pc.parse_evidence, evidence(text), 'reason'),
                (pc.parse_review, {'compatible': True, 'reason': text}, 'reason'),
                (pc.parse_comparison, dict(yes(), reason=text), 'reason'),
                (pc.parse_lexical, {'typicality': 'ordinary', 'role': 'general_word',
                                   'confidence': 'medium', 'note': text}, 'note'),
            ]
            for parser, raw, field in cases:
                with self.subTest(length=length, parser=parser.__name__):
                    self.assertEqual(parser(raw)[field], text)

    def test_negative_verdict_is_not_changed_to_success(self):
        raw = evidence('否定の理由を示す。' * 100, False, 'unclear')
        self.assertEqual(pc.evidence_verdict(raw), {'supported': False, 'basis': 'unclear'})

    def test_paragraphs_and_exact_unicode_are_preserved(self):
        text = '第一段落。\n\n第二段落は♡♥とe\u0301。'
        self.assertEqual(pc.parse_evidence(evidence('  '+text+'\n'))['reason'], text)

    def test_wrong_types_get_safe_type_metadata(self):
        for value, kind in [(None,'null'), (True,'boolean'), (1,'number'),
                            (1.2,'number'), ([], 'array'), ({'PRIVATE':'value'},'object')]:
            with self.subTest(kind=kind), self.assertRaises(pc.ProseError) as cm:
                pc.parse_evidence(evidence(value))
            d=cm.exception.diagnostic()
            self.assertEqual(d['code'],'description_string_required')
            self.assertEqual(d['field'],'reason');self.assertEqual(d['actual_type'],kind)
            self.assertNotIn('PRIVATE',json.dumps(d))

    def test_empty_reason_is_not_an_accepted_explanation(self):
        for value in ('', ' \n\t\u3000'):
            with self.assertRaises(pc.ProseError) as cm:pc.parse_evidence(evidence(value))
            self.assertEqual(cm.exception.diagnostic()['actual_length'],0)
            self.assertEqual(cm.exception.code,'description_too_short')

    def test_resource_cap_reports_lengths_but_never_text(self):
        text='PRIVATE_RESPONSE' * 2000
        with self.assertRaises(pc.ProseError) as cm:pc.parse_evidence(evidence(text))
        d=cm.exception.diagnostic()
        self.assertEqual(d['code'],'description_too_long')
        self.assertEqual(d['maximum'],pc.EXPLANATION_MAX_CHARS)
        self.assertEqual(d['actual_length'],len(text))
        self.assertNotIn('PRIVATE_RESPONSE',str(cm.exception)+json.dumps(d))

    def test_bad_verdicts_and_foreign_ids_are_still_rejected(self):
        for raw in [evidence('説明'*300,'true'), evidence('説明'*300,True,'unknown'),
                    dict(evidence('説明'*300), evidence_ids=['INVENTED'])]:
            with self.subTest(raw_keys=list(raw)),self.assertRaises(pc.ProseError):pc.evidence_verdict(raw)

    def test_narrative_temporal_only_still_does_not_establish_support(self):
        item=pc.bind_observations(observed(),CHUNK)[0][0];item['dimension']='narrative'
        raw=pc.evidence_verdict(evidence('説明'*300, True, 'temporal_only'))
        self.assertFalse(core.valid_evidence({**raw,'evidence_ids':item['evidence_ids']},item,CHUNK))

    def test_compact_summary_limits_are_not_relaxed(self):
        good={'summary':'あ'*8,'writing_use':'い'*160}
        self.assertEqual(pc.parse_synthesis(good),good)
        for key,n,code in [('summary',7,'description_too_short'),('writing_use',161,'description_too_long')]:
            with self.assertRaises(pc.ProseError) as cm:pc.parse_synthesis({**good,key:'文'*n})
            d=cm.exception.diagnostic()
            self.assertEqual((d['code'],d['field'],d['minimum'],d['maximum']),(code,key,8,160))

    def test_explanation_prompts_disclose_actual_bounds(self):
        ob=pc.bind_observations(observed(),CHUNK)[0][0]
        prompts=[pc.evidence_prompt(CHUNK,ob,'合成の操作'),pc.review_prompt('emotion',ob,'合成の操作'),
                 pc.comparison_prompt('lexicon',node(1),[node(2)]),
                 pc.lexical_prompt({'form':'合成語','kind':'word','count':2,'work_count':2})]
        for prompt in prompts:
            self.assertIn(f'1-{pc.EXPLANATION_MAX_CHARS}',prompt)
            self.assertIn('paragraph breaks',prompt)
            self.assertNotIn('W1234:S0001',prompt)

    def test_diagnostic_fields_cannot_be_arbitrary_model_text(self):
        for kw in [{'field':'PRIVATE_FIELD'}, {'actual_type':'PRIVATE_CLASS'},
                   {'actual_length':True}, {'maximum':-1}]:
            with self.assertRaises(ValueError) as cm:pc.ProseError('description_too_long',**kw)
            self.assertNotIn('PRIVATE',str(cm.exception))


class ExplanationRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.out=Path(self.tmp.name)
        self.reporter=llm_runtime.ProgressReporter(self.out,interval=0)
    def tearDown(self):self.tmp.cleanup()
    def invoke(self,model,**overrides):
        options=dict(kind='source-check',binding={'source':'synthetic'},prompt='TASK=synthetic',
                     parser=pc.evidence_verdict,cache_validator=pc.cached_evidence,
                     label='STYLE EVIDENCE group observation=one',retries=2,retry_delay=0)
        options.update(overrides)
        with redirect_stderr(io.StringIO()):return call_prose(model,self.reporter,**options)
    def diagnostics(self):
        return [json.loads(p.read_text()) for p in (self.out/'style_binding_diagnostics').glob('*.json')]

    def test_long_source_explanation_is_discarded_before_persistence(self):
        secret='PRIVATE_SOURCE_SENTINEL'*30
        model=Model([evidence(secret)])
        self.assertEqual(self.invoke(model),{'supported':True,'basis':'rhetorical'})
        self.assertEqual(len(model.prompts),1)
        serialized=''.join(p.read_text() for p in self.out.rglob('*.json'))
        self.assertNotIn(secret,serialized);self.assertNotIn('PRIVATE_SOURCE_SENTINEL',serialized)
        self.assertEqual(self.invoke(Model([])),{'supported':True,'basis':'rhetorical'})

    def test_actionable_safe_feedback_survives_corrective_retry(self):
        secret='PRIVATE_RESPONSE'*2000
        model=Model([evidence(secret),evidence('修正した説明文です')])
        self.invoke(model)
        self.assertEqual(len(model.prompts),2)
        correction=model.prompts[1].split('OUTPUT_REPAIR: ',1)[1].split('. Re-examine',1)[0]
        d=json.loads(correction)
        self.assertEqual((d['field'],d['code'],d['maximum'],d['actual_length']),
                         ('reason','description_too_long',pc.EXPLANATION_MAX_CHARS,len(secret)))
        self.assertNotIn('PRIVATE_RESPONSE',model.prompts[1])
        self.assertEqual(self.diagnostics()[0]['attempts'][0]['actual_length'],len(secret))

    def test_summary_retry_identifies_the_exact_field_and_bounds(self):
        good={'summary':'表現方法を抽象的にまとめます','writing_use':'新しい場面で表現方法を選びます'}
        m=Model([dict(good,writing_use='文'*161),good])
        result=self.invoke(m,kind='synthesis',parser=pc.parse_synthesis,cache_validator=None)
        self.assertEqual(result,good)
        self.assertIn('"field": "writing_use"',m.prompts[1]);self.assertIn('"maximum": 160',m.prompts[1])

    def test_exhaustion_still_fails_and_does_not_cache_false_success(self):
        m=Model([evidence('')]*3)
        with self.assertRaises(pc.ProseError):self.invoke(m)
        self.assertEqual(len(m.prompts),3)
        self.assertEqual(self.diagnostics()[0]['state'],'failed')
        self.assertFalse(list((self.out/'style_binding_cache').glob('*.json')))

    def test_invalid_verdict_does_not_become_valid_because_reason_is_long(self):
        m=Model([evidence('説明'*300, 'true')]*3)
        with self.assertRaises(pc.ProseError):self.invoke(m)
        self.assertEqual(len(m.prompts),3)
        self.assertFalse(list((self.out/'style_binding_cache').glob('*.json')))

    def test_previous_short_result_cache_is_reused(self):
        m=Model([]);binding={'source':'synthetic'}
        key=fingerprint('source-check',binding,m)
        value={'supported':False,'basis':'unclear'}
        folder=self.out/'style_binding_cache';folder.mkdir()
        (folder/(key+'.json')).write_text(json.dumps({'contract':pc.VERSION,'binding_sha256':key,'value':value}))
        self.assertEqual(self.invoke(m),value);self.assertEqual(m.prompts,[])
        self.assertTrue(self.diagnostics()[0]['cache_hit'])

    def test_task_namespace_is_unchanged(self):
        self.assertEqual(pc.VERSION,'python-owned-style.v1')
        m=Model([]);binding={'fixture':1}
        expected=core.digest('python-owned-task',{'contract':'python-owned-style.v1',
                  'kind':'source-check','binding':binding,'max_output_tokens':4096},m)
        self.assertEqual(fingerprint('source-check',binding,m),expected)

    def test_host_repair_task_is_replayable_with_safe_length_details(self):
        m=SubagentModel(self.out/'queue','fixture')
        with self.assertRaises(TasksPending):self.invoke(m)
        task=list_tasks(m.task_dir)[0];submit(m.task_dir,task['task_id'],evidence(''))
        with self.assertRaises(TasksPending):self.invoke(m)
        task=next(t for t in list_tasks(m.task_dir) if t['state']=='pending')
        prompt=json.loads((m.task_dir/'requests'/f"{task['task_id']}.json").read_text())['prompt']
        self.assertIn('"field": "reason"',prompt)
        submit(m.task_dir,task['task_id'],evidence('説明'*300))
        self.assertEqual(self.invoke(m),{'supported':True,'basis':'rhetorical'})

    def test_source_pipeline_binds_true_false_and_reuses_checkpoints(self):
        observations={};chunks={}
        for i in (1,2):
            chunk=[(f'W{i:04d}:S0001','朝には赤い手袋を見つけた。'),(f'W{i:04d}:S0002','夕方には青い時計を見つけた。')]
            oid=f'O{i:05d}'
            observations[oid]={'id':oid,'work_id':f'W{i:04d}','chunk_index':1,'dimension':'emotion',
                'trigger':f'合成の条件{i}','technique':'小さな動作で間を示す','effect':'感情を間接的に伝える',
                'evidence_ids':[s for s,_ in chunk],'anchors':[]}
            chunks[(f'W{i:04d}',1)]=chunk
        chosen=[{'id':'GROUP','label':'小さな動作で間を示す','members':list(observations)}]
        reviewed={'GROUP':list(observations)}
        class SourceModel(Model):
            def ask(self,prompt):
                self.prompts.append(prompt)
                selected=json.loads(prompt.split('EVIDENCE=',1)[1])
                return evidence('説明'*300, selected['observation']['trigger'].endswith('1'))
        store=llm_runtime.CheckpointStore(self.out/'main.sqlite3')
        try:
            m=SourceModel([])
            with redirect_stderr(io.StringIO()):
                supported,calls,cached=llm_style._check_sources(chosen,reviewed,observations,chunks,m,store,self.reporter,2,0,2,4)
            self.assertEqual((supported,calls,cached),({'GROUP':['O00001']},2,0))
            m=Model([])
            with redirect_stderr(io.StringIO()):
                supported,calls,cached=llm_style._check_sources(chosen,reviewed,observations,chunks,m,store,self.reporter,2,0,2,4)
            self.assertEqual((supported,calls,cached),({'GROUP':['O00001']},0,2))
        finally:store.close()

    def test_native_http_long_reason_works_streaming_and_nonstreaming(self):
        bodies=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));bodies.append(body)
                text=json.dumps(evidence('文'*429),ensure_ascii=False)
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream' if body['stream'] else 'application/json')
                self.end_headers()
                if body['stream']:
                    for part in (text[:70],text[70:]):
                        self.wfile.write(('data: '+json.dumps({'choices':[{'delta':{'content':part},'finish_reason':None}]})+'\n\n').encode())
                    self.wfile.write(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
                else:
                    self.wfile.write(json.dumps({'choices':[{'message':{'content':text},'finish_reason':'stop'}]}).encode())
        srv=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
        try:
            for streaming in (False,True):
                m=llm_runtime.HTTPModel('openai-compatible','fixture',f'http://localhost:{srv.server_port}/v1',stream=streaming)
                self.assertEqual(self.invoke(m,binding={'stream':streaming}),{'supported':True,'basis':'rhetorical'})
            self.assertEqual(len(bodies),2)
            for body in bodies:
                self.assertEqual(body['reasoning_effort'],'none')
                self.assertFalse(body['chat_template_kwargs']['enable_thinking'])
        finally:srv.shutdown();srv.server_close();thread.join(2)

if __name__=='__main__':unittest.main()
