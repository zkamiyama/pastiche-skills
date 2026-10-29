"""Plain prose tasks, Python-owned fields and migration; synthetic fixtures only."""
from __future__ import annotations
from contextlib import contextmanager, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import hashlib
import io
import json
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/pastiche-profile/scripts'))
import llm_runtime as rt
import llm_style as style
import llm_style_core as core
import prose_contracts as pc
from prose_runtime import call_prose, fingerprint
from task_transport import SubagentModel, CallableModel, TasksPending, list_tasks, submit, TEXT_MODE

SUMMARY = '動作の順序を入れ替えて心情の変化を間接的に示します。'
APPLICATION = '新しい場面では説明より先に行動を置いて感情を伝えます。'

def pattern(n=1):
    return {'pattern_id': f'P{n:04d}', 'status': 'model_supported', 'dimension': 'emotion',
            'trigger': '合成の場面条件', 'mechanism': f'合成の描写操作{n}',
            'effect': '感情の変化を間接的に伝える', 'support_work_count': 2}

class TextModel:
    backend = 'fixture'; model = 'fixture'; endpoint = 'fixture'; max_output_tokens = 4096
    def __init__(self, replies=()): self.replies = iter(replies); self.prompts = []
    def ask(self, prompt): raise AssertionError('JSON synthesis must not be used')
    def ask_text(self, prompt):
        self.prompts.append(prompt); value = next(self.replies)
        if isinstance(value, BaseException): raise value
        return value

