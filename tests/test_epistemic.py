"""Declared first-order false belief regression tests. No real models or corpus."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
SCRIPTS = Path(__file__).resolve().parents[1]/'skills/pastiche-profile/scripts'
sys.path.insert(0,str(SCRIPTS))
from story_workbench.epistemic import simulate, scene_view, parse_source, SCHEMA
from story_workbench.store import Store, Conflict, segments
from story_workbench.writing import validate_plan, prepare, make_plan
from task_transport import CallableModel
from story_workbench.pipeline import ingest
from story_workbench.protocol import parse_extract
from test_world import extracted, BODY


def false_belief_plan():
    agents=[{'id':'A','name':'アキ','reference_id':None},{'id':'B','name':'ベル','reference_id':None}]
    scenes=[{'id':'move','summary':'ベルが物を移す。アキは不在。','function':'秘密の移動','character_ids':['B'],'depends_on':[]},
            {'id':'return','summary':'アキは以前の場所を探す。','function':'誤信念による行動','character_ids':['A','B'],'depends_on':['move']}]
    return {'premise':'不在中の移動を知らない人物の選択','pattern_ids':[],'characters':agents,'scenes':scenes,
        'plot_tree':[{'id':'root','parent':None,'scene_ids':['move','return']}],
        'epistemic':{'schema':SCHEMA,'facts':[{'id':'F','description':'物の所在','values':['かご','箱'],'initial_world':'かご'}],
            'initial_beliefs':[{'agent':'A','fact':'F','value':'かご','basis':'perception'},
                               {'agent':'B','fact':'F','value':'かご','basis':'perception'}],
            'events':[{'id':'E1','scene_id':'move','order':0,'participants':['B'],'access':{'B':['sight']},
                       'updates':[{'fact':'F','value':'箱'}],'observations':[{'agent':'B','fact':'F','channel':'sight'}],
                       'reader_reveals':['F']},
                      {'id':'E2','scene_id':'return','order':1,'participants':['A','B'],'access':{},
                       'requires':[{'agent':'A','fact':'F','value':'かご','mode':'believes'}]}]}}


def run(plan): return simulate(plan['epistemic'],plan['scenes'],plan['characters'])


class EpistemicTests(unittest.TestCase):
    def test_false_belief_survives_unobserved_world_change(self):
        r=run(false_belief_plan())
        self.assertEqual(r['final_world']['F'],'箱')
        self.assertEqual(r['final_beliefs']['A']['F']['value'],'かご')
        self.assertEqual(r['final_beliefs']['B']['F']['value'],'箱')
        self.assertEqual(r['reader'][0]['after']['F']['value'],'箱')

    def test_false_belief_not_mislabeled_as_knowledge(self):
        p=false_belief_plan();p['epistemic']['events'][1]['requires'][0]['mode']='knows'
        with self.assertRaisesRegex(ValueError,'verified knowledge'):run(p)

    def test_absent_person_cannot_be_given_an_observation(self):
        p=false_belief_plan();p['epistemic']['events'][0]['observations'].append({'agent':'A','fact':'F','channel':'sight'})
        with self.assertRaisesRegex(ValueError,'access'):run(p)

    def test_present_but_asleep_has_no_sight_access(self):
        p=false_belief_plan();p['scenes'][0]['character_ids'].append('A')
        e=p['epistemic']['events'][0];e['participants'].append('A');e['access']['A']=[]
        e['observations'].append({'agent':'A','fact':'F','channel':'sight'})
        with self.assertRaisesRegex(ValueError,'access'):run(p)

    def test_observation_updates_belief_only_after_access(self):
        p=false_belief_plan();e=p['epistemic']['events'][1];e['access']['A']=['sight'];e['observations']=[{'agent':'A','fact':'F','channel':'sight'}]
        self.assertEqual(run(p)['final_beliefs']['A']['F']['value'],'箱')

    def test_cannot_supply_invented_perception_value(self):
        p=false_belief_plan();p['epistemic']['events'][0]['observations'][0]['value']='かご'
        with self.assertRaisesRegex(ValueError,'observation disagrees'):run(p)

    def message(self, accepted):
        p=false_belief_plan();e=p['epistemic']['events'][1]
        e['access']={'A':['hearing']};e['messages']=[{'speaker':'B','recipients':['A'],'accepted_by':accepted,
            'fact':'F','value':'箱','channel':'hearing'}]
        return p

    def test_hearing_report_is_not_automatically_believing(self):
        r=run(self.message([]));self.assertEqual(r['final_beliefs']['A']['F']['value'],'かご')
        self.assertEqual(r['events'][-1]['after']['reports']['A'][0]['value'],'箱')

    def test_accepted_report_revises_belief(self):
        r=run(self.message(['A']));self.assertEqual(r['final_beliefs']['A']['F']['value'],'箱')
        self.assertEqual(r['final_beliefs']['A']['F']['basis'],'communication')

    def test_false_testimony_does_not_change_world(self):
        p=self.message(['A']);p['epistemic']['events'][1]['messages'][0]['value']='かご'
        r=run(p);self.assertEqual(r['final_world']['F'],'箱');self.assertEqual(r['final_beliefs']['A']['F']['value'],'かご')

    def test_message_needs_reception_channel(self):
        p=self.message(['A']);p['epistemic']['events'][1]['access']={}
        with self.assertRaisesRegex(ValueError,'access'):run(p)

    def test_inference_premises_are_private(self):
        p=false_belief_plan();e=p['epistemic']['events'][1]
        e['inferences']=[{'agent':'A','fact':'F','value':'箱','premises':[{'fact':'F','value':'箱'}]}]
        with self.assertRaisesRegex(ValueError,'unavailable'):run(p)

    def test_forgetting_is_not_denial(self):
        p=false_belief_plan();p['epistemic']['events'][1]['forget']=[{'agent':'A','fact':'F'}]
        r=run(p);self.assertIsNone(r['final_beliefs']['A']['F']['value']);self.assertEqual(r['final_world']['F'],'箱')

    def test_unknown_is_not_false_or_world_truth(self):
        p=false_belief_plan();p['epistemic']['initial_beliefs']=[];p['epistemic']['events'][1]['requires']=[{'agent':'A','fact':'F','value':None,'mode':'unknown'}]
        r=run(p);self.assertNotIn('F',r['final_beliefs']['A'])

    def test_reader_follows_presentation_and_does_not_update_minds(self):
        p=false_belief_plan();p['scenes'].reverse()
        r=run(p);self.assertEqual(r['reader'][0]['before'],{});self.assertEqual(r['reader'][0]['after'],{})
        self.assertEqual(r['reader'][1]['after']['F']['value'],'箱')
        self.assertEqual(r['final_beliefs']['A']['F']['value'],'かご')

    def test_private_context_omits_reality_other_minds_and_future(self):
        p=false_belief_plan();v=scene_view(p,'return','A');render=json.dumps(v,ensure_ascii=False)
        self.assertNotIn('箱',render);self.assertNotIn('final_world',render);self.assertNotIn('truth',render)
        self.assertEqual(v['events'][0]['beliefs_before']['F']['value'],'かご')
        self.assertIn('world',scene_view(p,'return')['events'][0]['before'])

    def test_every_scene_requires_timeline_coverage(self):
        p=false_belief_plan();p['epistemic']['events'].pop()
        with self.assertRaisesRegex(ValueError,'every scene'):run(p)

    def test_duplicate_chronology_is_rejected(self):
        p=false_belief_plan();p['epistemic']['events'][1]['order']=0
        with self.assertRaises(ValueError):run(p)

    def test_unknown_agent_fact_and_value_are_rejected(self):
        for key,value in [('agent','MISSING'),('fact','MISSING'),('value','MISSING')]:
            p=false_belief_plan();p['epistemic']['events'][1]['requires'][0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):run(p)

    def test_legacy_plan_rejected_not_silently_downgraded(self):
        p=false_belief_plan();p['facts']=[]
        with self.assertRaisesRegex(ValueError,'legacy'):validate_plan(p,{'mode':'original','characters':{'entities':[]},'plot_patterns':[]})


class SourcePerspectiveTests(unittest.TestCase):
    def test_source_holder_is_not_world_truth_and_can_be_revoked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'fiction.txt';source.write_text(BODY,encoding='utf-8')
            s=Store(root/'character_graph.sqlite3')
            try:
                src,body=s.register_source(source,'novel:one');units=segments(body);raw=extracted(units)
                raw['perspectives']=[{'holder':'c','proposition':{'subject':'s','predicate':'requires','value':'潮石は不要だと考える'},
                    'stance':'affirmed','basis':'inference','speaker':None,'event_id':'e','evidence_ids':[units[2]['id']]}]
                parsed=parse_extract(raw,units);ingest(s,src,units,parsed,0,len(body))
                perspectives=s.perspective_context(src['scope']);self.assertEqual(len(perspectives['claims']),1)
                self.assertTrue(all(c['kind']!='perspective' for c in s.world_context(src['scope'])['claims']))
                c=perspectives['claims'][0];self.assertEqual(c['basis'],'inference')
                s.review(c['id'],'revoke',1,'human','incorrect attribution')
                with self.assertRaises(Conflict):s.validate_context_snapshot({'schema':'pastiche.writer.context.v4','scope':src['scope'],'perspectives':perspectives})
                self.assertEqual(s.perspective_context(src['scope'])['claims'],[])
            finally:s.close()

    def test_long_copied_source_is_rejected(self):
        units=segments(BODY);raw=extracted(units)
        copied=''.join(u['text'] for u in units)
        raw['perspectives']=[{'holder':'c','proposition':{'subject':'s','predicate':'mentions','value':copied},
            'stance':'affirmed','basis':'inference','speaker':None,'event_id':'e','evidence_ids':[units[0]['id']]}]
        with self.assertRaises(ValueError):parse_extract(raw,units)

    def test_semantic_mismatch_is_reported_not_mislabeled_as_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);context=prepare(out,'fixture')
            def answer(prompt):
                if prompt.startswith('TASK=WRITE_PLAN'):
                    return false_belief_plan()
                return {'issues':[{'kind':'undeclared_premise','scene_id':'return',
                    'event_ids':['E2'],'description':'台詞に必要な認識が宣言されていない',
                    'repair':'人物が持つ信念だけに基づいて判断させる'}]}
            make_plan(out,context,'新しい短編を書く',CallableModel(answer,'fixture'),retries=0,interval=0)
            audit=json.loads((out/'writing/perspective_plan_audit.json').read_text(encoding='utf-8'))
            self.assertEqual(audit['status'],'needs_revision')
            self.assertEqual(len(audit['issues']),1)

    def test_missing_or_fabricated_perspective_is_not_accepted(self):
        units=segments(BODY);raw=extracted(units);raw.pop('perspectives')
        with self.assertRaisesRegex(ValueError,'perspectives'):parse_extract(raw,units)
        raw['perspectives']=[{'holder':'invented','proposition':{'subject':'s','predicate':'x','value':'y'}}]
        with self.assertRaises(ValueError):parse_extract(raw,units)

if __name__=='__main__': unittest.main()
