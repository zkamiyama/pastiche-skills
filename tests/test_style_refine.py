"""Synthetic refinement contracts and orchestration; no production corpus/model."""
from __future__ import annotations
from copy import deepcopy
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/pastiche-profile/scripts'
sys.path.insert(0, str(SCRIPTS))
import pastiche
import workbench
from task_transport import CallableModel, SubagentModel, TasksPending, list_tasks, submit
from story_workbench.style_refine import refine
from story_workbench import refine_contracts as contracts
from story_workbench.store import Store, Conflict, atomic_json, segments
from story_workbench.writing import prepare
from test_epistemic import false_belief_plan

BEFORE = 'アキは不安で、ためらっていた。'
AFTER = 'アキはためらっていた。'
BODY = ('アキは、かごの前で足を止めた。\n' + BEFORE + '\n'
        '「まだ、ここにあるはず」とアキは言った。\n'
        '返事はなかった。かごの口に目を落としたまま、アキは言葉を続けなかった。\n'
        '廊下は静かだった。誰もそれ以上の説明をしなかった。\n')


def profile():
    return {'schema': 'pastiche.qualitative.v1', 'patterns': [{
        'pattern_id': 'P0001', 'dimension': 'emotion', 'status': 'model_supported',
        'trigger': '人物のためらいが示される場面',
        'mechanism': 'すでに表現された心理を地の文で重ねて説明しない',
        'effect': '台詞と間による情感を保つ', 'support_work_count': 2,
        'validation_passes': 2, 'evidence_ids': ['W0001:S0001', 'W0002:S0001'], 'anchors': []}]}


class Model:
    backend, model, endpoint = 'fixture', 'synthetic-review', 'fixture'
    def __init__(self, mode='accept', hook=None):
        self.mode, self.hook, self.calls = mode, hook, []

    def ask(self, prompt):
        self.calls.append(prompt)
        if self.hook:
            self.hook(prompt)
        if prompt.startswith('TASK=DRAFT_AUDIT'):
            units = json.loads(prompt.split('\nDRAFT=', 1)[1])
            body = '\n'.join(u['text'] for u in units)
            bad = self.mode == 'bad_original' or (self.mode == 'bad_candidate' and BEFORE not in body)
            return {'issues': [{'kind': 'knowledge', 'description': '宣言されていない情報を利用している',
                'evidence_ids': [units[0]['id']], 'claim_id': None,
                'repair': '認識状態を守るように計画を再確認する'}] if bad else []}
        data = json.loads(prompt.split('\nDATA=', 1)[1])
        if prompt.startswith('TASK=STYLE_REVIEW'):
            body = '\n'.join(u['text'] for u in data['segments'])
            status = 'underused' if BEFORE in body else 'aligned'
            if self.mode == 'not_applicable': status = 'not_applicable'
            if self.mode == 'uncertain_review': status = 'uncertain'
            if self.mode == 'no_improvement': status = 'underused'
            unit = next((u for u in data['segments'] if BEFORE in u['text']), data['segments'][0])
            return {'draft_sha256': data['draft_sha256'], 'assessments': [
                {'pattern_id': p['pattern_id'], 'status': status, 'evidence_ids': [unit['id']],
                 'reason': '同じ心理説明の重複を場面に即して検討する',
                 'instruction': '不安の重複説明を減らし、人物の信念は変えない' if status == 'underused' else ''}
                for p in data['patterns']]}
        if prompt.startswith('TASK=STYLE_REFINE'):
            unit = next(u for u in data['segments'] if BEFORE in u['text'])
            return {'draft_sha256': data['draft_sha256'], 'edits': [] if self.mode == 'no_edit' else [
                {'segment_id': unit['id'], 'before': BEFORE, 'after': AFTER,
                 'pattern_ids': ['P0001'], 'reason': '新しい動作を足さず重複説明だけを減らす'}]}
        if prompt.startswith('TASK=STYLE_COMPARE'):
            a = '\n'.join(u['text'] for u in data['A']['segments'])
            label = 'B' if BEFORE in a else 'A'
            preference = 'A' if self.mode == 'order_bias' else 'uncertain' if self.mode == 'uncertain_compare' else label
            quality = ('B' if label == 'A' else 'A') if self.mode == 'worse_quality' else 'tie'
            return {'pair_sha256': data['pair_sha256'], 'style_preference': preference,
                'quality_preference': quality, 'meaning_preserved': self.mode != 'changed_meaning',
                'pattern_ids': ['P0001'], 'evidence_a': [data['A']['segments'][0]['id']],
                'evidence_b': [data['B']['segments'][0]['id']], 'reason': '文体への適合と意味の維持を別々に比較した'}
        raise AssertionError('unexpected fixture task')


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.out = self.root / 'profile'
        self.out.mkdir()
        self.context = self.out / 'writer_context.json'
        prepare(self.out, 'fixture')
        self.plan = self.root / 'plan.json'; atomic_json(self.plan, false_belief_plan())
        self.patterns = self.out / 'qualitative_patterns.json'; atomic_json(self.patterns, profile())
        self.draft = self.root / 'draft.txt'; self.draft.write_text(BODY, encoding='utf-8')

    def tearDown(self): self.tmp.cleanup()

    def run_refine(self, model=None, **options):
        opts = {'scene_id': 'return', 'character_id': 'A', 'retries': 0, 'interval': 0}
        opts.update(options)
        with redirect_stderr(io.StringIO()):
            return refine(self.out, self.draft, self.plan, self.context, self.patterns, model or Model(), **opts)

    def directory(self, result): return self.out / 'writing/refine' / result['run_id']
    def selected(self, result): return (self.directory(result) / 'selected.txt').read_text(encoding='utf-8')