@contextmanager
def http_fixture(*, finish='stop', text=SUMMARY):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length']))); requests.append(body)
            ollama = self.path == '/api/generate'
            is_json = 'format' in body or 'response_format' in body
            answer = '{"ok":true}' if is_json else text
            self.send_response(200)
            self.send_header('Content-Type', 'application/json' if not body['stream'] else 'text/event-stream')
            self.end_headers()
            if body['stream']:
                if ollama:
                    chunks = [{'response': answer, 'thinking': 'IGNORED_REASONING', 'done': False},
                              {'response': '', 'done': True, 'done_reason': finish}]
                    for chunk in chunks: self.wfile.write((json.dumps(chunk)+'\n').encode())
                else:
                    for delta, reason in [({'reasoning_content':'IGNORED_REASONING'},None),
                                           ({'content':answer},None), ({},finish)]:
                        self.wfile.write(('data: '+json.dumps({'choices':[{'delta':delta,'finish_reason':reason}]})+'\n\n').encode())
                    self.wfile.write(b'data: [DONE]\n\n')
            else:
                wrapped = ({'response': answer, 'done_reason': finish} if ollama else
                           {'choices':[{'message':{'content':answer, 'reasoning_content':'IGNORED_REASONING'},'finish_reason':finish}]})
                self.wfile.write(json.dumps(wrapped).encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    try: yield f'http://127.0.0.1:{server.server_port}', requests
    finally: server.shutdown(); server.server_close(); worker.join(2)

class TextHTTPTests(unittest.TestCase):
    def test_native_text_and_json_streaming_and_nonstreaming(self):
        with http_fixture() as (base, requests):
            for backend, path in [('openai-compatible','/v1'),('ollama','/api/generate')]:
                for stream in (False,True):
                    with self.subTest(backend=backend,stream=stream):
                        m = rt.HTTPModel(backend,'fixture',base+path,stream=stream,max_output_tokens=4096)
                        self.assertEqual(m.ask_text('plain task'), SUMMARY)
                        b = requests[-1]
                        self.assertNotIn('format',b); self.assertNotIn('response_format',b)
                        if backend == 'ollama': self.assertIs(b['think'],False)
                        else:
                            self.assertEqual(b['reasoning_effort'],'none')
                            self.assertIs(b['chat_template_kwargs']['enable_thinking'],False)
                            self.assertEqual(b['max_tokens'],4096)
                            self.assertIn('not JSON',b['messages'][0]['content'])
                        self.assertEqual(m.ask('JSON task'),{'ok':True})
                        self.assertIn('format' if backend=='ollama' else 'response_format',requests[-1])
    def test_finish_length_is_rejected_in_both_transports(self):
        with http_fixture(finish='length') as (base,_):
            for stream in (False,True):
                m=rt.HTTPModel('openai-compatible','fixture',base+'/v1',stream=stream)
                with self.assertRaises(rt.ModelRequestError): m.ask_text('text')
    def test_no_reasoning_text_substitution(self):
        with http_fixture(text='') as (base,_):
            m=rt.HTTPModel('openai-compatible','fixture',base+'/v1',stream=False)
            self.assertEqual(m.ask_text('text'),'')
    def test_four_workers_preserve_text_mode_and_json_defaults(self):
        with http_fixture() as (base,requests):
            m=rt.HTTPModel('openai-compatible','fixture',base+'/v1',max_output_tokens=4096)
            results=list(rt.model_jobs(range(4),m,4,lambda i,worker: worker.ask_text('task')))
            self.assertEqual(len(results),4); self.assertTrue(all(v==SUMMARY for _,v in results))
            self.assertTrue(all('response_format' not in b for b in requests))
            self.assertEqual(m._request_body('other')['response_format'],{'type':'json_object'})
    def test_nonstring_body_is_rejected_without_echo(self):
        with http_fixture(text={'PRIVATE_UNTRUSTED':'x'}) as (base,_):
            m=rt.HTTPModel('openai-compatible','fixture',base+'/v1',stream=False)
            with self.assertRaises(rt.ModelRequestError) as cm: m.ask_text('text')
            self.assertNotIn('PRIVATE_UNTRUSTED',str(cm.exception))

class TextContractTests(unittest.TestCase):
    def test_single_field_prompts_have_no_ids_or_output_schema(self):
        for f in ('summary','writing_use'):
            p=pc.synthesis_text_prompt(pattern(),f)
            self.assertNotIn('P0001',p); self.assertNotIn('"summary":',p); self.assertNotIn('"writing_use":',p)
            self.assertIn('8〜160',p)
    def test_unvalidated_patterns_and_unknown_fields_are_refused(self):
        with self.assertRaises(ValueError): pc.synthesis_text_prompt(dict(pattern(),status='hypothesis'),'summary')
        with self.assertRaises(ValueError): pc.synthesis_text_prompt(pattern(),'foreign')
    def test_original_compact_bounds_and_exact_text_are_retained(self):
        for n in (8,160): self.assertEqual(pc.parse_synthesis_text('文'*n,'summary'),'文'*n)
        self.assertEqual(pc.parse_synthesis_text('  '+SUMMARY+'\n','summary'),SUMMARY)
        for value in ('','文'*7,'文'*161,None,{},[] ):
            with self.subTest(kind=type(value).__name__),self.assertRaises(pc.ProseError):pc.parse_synthesis_text(value,'summary')
    def test_json_and_fenced_content_are_not_silently_unwrapped(self):
        for value in ('{"application":"説明の文章です"}','["説明の文章です"]','```json\n{}\n```'):
            with self.assertRaisesRegex(pc.ProseError,'plain_prose_required'):pc.parse_synthesis_text(value,'summary')
    def test_missing_fields_diagnostic_contains_only_expected_names(self):
        with self.assertRaises(pc.ProseError) as cm: pc.parse_synthesis({'PRIVATE_KEY':'PRIVATE_VALUE'})
        self.assertEqual(cm.exception.diagnostic()['missing_fields'],['summary','writing_use'])
        self.assertNotIn('PRIVATE',str(cm.exception))
    def test_extra_field_diagnostic_does_not_echo_unknown_key(self):
        with self.assertRaises(pc.ProseError) as cm: pc.envelope({'text':SUMMARY,'PRIVATE_KEY':1},{'text'})
        self.assertEqual(cm.exception.diagnostic()['unexpected_field_count'],1)
        self.assertNotIn('PRIVATE',str(cm.exception))

class TextBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.out=Path(self.tmp.name)
        self.reporter=rt.ProgressReporter(self.out,interval=0);self.store=rt.CheckpointStore(self.out/'checkpoint.sqlite3')
    def tearDown(self): self.store.close(); self.tmp.cleanup()
    def synth(self,m,retries=0):
        with redirect_stderr(io.StringIO()):return style._synthesize([pattern()],m,self.store,self.reporter,4,retries)
    def field(self,m,field='summary',retries=2):
        with redirect_stderr(io.StringIO()):
            return call_prose(m,self.reporter,kind='synthesis-text',binding={'pattern':pattern(),'field':field},
                prompt=pc.synthesis_text_prompt(pattern(),field),parser=lambda text:{'text':pc.parse_synthesis_text(text,field)},
                cache_validator=lambda obj:pc.cached_synthesis_text(obj,field),label='SYNTHETIC',retries=retries,
                retry_delay=0,text_output=True)
    def test_python_assembles_fields_and_pattern_reference(self):
        m=TextModel([SUMMARY,APPLICATION]);notes,calls,cached=self.synth(m)
        self.assertEqual((calls,cached),(2,0));self.assertEqual(len(m.prompts),2)
        self.assertEqual(notes,[{'summary':SUMMARY,'writing_use':APPLICATION,'dimension':'emotion','pattern_ids':['P0001']}])
        self.assertEqual(self.synth(TextModel())[1:],(0,1))
    def test_partial_field_success_survives_resume(self):
        m=TextModel([SUMMARY,''] )
        with self.assertRaises(pc.ProseError):self.synth(m)
        next_model=TextModel([APPLICATION]);notes,calls,cached=self.synth(next_model)
        self.assertEqual(calls,1);self.assertEqual(notes[0]['summary'],SUMMARY)
        self.assertTrue(next_model.prompts[0].startswith('TASK=STYLE_WRITING_USE_TEXT'))
    def test_legacy_combined_valid_cache_is_retained(self):
        m=TextModel();value={'summary':SUMMARY,'writing_use':APPLICATION}
        key=fingerprint('synthesis',pattern(),m);folder=self.out/'style_binding_cache';folder.mkdir()
        rt.atomic_json_write(folder/(key+'.json'),{'contract':pc.VERSION,'binding_sha256':key,'value':value})
        self.assertEqual(self.synth(m)[1],0);self.assertEqual(m.prompts,[])
    def test_invalid_legacy_cache_does_not_invent_missing_fields(self):
        m=TextModel([SUMMARY,APPLICATION]);key=fingerprint('synthesis',pattern(),m)
        folder=self.out/'style_binding_cache';folder.mkdir()
        rt.atomic_json_write(folder/(key+'.json'),{'contract':pc.VERSION,'binding_sha256':key,'value':{'application':'not a valid note'}})
        self.assertEqual(self.synth(m)[1],2)
    def test_field_retry_is_bounded_and_not_json_instruction(self):
        m=TextModel(['文'*161,SUMMARY]);self.assertEqual(self.field(m),{'text':SUMMARY})
        self.assertIn('plain prose',m.prompts[1]);self.assertNotIn('文'*161,m.prompts[1])
        diag=next((self.out/'style_binding_diagnostics').glob('*.json'));d=json.loads(diag.read_text())
        self.assertEqual(d['attempts'][0]['field'],'summary');self.assertEqual(d['state'],'completed')
    def test_exhaustion_has_no_fabricated_text_cache(self):
        m=TextModel(['','',''])
        with self.assertRaises(pc.ProseError):self.field(m)
        self.assertEqual(len(m.prompts),3);self.assertFalse(list((self.out/'style_binding_cache').glob('*.json')))
    def test_transport_and_content_errors_share_one_budget(self):
        m=TextModel([rt.ModelRequestError('PRIVATE_RESPONSE'),'文'*161,SUMMARY]);self.field(m)
        self.assertEqual(len(m.prompts),3)
        for path in (self.out/'style_binding_diagnostics').glob('*.json'):self.assertNotIn('PRIVATE_RESPONSE',path.read_text())
    def test_old_json_cache_key_is_unchanged_and_text_is_distinct(self):
        m=TextModel();b={'x':1}
        expected=core.digest('python-owned-task',{'contract':pc.VERSION,'kind':'synthesis','binding':b,'max_output_tokens':4096},m)
        self.assertEqual(fingerprint('synthesis',b,m),expected)
        self.assertNotEqual(fingerprint('synthesis',b,m,text_output=True),expected)
    def test_no_text_support_is_a_clear_nonretryable_error(self):
        class OldModel:
            backend='fixture';model='fixture';endpoint='fixture'
            def ask(self,p):raise AssertionError('no implicit JSON fallback')
        with self.assertRaisesRegex(rt.ModelRequestError,'does not support plain-text'):self.field(OldModel())
    def test_worker_callback_is_restored_after_text_request(self):
        m=TextModel([SUMMARY]);callback=lambda n:None;m.on_chunk=callback;self.field(m);self.assertIs(m.on_chunk,callback)
    def test_text_mode_requires_explicit_cache_validator(self):
        with self.assertRaises(ValueError):
            call_prose(TextModel(),self.reporter,kind='x',binding={},prompt='x',parser=lambda r:r,label='x',retries=0,text_output=True)

class TextHostTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.out=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def test_host_wraps_plain_text_without_changing_model_prompt(self):
        m=SubagentModel(self.out/'queue','fixture');prompt='Write plain prose.'
        with self.assertRaises(TasksPending):m.ask_text(prompt)
        tasks=list_tasks(m.task_dir);self.assertEqual(len(tasks),1);tid=tasks[0]['task_id']
        req=json.loads((m.task_dir/'requests'/f'{tid}.json').read_text())
        self.assertEqual(req['prompt'],prompt);self.assertEqual(req['response_mode'],TEXT_MODE)
        self.assertIn('HOST',req['output_contract']);submit(m.task_dir,tid,{'text':SUMMARY})
        self.assertEqual(m.ask_text(prompt),SUMMARY)
    def test_json_and_text_host_requests_have_different_ids(self):
        m=SubagentModel(self.out/'queue','fixture')
        for method in (m.ask,m.ask_text):
            with self.assertRaises(TasksPending):method('same input')
        tasks=list_tasks(m.task_dir);self.assertEqual(len(tasks),2)
    def test_unknown_or_tampered_host_mode_is_rejected(self):
        m=SubagentModel(self.out/'queue','fixture')
        with self.assertRaises(TasksPending):m.ask_text('text')
        f=next((m.task_dir/'requests').glob('*.json'));v=json.loads(f.read_text());v['response_mode']='other';f.write_text(json.dumps(v))
        with self.assertRaises(ValueError):list_tasks(m.task_dir)
    def test_wrong_host_envelope_is_not_guessed(self):
        m=SubagentModel(self.out/'queue','fixture')
        with self.assertRaises(TasksPending):m.ask_text('text')
        tid=list_tasks(m.task_dir)[0]['task_id'];submit(m.task_dir,tid,{'application':SUMMARY})
        with self.assertRaises(ValueError):m.ask_text('text')
    def test_callable_text_interface_is_separate_from_json(self):
        self.assertEqual(CallableModel(lambda p:SUMMARY,'fixture').ask_text('x'),SUMMARY)
        with self.assertRaises(ValueError):CallableModel(lambda p:{'text':SUMMARY},'fixture').ask_text('x')
    def test_full_synthesis_host_resume(self):
        m=SubagentModel(self.out/'queue','fixture');reporter=rt.ProgressReporter(self.out,interval=0)
        store=rt.CheckpointStore(self.out/'checkpoint.sqlite3')
        try:
            for _ in range(4):
                try:
                    with redirect_stderr(io.StringIO()):notes,_,_=style._synthesize([pattern()],m,store,reporter,1,0)
                    break
                except TasksPending:
                    for task in list_tasks(m.task_dir):
                        if task['state']=='pending':
                            req=json.loads((m.task_dir/'requests'/f"{task['task_id']}.json").read_text())
                            answer=SUMMARY if 'STYLE_SUMMARY_TEXT' in req['prompt'] else APPLICATION
                            submit(m.task_dir,task['task_id'],{'text':answer})
            else:self.fail('host resume never completed')
            self.assertEqual(notes[0]['summary'],SUMMARY);self.assertEqual(notes[0]['writing_use'],APPLICATION)
        finally:store.close()

if __name__=='__main__':unittest.main()
