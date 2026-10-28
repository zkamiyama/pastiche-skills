"""Synthetic world graph tests. Never fetch real sites or use private model IDs."""
from __future__ import annotations
import copy
import http.client
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/pastiche-profile/scripts'
sys.path.insert(0, str(SCRIPTS))
from story_workbench.store import Store, Conflict, segments, entity_type, uid
from story_workbench.protocol import parse_extract, extract_prompt
from story_workbench.pipeline import analyze
from story_workbench.world import conflict_candidates, export_world
from story_workbench.writing import prepare, validate_plan, make_plan, audit_draft
from story_workbench.research import research
from story_workbench.server import make_server
from test_workbench import Model as StoryModel, sample_plan
import workbench

BODY = ('ミナとレンは白灯市の東湾を見た。\n'
        'ミナは航路組合に所属し、航路組合は白灯市の港を管理している。\n'
        '浮航術には充電した潮石が必要だ。灯台内では予備電源を使える。\n'
        'ミナは知らせを受け取った。レンは次の計画を考えた。\n') * 3


def extracted(chunk):
    refs = [u['id'] for u in chunk]
    def evidence(word):
        return [next(u['id'] for u in chunk if word in u['text'])]
    worlds = [('p', '白灯市', 'place'), ('o', '航路組合', 'organization'),
              ('s', '浮航術', 'system'), ('r', '潮石', 'artifact')]
    return {
        'characters': [{'id': 'c', 'name': 'ミナ', 'aliases': [], 'identity': 'explicit', 'evidence_ids': evidence('ミナ')}],
        'events': [{'id': 'e', 'summary': '人物は知らせから次の方針を考える', 'function': '行動方針の更新',
                    'before': '知らせを待っている', 'after': '次の計画を立てる', 'story_time': None,
                    'actors': ['c'], 'world_entities': ['p', 'o'], 'evidence_ids': evidence('知らせ')}],
        'claims': [{'subject': 'c', 'object': None, 'kind': 'goal', 'predicate': 'plans',
                    'value': '知らせに応じて計画を改める', 'epistemic': 'inferred', 'evidence_ids': evidence('知らせ')}],
        'links': [],
        'world_entities': [{'id': i, 'name': n, 'entity_type': t, 'identity': 'explicit', 'aliases': [],
                             'evidence_ids': evidence(n)} for i, n, t in worlds],
        'world_claims': [
            {'subject': 'c', 'object': 'o', 'kind': 'affiliation', 'predicate': 'member_of',
             'value': '組合の構成員である', 'epistemic': 'explicit', 'modality': 'descriptive', 'polarity': 'affirmed',
             'conditions': [], 'exceptions': [], 'story_time': None, 'evidence_ids': evidence('所属')},
            {'subject': 's', 'object': 'r', 'kind': 'magic', 'predicate': 'requires',
             'value': '作動には蓄えられた資源が必要', 'epistemic': 'explicit', 'modality': 'capability', 'polarity': 'affirmed',
             'conditions': ['潮石が充電されている'], 'exceptions': ['灯台内では予備電源を利用できる'],
             'story_time': None, 'evidence_ids': evidence('浮航術')}
        ]}


class WorldModel(StoryModel):
    def ask(self, prompt):
        if prompt.startswith(('TASK=STORY_EXTRACT', 'TASK=CHARACTER_WEB', 'TASK=WORLD_WEB')):
            self.calls.append(prompt)
            lines = re.findall(r'^\[(S\d+)\] (.*)$', prompt.split('DATA:\n', 1)[1], re.M)
            return extracted([{'id': i, 'text': t} for i, t in lines])
        return super().ask(prompt)


class WorldFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.corpus = self.root/'corpus'; self.corpus.mkdir()
        self.out = self.root/'out'
        for i in range(3): (self.corpus/f'fiction{i}.txt').write_text(BODY, encoding='utf-8')
        self.db = self.out/'character_graph.sqlite3'

    def tearDown(self): self.tmp.cleanup()

    def fixture(self):
        s = Store(self.db)
        src, body = s.register_source(self.corpus/'fiction0.txt', 'original:world')
        units = segments(body)
        q = s.evidence_for(src, units, [units[2]['id']], len(body))
        a = s.entity(src['id'], 'ミナ', src['scope'])
        place = s.entity(src['id'], '白灯市', src['scope'], entity_type='place')
        system = s.entity(src['id'], '浮航術', src['scope'], entity_type='system')
        resource = s.entity(src['id'], '潮石', src['scope'], entity_type='artifact')
        c = s.add_claim({'domain': 'world', 'subject': system, 'object': resource,
                        'kind': 'magic', 'predicate': 'requires', 'value': '充電された資源を必要とする',
                        'scope': src['scope'], 'epistemic': 'explicit', 'modality': 'capability', 'polarity': 'affirmed',
                        'conditions': ['潮石が充電されている'], 'exceptions': ['灯台内では予備電源を使用できる'],
                        'evidence_ids': [q], 'observed_at': .4})
        return s, src, q, a, place, system, resource, c