class RefineFlowTests(Fixture):
    def test_acceptance_is_bound_to_hash_and_never_overwrites_original(self):
        raw = self.draft.read_bytes(); model = Model()
        result = self.run_refine(model)
        self.assertEqual(result['selection'], 'candidate')
        self.assertEqual(result['accepted_rounds'], 1)
        self.assertEqual(self.draft.read_bytes(), raw)
        self.assertEqual(self.selected(result), BODY.replace(BEFORE, AFTER))
        self.assertEqual(result['selected_sha256'], contracts.digest(self.selected(result)))
        folder = self.directory(result)
        for name in ('review-before.json','review-after.json','edits.json','semantic-before.json',
                     'semantic-after.json','candidate.json','surface-after.json','comparisons.json','changes.diff'):
            self.assertTrue((folder/'round-1'/name).is_file(), name)
        state = json.loads((self.out/'writing/refine_status.json').read_text())
        self.assertEqual(state['state'], 'completed')
        self.assertEqual(state['selected_sha256'], result['selected_sha256'])
        self.assertEqual(len(model.calls), 7)

    def test_second_round_rejection_keeps_first_accepted_revision(self):
        second_before, second_after = '彼女は不安を何度も感じていた。', '彼女は黙っていた。'
        body = BODY + second_before + '\n'
        self.draft.write_text(body, encoding='utf-8')
        raw = profile(); p2 = deepcopy(raw['patterns'][0]); p2['pattern_id'] = 'P0002'
        raw['patterns'].append(p2); atomic_json(self.patterns, raw)
        class TwoRounds(Model):
            def ask(self, prompt):
                if not prompt.startswith('TASK=STYLE_'):
                    return super().ask(prompt)
                self.calls.append(prompt)
                data = json.loads(prompt.split('\nDATA=', 1)[1])
                targets = [('P0001', BEFORE, AFTER), ('P0002', second_before, second_after)]
                if prompt.startswith('TASK=STYLE_REVIEW'):
                    assessments = []
                    for pid, before, after in targets:
                        unit = next((u for u in data['segments'] if before in u['text']), None)
                        assessments.append({'pattern_id': pid, 'status': 'underused' if unit else 'aligned',
                            'evidence_ids': [unit['id'] if unit else data['segments'][0]['id']],
                            'reason': '重複する心理説明を確認する', 'instruction': '重複部分を減らす' if unit else ''})
                    return {'draft_sha256': data['draft_sha256'], 'assessments': assessments}
                if prompt.startswith('TASK=STYLE_REFINE'):
                    for pid, before, after in targets:
                        unit = next((u for u in data['segments'] if before in u['text']), None)
                        if unit:
                            return {'draft_sha256': data['draft_sha256'], 'edits': [{'segment_id': unit['id'],
                                'before': before, 'after': after, 'pattern_ids': [pid], 'reason': '重複部分を減らす'}]}
                a = '\n'.join(u['text'] for u in data['A']['segments'])
                b = '\n'.join(u['text'] for u in data['B']['segments'])
                label = 'A' if len(a) < len(b) else 'B'
                is_second = second_after in a or second_after in b
                return {'pair_sha256': data['pair_sha256'], 'style_preference': 'uncertain' if is_second else label,
                    'quality_preference': 'tie', 'meaning_preserved': True, 'pattern_ids': ['P0001','P0002'],
                    'evidence_a': [data['A']['segments'][0]['id']], 'evidence_b': [data['B']['segments'][0]['id']],
                    'reason': '二回目は改善したか判断できない'}
        result = self.run_refine(TwoRounds(), max_rounds=2)
        self.assertEqual(result['accepted_rounds'], 1)
        self.assertEqual(result['reason'], 'comparison_inconclusive_or_regression')
        self.assertEqual(self.selected(result), body.replace(BEFORE, AFTER))
        self.assertEqual([r['accepted'] for r in result['rounds']], [True, False])

    def test_no_edits_when_diagnosis_is_inapplicable_or_uncertain(self):
        for mode in ('not_applicable','uncertain_review'):
            model = Model(mode); model.model = mode
            result = self.run_refine(model)
            self.assertEqual(result['selection'], 'original')
            self.assertEqual(len(model.calls), 1)
            self.assertEqual(self.selected(result), BODY)

    def test_no_supported_patterns_calls_no_model(self):
        p = profile(); p['patterns'][0]['status'] = 'hypothesis'; atomic_json(self.patterns, p)
        model = Model(); result = self.run_refine(model)
        self.assertEqual(result['reason'], 'no_supported_patterns')
        self.assertEqual(model.calls, [])

    def test_failing_any_gate_preserves_original(self):
        for mode, reason in [('bad_original','needs_structural_revision'), ('bad_candidate','candidate_semantic_issue'),
             ('no_edit','no_change_proposed'), ('no_improvement','profile_improvement_not_established'),
             ('order_bias','comparison_inconclusive_or_regression'), ('worse_quality','comparison_inconclusive_or_regression'),
             ('changed_meaning','comparison_inconclusive_or_regression'), ('uncertain_compare','comparison_inconclusive_or_regression')]:
            model = Model(mode); model.model = mode
            with self.subTest(mode=mode):
                result = self.run_refine(model)
                self.assertEqual(result['selection'],'original')
                self.assertEqual(result['reason'],reason)
                self.assertEqual(self.selected(result),BODY)

    def test_character_tasks_do_not_receive_director_truth_or_edit_labels(self):
        model = Model(); self.run_refine(model)
        for prompt in model.calls:
            if prompt.startswith('TASK=STYLE_'):
                data=json.loads(prompt.split('\nDATA=',1)[1])
                self.assertEqual(data['view']['audience'],'character')
                self.assertNotIn('箱', json.dumps(data,ensure_ascii=False))
                self.assertNotIn('plan',data);self.assertNotIn('context',data)
                if prompt.startswith('TASK=STYLE_COMPARE'):
                    self.assertEqual(set(data),{'pair_sha256','patterns','view','A','B'})
                    self.assertNotIn('diagnosis',data)
        self.assertTrue(any('箱' in p for p in model.calls if p.startswith('TASK=DRAFT_AUDIT')))

    def test_director_requires_explicit_selection_and_private_viewpoint_must_be_present(self):
        for opts in ({'character_id':None}, {'director':True}, {'character_id':'missing'}, {'scene_id':'missing'}):
            with self.subTest(opts=opts),self.assertRaises(ValueError): self.run_refine(**opts)
        self.assertEqual(self.run_refine(character_id=None,director=True)['selection'],'candidate')

    def test_multi_event_private_scenes_require_explicit_event_excerpt(self):
        p=false_belief_plan()
        p['epistemic']['events'].append({'id':'E3','scene_id':'return','order':2,'participants':['A'],'access':{}})
        atomic_json(self.plan,p)
        with self.assertRaisesRegex(ValueError,'multi-event'): self.run_refine()
        self.assertEqual(self.run_refine(event_id='E2')['selection'],'candidate')
        with self.assertRaises(ValueError):self.run_refine(event_id='E1')

    def test_surface_numbers_are_recorded_not_optimized_as_quotas(self):
        path=self.root/'style.json'
        atomic_json(path,pastiche.aggregate([pastiche.measure('極端に短い。')],[],{},'none'))
        result=self.run_refine(style_profile=path)
        stats=json.loads((self.directory(result)/'surface-selected.json').read_text())
        self.assertTrue(stats['comparison']['draft_too_short_for_stable_comparison'])
        self.assertEqual(result['selection'],'candidate')

    def test_lexical_profile_uses_existing_offline_counter(self):
        path=self.root/'lexical.json'
        atomic_json(path,{'schema':'pastiche.lexical.v1','total_characters':100,'symbols':[],
            'symbol_runs':[],'variant_pairs':[],'sentence_endings':[]})
        result=self.run_refine(lexical_profile=path)
        stats=json.loads((self.directory(result)/'surface-selected.json').read_text())
        self.assertEqual(stats['lexical']['schema'],'pastiche.lexical-evaluation.v1')

    def test_identical_resume_reuses_validated_cache(self):
        first=self.run_refine()
        model=Model(); second=self.run_refine(model)
        self.assertEqual(first,second);self.assertEqual(model.calls,[])

    def test_max_two_rounds_stops_when_all_patterns_aligned(self):
        model=Model();result=self.run_refine(model,max_rounds=2)
        self.assertEqual(result['accepted_rounds'],1)
        self.assertEqual(result['reason'],'no_actionable_diagnosis')
        self.assertEqual(len(model.calls),7)
        for n in (0,3,True):
            with self.assertRaises(ValueError):self.run_refine(max_rounds=n)

    def test_separate_judge_only_writer_performs_refine(self):
        writer=Model();judge=Model();judge.model='independent-config'
        result=self.run_refine(writer,judge_model=judge)
        self.assertFalse(result['judge_uses_same_configuration'])
        self.assertEqual(len(writer.calls),1)
        self.assertTrue(writer.calls[0].startswith('TASK=STYLE_REFINE'))
        self.assertEqual(len(judge.calls),6)

    def test_http_executor_roundtrip_uses_identical_refine_contracts(self):
        import threading
        from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
        from llm_runtime import HTTPModel
        fixture = Model()
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                result = fixture.ask(payload['messages'][-1]['content'])
                response = json.dumps({'choices':[{'message':{'content':json.dumps(result,ensure_ascii=False)},
                                                  'finish_reason':'stop'}]}).encode()
                self.send_response(200); self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(response))); self.end_headers()
                self.wfile.write(response)
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            model = HTTPModel('openai-compatible','synthetic-http',
                f'http://127.0.0.1:{server.server_port}/v1/chat/completions',timeout=10,stream=False)
            result = self.run_refine(model)
            self.assertEqual(result['accepted_rounds'],1)
            self.assertEqual(self.selected(result),BODY.replace(BEFORE,AFTER))
            self.assertEqual(len(fixture.calls),7)
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)

    def test_subagent_barriers_resume_to_the_same_selected_text(self):
        directory=self.root/'private-tasks'; model=SubagentModel(directory,'fixture')
        fixture=Model(); rounds=0
        while True:
            try:
                result=self.run_refine(model,workers=2);break
            except TasksPending:
                state=json.loads((self.out/'writing/refine_status.json').read_text())
                self.assertEqual(state['state'],'waiting_for_tasks')
                for task in list_tasks(directory):
                    if task['state']=='pending':
                        request=json.loads((directory/'requests'/f"{task['task_id']}.json").read_text())
                        submit(directory,task['task_id'],fixture.ask(request['prompt']))
                rounds+=1
                self.assertLess(rounds,10)
        self.assertEqual(self.selected(result),BODY.replace(BEFORE,AFTER))
        self.assertEqual(result['accepted_rounds'],1)
        self.assertEqual(len(list_tasks(directory)),7)

    def test_change_to_any_input_during_inference_aborts_publication(self):
        for path in (self.draft,self.patterns,self.plan,self.context):
            original=path.read_bytes()
            def hook(prompt):
                if prompt.startswith('TASK=STYLE_REFINE'):
                    path.write_bytes(original+b' ')
            model=Model(hook=hook);model.model=path.name
            with self.subTest(path=path),self.assertRaises(Conflict):self.run_refine(model)
            state=json.loads((self.out/'writing/refine_status.json').read_text())
            self.assertEqual(state['state'],'failed')
            self.assertFalse((self.out/'writing/refine'/state['run_id']/'selected.txt').exists())
            path.write_bytes(original)

    def test_human_revocation_during_refine_is_honored(self):
        source=self.root/'source.txt';source.write_text('アキは短い言葉で答える。',encoding='utf-8')
        store=Store(self.out/'character_graph.sqlite3')
        try:
            src,body=store.register_source(source,'fixture');units=segments(body)
            actor=store.entity(src['id'],'アキ','fixture')
            evidence=store.evidence_for(src,units,[units[0]['id']],len(body))
            cid=store.add_claim({'subject':actor,'object':None,'kind':'voice','predicate':'speaks',
                'value':'短い応答を選ぶ','scope':'fixture','evidence_ids':[evidence]})
        finally:store.close()
        prepare(self.out,'fixture')
        def hook(prompt):
            if prompt.startswith('TASK=STYLE_REFINE'):
                s=Store(self.out/'character_graph.sqlite3')
                try:s.review(cid,'revoke',1,'human','do not use')
                finally:s.close()
        with self.assertRaises(Conflict):self.run_refine(Model(hook=hook))

    def test_fabricated_reference_is_failure_not_successful_empty_diagnosis(self):
        class Broken(Model):
            def ask(self,prompt):
                answer=super().ask(prompt)
                if prompt.startswith('TASK=STYLE_REVIEW'):
                    answer['assessments'][0]['evidence_ids']=['invented']
                return answer
        with self.assertRaises(ValueError):self.run_refine(Broken())
        self.assertEqual(json.loads((self.out/'writing/refine_status.json').read_text())['state'],'failed')
        self.assertEqual(self.draft.read_text(encoding='utf-8'),BODY)

    def test_invalid_profile_or_oversized_draft_fails_without_model_call(self):
        model=Model()
        self.draft.write_text('x'*16001,encoding='utf-8')
        with self.assertRaises(ValueError):self.run_refine(model)
        self.assertEqual(model.calls,[])

    def test_status_output_cannot_overwrite_input(self):
        self.draft=self.out/'writing/refine_status.json';self.draft.parent.mkdir(exist_ok=True)
        self.draft.write_text(BODY,encoding='utf-8')
        with self.assertRaises(ValueError):self.run_refine()
        self.assertEqual(self.draft.read_text(encoding='utf-8'),BODY)

    def test_cli_forwards_options_and_subagent_waits(self):
        args=['refine','--out',str(self.out),'--draft',str(self.draft),'--plan',str(self.plan),
              '--context',str(self.context),'--scene-id','return','--character-id','A',
              '--executor','subagent','--task-dir',str(self.root/'tasks')]
        with redirect_stderr(io.StringIO()),patch('builtins.print'):
            self.assertEqual(workbench.main(args),75)
        self.assertEqual(len(list_tasks(self.root/'tasks')),1)


