"""Host-agent handoff uses production validators; no subagent is fabricated."""
from contextlib import redirect_stderr
from io import StringIO
import json
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
SCRIPTS=Path(__file__).resolve().parents[1]/'skills/pastiche-profile/scripts'
sys.path.insert(0,str(SCRIPTS))
from task_transport import SubagentModel, CallableModel, TasksPending, list_tasks, submit
from llm_runtime import model_jobs
from story_workbench.engine import Engine, Task
from story_workbench.store import Store
from story_workbench.pipeline import analyze
from test_workbench import Model, sample_plan
from test_llm_style import FixtureModel
import llm_style
import workbench
import run


class TaskTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.queue=self.root/'queue';self.model=SubagentModel(self.queue,'fixture')
    def tearDown(self):self.tmp.cleanup()

    def fill(self,fixture):
        for item in list_tasks(self.queue):
            if item['state']=='pending':
                request=json.loads((self.queue/'requests'/f"{item['task_id']}.json").read_text(encoding='utf-8'))
                submit(self.queue,item['task_id'],fixture.ask(request['prompt']))

    def test_entire_ready_batch_is_enqueued_with_bounded_workers(self):
        for workers in (1,3):
            directory=self.root/str(workers);model=SubagentModel(directory)
            with self.assertRaises(TasksPending):
                list(model_jobs(range(7),model,workers,lambda n,m:m.ask(f'prompt {n}')))
            self.assertEqual(len(list_tasks(directory)),7)
            for item in list_tasks(directory):submit(directory,item['task_id'],{'answer':item['task_id']})
            answers=list(model_jobs(range(7),model,workers,lambda n,m:m.ask(f'prompt {n}')))
            self.assertEqual(len(answers),7)

    def test_input_hash_prevents_mixed_or_tampered_responses(self):
        with self.assertRaises(TasksPending):self.model.ask('sensitive synthetic input')
        row=list_tasks(self.queue)[0];submit(self.queue,row['task_id'],{'x':1})
        self.assertEqual(self.model.ask('sensitive synthetic input'),{'x':1})
        p=self.queue/'responses'/f"{row['task_id']}.json";v=json.loads(p.read_text());v['input_sha256']='wrong';p.write_text(json.dumps(v))
        with self.assertRaisesRegex(ValueError,'match'):self.model.ask('sensitive synthetic input')

    def test_request_mutation_and_traversal_are_rejected(self):
        with self.assertRaises(TasksPending):self.model.ask('original')
        row=list_tasks(self.queue)[0];p=self.queue/'requests'/f"{row['task_id']}.json"
        request=json.loads(p.read_text());request['prompt']='modified';p.write_text(json.dumps(request))
        with self.assertRaisesRegex(ValueError,'modified'):submit(self.queue,row['task_id'],{})
        with self.assertRaises(ValueError):submit(self.queue,'../anything',{})

    def test_explicit_replacement_is_required(self):
        with self.assertRaises(TasksPending):self.model.ask('task')
        tid=list_tasks(self.queue)[0]['task_id'];submit(self.queue,tid,{'v':1})
        with self.assertRaises(ValueError):submit(self.queue,tid,{'v':2})
        submit(self.queue,tid,{'v':2},replace=True);self.assertEqual(self.model.ask('task'),{'v':2})

    def test_stage_validates_domain_results_not_just_json(self):
        store=Store(self.root/'db.sqlite3')
        def validate(r):
            if r.get('id')!='allowed':raise ValueError('fabricated ID')
            return r
        try:
            engine=Engine(store,self.model,self.root,workers=2,retries=0,interval=0)
            with self.assertRaises(TasksPending):engine.run('stage',[Task('job','contract',validate)])
            tid=list_tasks(self.queue)[0]['task_id'];submit(self.queue,tid,{'id':'fake'})
            with self.assertRaisesRegex(ValueError,'validation'):engine.run('stage',[Task('job','contract',validate)])
            submit(self.queue,tid,{'id':'allowed'},replace=True)
            self.assertEqual(engine.run('stage',[Task('job','contract',validate)])['job']['id'],'allowed')
        finally:store.close()

    def test_callable_host_adapter_obeys_json_contract(self):
        self.assertEqual(CallableModel(lambda p:{'ok':p},'fake-host').ask('x'),{'ok':'x'})
        with self.assertRaises(ValueError):CallableModel(lambda p:'not an object','bad').ask('x')

    def corpus(self):
        folder=self.root/'corpus';folder.mkdir()
        source=('ミナは駅でレンの話を聞いた。帰る時刻はまだ決まっていない。\n'
                'ミナとレンは新しい知らせを待った。二人は別の方法を話し合った。\n')*4
        for i in range(3):(folder/f'{i}.txt').write_text(source,encoding='utf-8')
        return folder

    def test_full_story_pipeline_resumes_through_host_handoffs(self):
        corpus=self.corpus();out=self.root/'out';fixture=Model()
        for iteration in range(12):
            try:
                result=analyze(corpus,out,self.model,workers=3,retries=0,interval=0,fanout=4,panel_size=3,scope='test')
                break
            except TasksPending:
                audit=json.loads((out/'story_audit.json').read_text())
                self.assertEqual(audit['state'],'waiting_for_tasks')
                self.fill(fixture)
        else:self.fail('handoff failed to make stage progress')
        self.assertEqual(result['state'],'completed');self.assertGreater(iteration,2)
        resumed=analyze(corpus,out,self.model,retries=0,interval=0,fanout=4,panel_size=3,scope='test')
        self.assertEqual(resumed['attempts'],0)

    def test_style_pipeline_handoff_uses_same_contracts(self):
        corpus=self.corpus();out=self.root/'style';fixture=FixtureModel()
        # lexical anchor in synthetic fixture is the station character present in source.
        for _ in range(10):
            try:
                audit=llm_style.analyze(corpus,out,self.model,retries=0,progress_interval=0,llm_workers=3)
                break
            except TasksPending:self.fill(fixture)
        else:self.fail('style handoff failed')
        self.assertGreater(audit['verified_groups'],0)

    def test_cli_returns_75_without_contacting_endpoint(self):
        corpus=self.corpus()
        with patch('llm_runtime.HTTPModel',side_effect=AssertionError('must not create HTTP model')):
            rc=workbench.main(['analyze',str(corpus),'--out',str(self.root/'out'),
                '--executor','subagent','--task-dir',str(self.queue),'--progress-interval','0'])
        self.assertEqual(rc,75);self.assertEqual(len(list_tasks(self.queue)),1)  # identical prompts are content-addressed

    def test_top_level_cli_resumes_without_any_http_model(self):
        corpus=self.corpus();out=self.root/'entry-output';fixture=Model()
        command=[sys.executable,str(SCRIPTS/'run.py'),str(corpus),'--out',str(out),
            '--executor','subagent','--task-dir',str(self.queue),'--agent-profile','fixture',
            '--no-auto-install','--morphology','none','--no-lexical-review',
            '--progress-interval','0','--retries','0','--llm-workers','2']
        first=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(first.returncode,75,first.stderr[-2000:])
        self.assertTrue((out/'style_profile.json').is_file())
        self.fill(fixture)
        resumed=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(resumed.returncode,75,resumed.stderr[-2000:])
        new_tasks=[r for r in list_tasks(self.queue) if r['state']=='pending']
        self.assertTrue(new_tasks)
        prompt=json.loads((self.queue/'requests'/f"{new_tasks[0]['task_id']}.json").read_text(encoding='utf-8'))['prompt']
        self.assertTrue(prompt.startswith('TASK=STYLE_CLUSTER'))

    def test_removed_legacy_modes_are_not_accepted(self):
        with redirect_stderr(StringIO()),self.assertRaises(SystemExit):
            run.main(['/unused','--mode','fast'])

if __name__=='__main__':unittest.main()