class WorldExtractionTests(WorldFixture):
    def test_contract_and_full_pipeline(self):
        self.assertIn('Both arrays are REQUIRED', extract_prompt(segments(BODY)))
        model = WorldModel()
        audit = analyze(self.corpus, self.out, model, scope='original:world', workers=2,
                        chunk_chars=3800, retries=0, interval=0, fanout=4)
        self.assertEqual(audit['scanned_chunks'], audit['full_chunks'])
        self.assertEqual(audit['world_entities'], 12)  # 4 per work, not auto-merged across works
        self.assertEqual(audit['world_claims'], 6)
        world = json.loads((self.out/'world_graph.json').read_text(encoding='utf-8'))
        self.assertEqual(world['schema'], 'pastiche.world.graph.v1')
        self.assertTrue(all(c['domain'] == 'world' for c in world['claims']))
        self.assertTrue(all(c['status'] == 'auto_accepted' for c in world['claims']))
        self.assertIn('灯台内', (self.out/'world_bible.md').read_text(encoding='utf-8'))
        maps = json.loads((self.out/'story_maps.json').read_text(encoding='utf-8'))
        self.assertTrue(all(w['nodes'][0]['world_entities'] for w in maps['works']))
        again = WorldModel()
        audit2 = analyze(self.corpus, self.out, again, scope='original:world', workers=2,
                         retries=0, interval=0, fanout=4)
        self.assertEqual(audit2['attempts'], 0)
        self.assertFalse(again.calls)
        self.assertNotIn(BODY[:80], json.dumps(world, ensure_ascii=False))

    def test_requires_world_arrays_and_rejects_fabricated_ids(self):
        chunk = segments(BODY); raw = extracted(chunk)
        clean = parse_extract(raw, chunk)
        self.assertEqual(len(clean['world_entities']), 4)
        for key in ['world_entities', 'world_claims']:
            bad = copy.deepcopy(raw); del bad[key]
            with self.assertRaises(ValueError): parse_extract(bad, chunk)
        bad = copy.deepcopy(raw); bad['world_entities'][0]['name'] = '登場しない国'
        with self.assertRaises(ValueError): parse_extract(bad, chunk)
        bad = copy.deepcopy(raw); bad['world_entities'][0]['id'] = 'c'
        with self.assertRaises(ValueError): parse_extract(bad, chunk)
        bad = copy.deepcopy(raw); bad['world_claims'][0]['object'] = 'UNKNOWN'
        with self.assertRaises(ValueError): parse_extract(bad, chunk)
        bad = copy.deepcopy(raw); bad['world_claims'][0]['subject'] = 'c'; bad['world_claims'][0]['object'] = None
        with self.assertRaises(ValueError): parse_extract(bad, chunk)
        bad = copy.deepcopy(raw); bad['events'][0]['world_entities'] = ['MISSING']
        with self.assertRaises(ValueError): parse_extract(bad, chunk)

    def test_invalid_modality_and_unsupported_evidence_rejected(self):
        for field, value in [('modality', 'absolute_truth'), ('polarity', 'certain'), ('evidence_ids', ['S999999'])]:
            raw = extracted(segments(BODY)); raw['world_claims'][0][field] = value
            with self.assertRaises(ValueError): parse_extract(raw, segments(BODY))

    def test_failed_world_extraction_is_not_a_successful_empty_graph(self):
        class Broken(WorldModel):
            def ask(self, prompt):
                result = super().ask(prompt)
                if 'world_claims' in result: del result['world_claims']
                return result
        with self.assertRaises(ValueError):
            analyze(self.corpus, self.out, Broken(), retries=0, interval=0)
        self.assertEqual(json.loads((self.out/'world_audit.json').read_text())['state'], 'failed')