class RefineContractTests(unittest.TestCase):
    def setUp(self):
        self.patterns=contracts.catalog(profile())
        from story_workbench.style_refine import review_task
        self.diagnosis=Model().ask(review_task(BODY,self.patterns,{},{}).prompt)
        self.diagnosis=contracts.review(self.diagnosis,BODY,self.patterns)
        self.patch={'draft_sha256':contracts.digest(BODY),'edits':[{'segment_id':'S000002',
                    'before':BEFORE,'after':AFTER,'pattern_ids':['P0001'],'reason':'重複を減らす'}]}

    def test_unknown_and_duplicate_patterns_and_unproven_support_are_rejected(self):
        for edit in ('duplicate','unsupported','long_anchor'):
            p=profile()
            if edit=='duplicate':p['patterns']*=2
            elif edit=='unsupported':p['patterns'][0]['validation_passes']=1
            else:p['patterns'][0]['anchors']=['x'*13]
            with self.subTest(edit=edit),self.assertRaises(ValueError):contracts.catalog(p)
        with self.assertRaises(ValueError):contracts.catalog(profile(),['missing'])

    def test_review_requires_all_patterns_and_real_evidence(self):
        for change in ('missing','duplicate','wrong_hash','not_actionable'):
            r=deepcopy(self.diagnosis)
            if change=='missing':r['assessments']=[]
            elif change=='duplicate':r['assessments']*=2
            elif change=='wrong_hash':r['draft_sha256']='bad'
            else:r['assessments'][0]['status']='aligned'
            with self.subTest(change=change),self.assertRaises(ValueError):contracts.review(r,BODY,self.patterns)

    def test_patch_hash_target_overlap_and_diagnosis_are_enforced(self):
        for change in ('hash','target','overlap','segment','pattern','no_op','budget'):
            p=deepcopy(self.patch);e=p['edits'][0]
            if change=='hash':p['draft_sha256']='old'
            elif change=='target':e['before']='absent'
            elif change=='overlap':p['edits']*=2
            elif change=='segment':e['segment_id']='S000001'
            elif change=='pattern':e['pattern_ids']=['missing']
            elif change=='no_op':e['after']=e['before']
            else:e['after']='x'*2000
            with self.subTest(change=change),self.assertRaises(ValueError):contracts.edits(p,BODY,self.diagnosis)

    def test_exact_patching_preserves_all_untouched_characters(self):
        original=BODY.encode('utf-8')
        clean=contracts.edits(self.patch,BODY,self.diagnosis)
        self.assertEqual(clean,contracts.edits(clean,BODY,self.diagnosis))
        self.assertEqual(contracts.apply_edits(BODY,clean),BODY.replace(BEFORE,AFTER))
        self.assertEqual(BODY.encode('utf-8'),original)
        with self.assertRaises(ValueError):contracts.apply_edits(BODY+'x',clean)

    def test_inapplicable_is_not_an_excuse_to_dismiss_an_unfixed_issue(self):
        revised=deepcopy(self.diagnosis);revised['assessments'][0]['status']='not_applicable'
        self.assertFalse(contracts.improves(self.diagnosis,revised))

    def test_acceptance_rule_exhaustive_preferences(self):
        from itertools import product
        labels=['A','B'];n=0
        for style1,style2,quality1,quality2,meaning1,meaning2 in product(
                ('A','B','tie','uncertain'),('A','B','tie','uncertain'),
                ('A','B','tie','uncertain'),('A','B','tie','uncertain'),(True,False),(True,False)):
            rs=[{'style_preference':style1,'quality_preference':quality1,'meaning_preserved':meaning1},
                {'style_preference':style2,'quality_preference':quality2,'meaning_preserved':meaning2}]
            expected=(style1=='A' and style2=='B' and quality1 in {'A','tie'} and quality2 in {'B','tie'} and meaning1 and meaning2)
            self.assertEqual(contracts.accept_pair(rs,labels),expected);n+=1
        self.assertEqual(n,1024)

    def test_acceptance_requires_counterbalanced_order_and_boolean_meaning(self):
        row = {'style_preference':'A','quality_preference':'tie','meaning_preserved':True}
        self.assertFalse(contracts.accept_pair([row,row],['A','A']))
        other = dict(row,style_preference='B',meaning_preserved='true')
        self.assertFalse(contracts.accept_pair([row,other],['A','B']))

    def test_comparison_requires_pair_binding_and_citations(self):
        from story_workbench.style_refine import compare_task
        other=BODY.replace(BEFORE,AFTER)
        task=compare_task(BODY,other,self.patterns,{}, {}, {}, 'pair')
        raw=Model().ask(task.prompt)
        self.assertEqual(task.validate(raw),raw)
        raw['evidence_a']=['made-up']
        with self.assertRaises(ValueError):task.validate(raw)