class WorldStoreTests(WorldFixture):
    def test_typed_ids_keep_old_character_identity(self):
        s, src, q, actor, place, system, resource, claim = self.fixture()
        try:
            self.assertEqual(actor, uid('C', src['id'], 'ミナ', src['scope']))
            ident = s.entity(src['id'], '白灯市', src['scope'])
            self.assertNotEqual(ident, place)
            with self.assertRaises(ValueError): s.merge(ident, place, 'user', 'different types')
            another = s.entity('other', '白灯の市', src['scope'], entity_type='place')
            s.merge(another, place, 'user', 'same location')
            self.assertEqual(s.canonical(another), place)
            s.unmerge(another, 'user', 'reconsidered')
            self.assertEqual(s.canonical(another), another)
        finally: s.close()

    def test_world_claims_do_not_leak_into_character_context(self):
        s, src, q, actor, place, system, resource, claim = self.fixture()
        try:
            self.assertEqual(s.writer_context(src['scope'])['claims'], [])
            w = s.world_context(src['scope'])
            self.assertEqual(len(w['claims']), 1)
            self.assertEqual(w['claims'][0]['conditions'], ['潮石が充電されている'])
            self.assertEqual(w['claims'][0]['modality'], 'capability')
            self.assertEqual(s.world_context(src['scope'], .2)['claims'], [])
            self.assertEqual(s.world_context('other:canon')['claims'], [])
            s.review(claim, 'revoke', 1, 'user', 'do not use')
            self.assertEqual(s.world_context(src['scope'])['claims'], [])
            s.review(claim, 'correct', 2, 'user', 'keep conditional', {'exceptions': ['別の蓄電装置で代用できる']})
            self.assertEqual(s.world_context(src['scope'])['claims'][0]['status'], 'human_approved')
        finally: s.close()

    def test_unreviewed_world_source_edit_excludes_claim(self):
        s, src, q, actor, place, system, resource, claim = self.fixture()
        try:
            Path(src['path']).write_text(BODY+'後日変更。', encoding='utf-8')
            self.assertEqual(s.world_context(src['scope'])['claims'], [])
            with self.assertRaises(Conflict): s.review(claim, 'approve', 1, 'user', 'stale')
        finally: s.close()

    def test_character_endpoints_cannot_be_world_without_world_entity(self):
        s, src, q, actor, place, system, resource, claim = self.fixture()
        try:
            raw = s.claim(claim)['original']
            raw.update(subject=actor, object=None)
            with self.assertRaises(ValueError): s.add_claim(raw)
            raw = s.claim(claim)['original']; raw.update(domain='character', kind='voice')
            with self.assertRaises(ValueError): s.add_claim(raw)
        finally: s.close()

    def test_opposite_claims_flagged_not_silently_overwritten(self):
        s, src, q, actor, place, system, resource, claim = self.fixture()
        try:
            other = s.claim(claim)['original']; other.update(polarity='denied', value='その資源は必要ないという別の説明')
            opposing = s.add_claim(other)
            w = s.world_context(src['scope'])
            self.assertEqual(len(w['claims']), 2)
            self.assertEqual(len(w['conflicts']['candidates']), 1)
            self.assertEqual(w['conflicts']['candidates'][0]['temporal_overlap'], 'unknown')
            s.review(opposing, 'revoke', 1, 'user', 'unreliable statement')
            self.assertEqual(s.world_context(src['scope'])['conflicts']['candidates'], [])
        finally: s.close()

    def test_eras_conditions_and_scopes_are_not_conflated(self):
        base = {'id':'A', 'scope':'canon1', 'subject':'S', 'object':'O', 'predicate':'requires',
                'modality':'capability', 'polarity':'affirmed', 'conditions':[], 'exceptions':[],
                'story_time':'旧暦', 'valid_from':0, 'valid_to':.3}
        b = {**base, 'id':'B', 'polarity':'denied', 'valid_from':.4, 'valid_to':1}
        self.assertEqual(conflict_candidates([base,b])['candidates'], [])
        for delta in ({'scope':'canon2'}, {'story_time':'新暦'}, {'conditions':['戦時中']}, {'modality':'belief'}):
            b = {**base,'id':'B','polarity':'denied',**delta}
            self.assertEqual(conflict_candidates([base,b])['candidates'], [])


class WorldWritingTests(WorldFixture):
    def ready(self):
        s, src, q, actor, place, system, resource, claim = self.fixture(); s.close()
        context = prepare(self.out, src['scope'], world_mode='canon')
        plan = sample_plan(); plan['world_claim_ids']=[claim]
        plan['scenes'][0].update(world_entity_ids=[system], world_claim_ids=[claim], world_note='条件と例外を区別する')
        return context, plan, claim

    def test_world_selected_for_plan_and_export(self):
        context, plan, claim = self.ready()
        result = validate_plan(plan, context)
        self.assertEqual(result['world_claim_ids'], [claim])
        self.assertEqual(context['schema'], 'pastiche.writer.context.v3')
        self.assertTrue((self.out/'world_bible.md').exists())
        self.assertTrue(context['warnings'])
        bad = copy.deepcopy(plan); bad['world_claim_ids']=['BAD']
        with self.assertRaises(ValueError): validate_plan(bad, context)
        bad = copy.deepcopy(plan); bad['scenes'][0]['world_entity_ids']=['BAD']
        with self.assertRaises(ValueError): validate_plan(bad, context)
        original = {**context, 'world_mode':'original'}
        with self.assertRaises(ValueError): validate_plan(plan, original)

    def test_old_writer_snapshot_cannot_resurrect_revoked_world_claim(self):
        context, plan, claim = self.ready()
        s = Store(self.db)
        s.review(claim, 'revoke', 1, 'user', 'withdraw'); s.close()
        model = WorldModel()
        with self.assertRaises(Conflict): make_plan(self.out, context, '新しい物語', model, retries=0, interval=0)
        self.assertEqual(model.calls, [])

    def test_world_draft_audit_requires_existing_rule_and_draft_evidence(self):
        context, plan, claim = self.ready()
        draft = self.root/'draft.txt'; draft.write_text('新しい人物は空の資源で術を使おうとした。', encoding='utf-8')
        class Auditor(WorldModel):
            def ask(self, prompt):
                self.calls.append(prompt)
                return {'issues':[{'kind':'world_rule','claim_id':claim,'description':'設定の適用条件を確認する必要がある',
                                   'repair':'例外が適用されるかを本文で確認する','evidence_ids':['S000001']}]}
        result = audit_draft(self.out, draft, plan, context, Auditor(), retries=0, interval=0)
        self.assertEqual(result['issues'][0]['claim_id'], claim)
        self.assertTrue((self.out/'writing/world_audit.json').exists())


class WorldResearchHTTPTests(WorldFixture):
    def test_world_research_has_same_evidence_and_opt_out_contract(self):
        class Fetch:
            def fetch(self, url): return url, BODY, []
        audit = research(self.out, ['https://example.test/world'], {'example.test'}, WorldModel(),
                         character='世界法則', universe='fixture', scope='official:world',
                         target_kind='world', max_pages=1, depth=0, delay=0, retries=0, interval=0, fetcher=Fetch())
        self.assertEqual(audit['target_kind'], 'world')
        self.assertGreater(audit['world']['claims'], 0)
        s=Store(self.db)
        try:
            self.assertTrue(all(c['observed_at'] is None for c in s.world_graph()['claims']))
            self.assertEqual(s.world_context('official:world', .5)['claims'], [])
        finally:s.close()

    def test_local_world_endpoint_and_edit_export(self):
        s,src,q,actor,place,system,resource,claim=self.fixture();s.close()
        server,token=make_server(self.out,0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(method,path,body=None,auth=True):
            conn=http.client.HTTPConnection('127.0.0.1',server.server_address[1],timeout=5)
            conn.request(method,path,None if body is None else json.dumps(body),
                         {'X-Pastiche-Token':token,'Content-Type':'application/json'} if auth else {})
            r=conn.getresponse(); data=r.read(); code=r.status;conn.close()
            return code,json.loads(data)
        try:
            self.assertEqual(request('GET','/api/world',auth=False)[0],403)
            code, data=request('GET','/api/world');self.assertEqual(code,200)
            self.assertEqual(data['claims'][0]['domain'],'world')
            code,r=request('POST','/api/review',{'id':claim,'action':'correct','revision':1,'reviewer':'user','note':'exception',
                                                'patch':{'exceptions':['新しい例外は人間が確認する']}})
            self.assertEqual(code,200);self.assertEqual(r['status'],'human_approved')
            code,r=request('POST','/api/export',{'scope':src['scope'],'cutoff':1,'world_mode':'canon'})
            self.assertEqual(code,200);self.assertEqual(r['world_mode'],'canon')
            self.assertEqual(r['world']['claims'][0]['exceptions'],['新しい例外は人間が確認する'])
        finally:server.shutdown();server.server_close();thread.join(3)

    def test_cli_demo_and_world_export(self):
        self.assertEqual(workbench.main(['demo','--out',str(self.out)]),0)
        self.assertEqual(workbench.main(['world-export','--out',str(self.out),'--canon-scope','demo:original']),0)
        context=json.loads((self.out/'world_context.json').read_text(encoding='utf-8'))
        self.assertEqual(len(context['claims']),4)
        self.assertTrue(any(c['exceptions'] for c in context['claims']))


if __name__=='__main__':unittest.main()
