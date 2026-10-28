"""Neutral synthetic fixtures only; no books, private servers or public crawling."""
from __future__ import annotations
from contextlib import contextmanager
import copy
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'pastiche-profile' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from story_workbench.store import Store, Conflict, segments, chunked, atomic_json
from story_workbench.protocol import parse_extract, parse_outline, Engine, Task
from story_workbench.pipeline import analyze, parse_alignment, parse_groups
from story_workbench.research import public_target, VisibleHTML, Fetcher, crawl, research
from story_workbench.server import make_server
from story_workbench.writing import prepare, validate_plan, make_plan, audit_draft
import workbench


class Model:
    model = 'fixture'
    backend = 'openai-compatible'
    endpoint = 'http://127.0.0.1:1/v1/chat/completions'
    def __init__(self, fail=None, reject=False):
        self.calls = []
        self.fail = fail
        self.reject = reject
        self.lock = threading.Lock()
        self.active = self.peak = 0

    def ask(self, prompt):
        with self.lock:
            self.calls.append(prompt)
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(.003)
            if self.fail and prompt.startswith('TASK='+self.fail):
                raise ValueError('synthetic failure')
            if prompt.startswith(('TASK=STORY_EXTRACT', 'TASK=CHARACTER_WEB')):
                refs = re.findall(r'^\[(S\d+)\]', prompt.split('DATA:\n', 1)[1], re.M)
                return {'characters': [
                    {'id': 'a', 'name': 'ミナ', 'aliases': [], 'identity': 'explicit', 'evidence_ids': refs[:1]},
                    {'id': 'b', 'name': 'レン', 'aliases': [], 'identity': 'explicit', 'evidence_ids': refs[:1]}],
                    'events': [
                        {'id': 'e1', 'summary': '人物は知らせを受けて方針を考える', 'function': '従来の期待に疑問が生じる',
                         'before': '予測を信じている', 'after': '予測を疑い始める', 'actors': ['a'], 'story_time': None, 'evidence_ids': refs[:1]},
                        {'id': 'e2', 'summary': '人物は別の判断に向けて動く', 'function': '選択の前提が改められる',
                         'before': '問いを持っている', 'after': '新しい解釈を選ぶ', 'actors': ['a', 'b'], 'story_time': '翌日', 'evidence_ids': refs[-1:]}],
                    'claims': [
                        {'subject': 'a', 'object': 'b', 'kind': 'relationship', 'predicate': 'trusts', 'value': '相手の説明を判断材料にする', 'epistemic': 'inferred', 'evidence_ids': refs[:1]},
                        {'subject': 'a', 'object': None, 'kind': 'voice', 'predicate': 'speaks', 'value': '返答を短くして判断を留保する', 'epistemic': 'inferred', 'evidence_ids': refs[-1:]}],
                    'links': [{'source': 'e1', 'target': 'e2', 'kind': 'motivates', 'evidence_ids': list(dict.fromkeys(refs[:1]+refs[-1:]))}]}
            if prompt.startswith('TASK=STORY_REDUCE'):
                nodes = json.loads(prompt.split('\nITEMS=', 1)[1])
                return {'groups': [{'children': [x['id'] for x in nodes[i:i+2]],
                    'summary': '期待から疑問へ、判断の前提が移っていく', 'function': '情報を受けて認識を変える局面',
                    'before': nodes[i]['before'], 'after': nodes[min(i+1,len(nodes)-1)]['after']}
                    for i in range(0, len(nodes), 2)]}
            if prompt.startswith('TASK=STORY_MAP'):
                return {'premise': '情報の不足が判断を遅らせる', 'trajectory': '期待を持つ人物が疑問を経て認識を改める',
                        'ending': '新しい見方のもとで選択する', 'links': []}
            if prompt.startswith('TASK=PLOT_GROUP'):
                nodes = json.loads(prompt.split('\nITEMS=', 1)[1])
                return {'groups': [{'ids': [n['id'] for n in nodes], 'label': '期待の修正を経る選択',
                                    'steps': ['既存の期待を疑う', '新しい解釈に基づいて選ぶ']}]}
            if prompt.startswith('TASK=PLOT_ALIGN'):
                group = json.loads(prompt.split('\nPATTERN=', 1)[1].split('\nWORK=',1)[0])
                work = json.loads(prompt.split('\nWORK=', 1)[1])
                roots = work['phases']
                return {'supported': True, 'alignment': [{'step': i, 'node_ids': [roots[min(i,len(roots)-1)]['id']]} for i in range(len(group['steps']))]}
            if prompt.startswith('TASK=PLOT_CHECK'):
                rows = json.loads(prompt.split('\nWINDOWS=', 1)[1])
                grouped = {}
                for row in rows: grouped.setdefault(row['step'], []).append(row['id'])
                return {'checks': [{'step': i, 'supported': not self.reject, 'evidence_ids': list(dict.fromkeys(qs)) if not self.reject else []} for i, qs in sorted(grouped.items())]}
            if prompt.startswith('TASK=WRITE_PLAN'):
                return sample_plan()
            if prompt.startswith('TASK=DRAFT_AUDIT'):
                return {'issues': []}
            # Existing qualitative pipeline fixture, preserving full default CLI.
            from test_llm_style import FixtureModel
            return FixtureModel().ask(prompt)
        finally:
            with self.lock: self.active -= 1


def sample_context():
    return {'mode': 'original', 'characters': {'entities': [], 'claims': []}, 'plot_patterns': []}


def sample_plan():
    return {'premise': '駅で待つ人物が手紙の意味を考える新作', 'pattern_ids': [],
            'characters': [{'id': 'a', 'name': '新しい人物', 'reference_id': None}],
            'facts': [{'id': 'f', 'description': '列車が遅れている', 'known_by_initial': [], 'reader_knows_initial': False}],
            'scenes': [
                {'id': 's1', 'summary': '知らせを受け取る', 'function': '前提を提示', 'character_ids': ['a'], 'depends_on': [],
                 'requires_knowledge': {'a': []}, 'reveals': {'a': ['f']}, 'reader_reveals': ['f']},
                {'id': 's2', 'summary': '次の行動を考える', 'function': '判断の更新', 'character_ids': ['a'], 'depends_on': ['s1'],
                 'requires_knowledge': {'a': ['f']}, 'reveals': {}, 'reader_reveals': []}],
            'plot_tree': [{'id': 'root', 'parent': None, 'scene_ids': []},
                          {'id': 'phase', 'parent': 'root', 'scene_ids': ['s1', 's2']}]}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.corpus = self.root / 'corpus'
        self.corpus.mkdir()
        self.out = self.root / 'out'
        self.body = ('ミナは駅でレンの話を聞いた。帰る時刻はまだ決まっていない。\n'
                     'ミナとレンは新しい知らせを待った。二人は別の方法を話し合った。\n') * 30 + 'ミナとレンは最後の選択をした。\n'
        for i in range(3):
            (self.corpus/f'fiction{i}.txt').write_text(self.body, encoding='utf-8')

    def tearDown(self): self.tmp.cleanup()

    def store_fixture(self):
        store = Store(self.out/'character_graph.sqlite3')
        source, body = store.register_source(self.corpus/'fiction0.txt', 'novel:original')
        units = segments(body)
        q = store.evidence_for(source, units, [units[0]['id']], len(body))
        a = store.entity(source['id'], 'ミナ', source['scope'])
        b = store.entity(source['id'], 'レン', source['scope'])
        cid = store.add_claim({'subject': a, 'object': b, 'predicate': 'trusts', 'kind': 'relationship',
                              'value': '相手の説明を判断材料にする', 'scope': source['scope'], 'epistemic': 'inferred',
                              'observed_at': .4, 'evidence_ids': [q]})
        return store, source, q, a, b, cid


class StoryTests(Fixture):
    def run_pipeline(self, model, **kwargs):
        return analyze(self.corpus, self.out, model, chunk_chars=800, workers=3, retries=0, interval=0,
                       fanout=4, panel_size=2, rounds=3, scope='novel:original', **kwargs)

    def test_full_coverage_hierarchy_patterns_and_parallel(self):
        model = Model()
        audit = self.run_pipeline(model)
        self.assertEqual(audit['state'], 'completed')
        self.assertEqual(audit['analyzed_works'], 3)
        self.assertEqual(audit['full_chunks'], audit['scanned_chunks'])
        self.assertGreater(audit['full_chunks'], 6) # NOT two chunks per work
        self.assertGreater(model.peak, 1)
        self.assertLessEqual(model.peak, 3)
        maps = json.loads((self.out/'story_maps.json').read_text(encoding='utf-8'))['works']
        for w in maps:
            self.assertEqual(w['total_segments'], w['scanned_segments'])
            lookup = {n['id']: n for n in w['nodes']}
            def leaves(nid):
                n=lookup[nid]
                return [nid] if n['type']=='event' else [e for c in n['children'] for e in leaves(c)]
            covered = [e for n in w['roots'] for e in leaves(n)]
            actual = [n['id'] for n in w['nodes'] if n['type']=='event']
            self.assertCountEqual(covered, actual)
            self.assertEqual(len(covered), len(set(covered)))
            self.assertTrue(any(n['story_time']=='翌日' for n in w['nodes']))
        patterns = json.loads((self.out/'plot_patterns.json').read_text(encoding='utf-8'))['patterns']
        self.assertTrue(any(p['status']=='model_supported' for p in patterns))
        graph = json.loads((self.out/'character_graph.json').read_text(encoding='utf-8'))
        self.assertTrue(all(c['status']=='auto_accepted' for c in graph['claims']))
        self.assertNotIn(self.body[:60], json.dumps(graph, ensure_ascii=False))
        self.assertEqual(len(graph['entities']), 6) # exact names not auto-merged across works

    def test_resume_preserves_human_review(self):
        self.run_pipeline(Model())
        store = Store(self.out/'character_graph.sqlite3')
        claim = store.graph()['claims'][0]
        store.review(claim['id'], 'approve', 1, 'tester', 'synthetic evidence checked')
        store.close()
        model = Model()
        audit = self.run_pipeline(model)
        self.assertEqual(audit['attempts'], 0)
        self.assertEqual(model.calls, [])
        store = Store(self.out/'character_graph.sqlite3')
        self.assertEqual(store.claim(claim['id'])['status'], 'human_approved')
        store.close()

    def test_source_rejection_does_not_become_supported(self):
        audit = self.run_pipeline(Model(reject=True))
        self.assertEqual(audit['supported_patterns'], 0)

    def test_failed_stage_has_audit_and_resumable_extraction(self):
        with self.assertRaises(ValueError): self.run_pipeline(Model(fail='STORY_MAP'))
        self.assertEqual(json.loads((self.out/'story_audit.json').read_text())['state'],'failed')
        model=Model()
        self.run_pipeline(model)
        self.assertFalse(any(p.startswith('TASK=STORY_EXTRACT') for p in model.calls))

    def test_protocol_rejects_missing_ids_and_lost_nodes(self):
        chunk=segments(self.body)[:3]
        raw=Model().ask('TASK=STORY_EXTRACT\nDATA:\n'+'\n'.join(f"[{u['id']}] {u['text']}" for u in chunk))
        raw['events'][0]['evidence_ids']=['S999999']
        with self.assertRaises(ValueError):parse_extract(raw,chunk)
        nodes=[{'id':'a'},{'id':'b'}]
        with self.assertRaises(ValueError):parse_outline({'groups':[{'children':['a'],'summary':'x','function':'x','before':'x','after':'x'}]},nodes)
        with self.assertRaises(ValueError):parse_groups({'groups':[{'ids':['a','a'],'label':'x','steps':['x','y']}]},{'a','b'})

    def test_chunking_covers_exact_nonempty_text(self):
        units=segments(self.body)
        result=chunked(units,800)
        self.assertEqual([u for c in result for u in c],units)
        for u in units:self.assertEqual(self.body[u['start']:u['end']],u['text'])


class StoreTests(Fixture):
    def test_review_revision_correction_and_reextraction(self):
        s,src,q,a,b,c=self.store_fixture()
        try:
            s.review(c,'approve',1,'human','looked at the source')
            self.assertEqual(len(s.writer_context(src['scope'])['claims']),1)
            with self.assertRaises(Conflict):s.review(c,'reject',1,'human','old revision')
            s.review(c,'correct',2,'human','weaker interpretation',{'value':'一時的に判断を借りる'})
            self.assertEqual(s.claim(c)['status'],'human_approved')
            self.assertEqual(s.writer_context(src['scope'])['claims'][0]['value'],'一時的に判断を借りる')
            s.review(c,'revoke',3,'human','do not use this interpretation')
            self.assertEqual(s.claim(c)['status'],'revoked')
            self.assertEqual(s.writer_context(src['scope'])['claims'],[])
            # A model re-extracting the same original claim must not reset a human rejection.
            original=s.claim(c)['original']
            self.assertEqual(s.add_claim(original),c)
            self.assertEqual(s.claim(c)['status'],'revoked')
            s.review(c,'approve',4,'human','restored after review')
            self.assertEqual(s.claim(c)['status'],'human_approved')
            self.assertEqual(s.claim(c)['revision'],5)
            self.assertEqual(len(s.history(c)),4)
        finally:s.close()

    def test_source_change_invalidates_approval_and_selectors(self):
        s,src,q,a,b,c=self.store_fixture()
        try:
            self.assertEqual(s.claim(c)['status'],'auto_accepted')
            self.assertEqual(len(s.writer_context(src['scope'])['claims']),1)
            s.review(c,'approve',1,'human','checked')
            Path(src['path']).write_text(self.body+'変更。',encoding='utf-8')
            self.assertTrue(s.graph()['claims'][0]['stale'])
            self.assertEqual(s.writer_context(src['scope'])['claims'],[])
            with self.assertRaises(Conflict):s.resolve_evidence(q)
            with self.assertRaises(Conflict):s.review(c,'approve',2,'human','must fail')
        finally:s.close()

    def test_scope_time_and_identity_rules(self):
        s,src,q,a,b,c=self.store_fixture()
        try:
            s.review(c,'approve',1,'human','checked')
            self.assertEqual(s.writer_context(src['scope'],.3)['claims'],[])
            self.assertEqual(s.writer_context('anime:original')['claims'],[])
            other=s.entity('other','ミナ','anime:original')
            with self.assertRaises(ValueError):s.merge(a,other,'human','different canon')
            s.merge(a,b,'human','synthetic alias check')
            self.assertEqual(s.canonical(a),b)
            with self.assertRaises(ValueError):s.merge(b,a,'human','would form a cycle')
            self.assertEqual(len(s.graph()['merges']),1)
            s.unmerge(a,'human','restore separate identities')
            self.assertEqual(s.canonical(a),a)
            self.assertEqual(len(s.graph()['merges']),2)
        finally:s.close()

    def test_evidence_selectors_use_codepoints_with_emoji(self):
        s,src,q,a,b,c=self.store_fixture()
        try:
            p=self.root/'emoji.txt';body='🌅ミナはレンの話を聞いた。\n🌠帰り道に考えた。'
            p.write_text(body,encoding='utf-8')
            source,decoded=s.register_source(p,'novel:emoji')
            units=segments(decoded)
            eid=s.evidence_for(source,units,[units[1]['id']],len(decoded))
            view=s.resolve_evidence(eid)
            self.assertEqual(view['coordinate_unit'],'unicode_code_points')
            sel=view['selectors'][0]
            self.assertEqual(view['text'][sel['start']-view['offset']:sel['end']-view['offset']],units[1]['text'])
        finally:s.close()

    def test_evidence_required_and_invalid_coordinates_rejected(self):
        s,src,q,a,b,c=self.store_fixture()
        try:
            raw=s.claim(c)['original']
            raw['evidence_ids']=[]
            with self.assertRaises(ValueError):s.add_claim(raw)
            with self.assertRaises(ValueError):s.save_layout({a:{'x':float('nan'),'y':0}})
            with self.assertRaises(ValueError):s.review(c,'correct',1,'human','test',{'valid_from':.8,'valid_to':.2})
        finally:s.close()


    def test_auto_accept_legacy_migration_and_human_decision_persistence(self):
        s,src,q,a,b,c = self.store_fixture()
        try:
            self.assertEqual(s.claim(c)['status'], 'auto_accepted')
            selected = s.writer_context(src['scope'])['claims']
            self.assertEqual(len(selected), 1)
            self.assertEqual(selected[0]['status'], 'auto_accepted')
            self.assertIn('NOT independently verified', s.writer_context(src['scope'])['note'])

            # Simulate an old DB created with the default 'pending' policy.
            s.db.execute("UPDATE claims SET status='pending' WHERE id=?", (c,))
            s.db.commit()
        finally:
            s.close()

        s = Store(self.out/'character_graph.sqlite3')
        try:
            self.assertEqual(s.claim(c)['status'], 'auto_accepted')
            s.review(c, 'defer', 1, 'human', 'check later')
            self.assertEqual(s.writer_context(src['scope'])['claims'], [])
        finally:
            s.close()

        s = Store(self.out/'character_graph.sqlite3')
        try:
            self.assertEqual(s.claim(c)['status'], 'needs_review')
            s.review(c, 'reject', 2, 'human', 'incorrect')
            self.assertEqual(s.claim(c)['status'], 'rejected')
            self.assertEqual(s.writer_context(src['scope'])['claims'], [])
        finally:
            s.close()

    def test_untouched_auto_accept_is_invalidated_by_source_edit(self):
        s,src,q,a,b,c = self.store_fixture()
        try:
            self.assertEqual(len(s.writer_context(src['scope'])['claims']), 1)
            Path(src['path']).write_text(self.body+'追記。', encoding='utf-8')
            self.assertEqual(s.claim(c)['status'], 'auto_accepted')
            self.assertEqual(s.writer_context(src['scope'])['claims'], [])
        finally:
            s.close()


class HTTPTests(Fixture):
    def test_token_origin_host_review_and_export(self):
        s,src,q,a,b,c=self.store_fixture();s.close()
        server,token=make_server(self.out,0,'test-session-token')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        port=server.server_address[1]
        def request(method,path,body=None,headers=None):
            conn=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
            conn.request(method,path,body=json.dumps(body).encode() if body is not None else None,
                         headers=headers or {})
            r=conn.getresponse();status=r.status;payload=r.read();hdr=dict(r.getheaders());conn.close()
            return status,payload,hdr
        good={'X-Pastiche-Token':token,'Content-Type':'application/json'}
        try:
            self.assertEqual(request('GET','/api/graph')[0],403)
            self.assertEqual(request('GET','/api/graph',headers={**good,'Origin':'https://evil.invalid'})[0],403)
            self.assertEqual(request('GET','/api/graph',headers={**good,'Host':'evil.invalid'})[0],403)
            self.assertEqual(request('GET','/../../etc/passwd',headers=good)[0],404)
            status,payload,hdr=request('GET','/api/graph',headers=good)
            self.assertEqual(status,200);self.assertIn('frame-ancestors',hdr['Content-Security-Policy'])
            self.assertNotIn('Access-Control-Allow-Origin',hdr)
            self.assertEqual(request('GET','/api/evidence?id='+q,headers=good)[0],200)
            self.assertEqual(request('POST','/api/review',{'id':c,'action':'approve','revision':1,'reviewer':'human','note':'test'},good)[0],200)
            self.assertEqual(request('POST','/api/review',{'id':c,'action':'approve','revision':1,'reviewer':'human'},good)[0],409)
            self.assertEqual(request('POST','/api/export',{'scope':src['scope'],'cutoff':1},good)[0],200)
            self.assertTrue((self.out/'character_bible.md').is_file())
        finally:server.shutdown();server.server_close();thread.join(3)


class ResearchTests(Fixture):
    def test_private_hosts_and_credentials_are_blocked(self):
        for address in ['127.0.0.1','10.0.0.1','169.254.169.254','::1','192.0.2.1']:
            with patch('socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',(address,443))]):
                with self.assertRaises(ValueError):public_target('https://site.test/page',{'site.test'})
        with self.assertRaises(ValueError):public_target('https://u:p@site.test/page',{'site.test'})
        with self.assertRaises(ValueError):public_target('file:///etc/passwd',{'site.test'})
        with self.assertRaises(ValueError):public_target('https://other.test',{'site.test'})

    def test_robots_denial_is_not_bypassed(self):
        f=Fetcher({'site.test'})
        with patch('story_workbench.research.public_target'),patch.object(f,'request',return_value=(200,{},b'User-agent: *\nDisallow: /')):
            with self.assertRaisesRegex(ValueError,'robots'):f.fetch('https://site.test/')
        f=Fetcher({'site.test'})
        with patch('story_workbench.research.public_target'),patch.object(f,'request',return_value=(503,{},b'')):
            with self.assertRaises(ValueError):f.fetch('https://site.test/')

    def test_failed_research_does_not_leave_completed_audit(self):
        class FakeFetcher:
            def fetch(_, url): return url, self.body, []
        atomic_json(self.out/'research/audit.json', {'state':'completed'})
        with self.assertRaises(ValueError):
            research(self.out,['https://site.test/a'],{'site.test'},Model(fail='CHARACTER_WEB'),
                     character='ミナ',universe='fiction',scope='novel:web',max_pages=1,
                     interval=0,retries=0,fetcher=FakeFetcher())
        self.assertEqual(json.loads((self.out/'research/audit.json').read_text())['state'],'failed')

    def test_html_is_data_and_crawl_is_bounded(self):
        parser=VisibleHTML();parser.feed('<p>ミナの説明</p><script>alert(1)</script><a href="/b">参照</a>')
        self.assertNotIn('alert',parser.content());self.assertEqual(parser.links,['/b'])
        class FakeFetcher:
            def fetch(_,url):return url,self.body,['https://site.test/b','https://other.test/private']
        saved=crawl(self.out,['https://site.test/a'],{'site.test'},max_pages=2,depth=1,fetcher=FakeFetcher())
        self.assertEqual(len(saved),2)
        logs=[json.loads(l) for l in (self.out/'research/fetch-log.jsonl').read_text().splitlines()]
        self.assertEqual(logs[1]['parent'],'https://site.test/a')
        audit=research(self.out,['https://site.test/a'],{'site.test'},Model(),character='ミナ',universe='fiction',
                       scope='novel:web',max_pages=1,interval=0,retries=0,fetcher=FakeFetcher())
        self.assertGreater(audit['claims'],0)
        s=Store(self.out/'character_graph.sqlite3')
        try:self.assertTrue(all(c['status']=='auto_accepted' and c['observed_at'] is None for c in s.graph()['claims']))
        finally:s.close()


class WritingTests(Fixture):
    def test_valid_plan_and_knowledge_leak(self):
        p=sample_plan();validate_plan(p,sample_context())
        p['scenes'][0]['requires_knowledge']={'a':['f']}
        with self.assertRaisesRegex(ValueError,'before acquiring'):validate_plan(p,sample_context())

    def test_tree_and_unapproved_references(self):
        p=sample_plan();p['plot_tree'][1]['scene_ids']=['s1']
        with self.assertRaises(ValueError):validate_plan(p,sample_context())
        p=sample_plan();p['characters'][0]['reference_id']='invented-canon'
        with self.assertRaises(ValueError):validate_plan(p,sample_context())
        p=sample_plan();p['scenes'][0]['depends_on']=['s2']
        with self.assertRaises(ValueError):validate_plan(p,sample_context())

    def test_writer_workflow_and_draft_hash(self):
        s,src,q,a,b,c=self.store_fixture();s.review(c,'approve',1,'human','checked');s.close()
        context=prepare(self.out,src['scope'])
        self.assertEqual(len(context['characters']['claims']),1)
        plan=make_plan(self.out,context,'駅で待つ人についての新しい短編',Model(),retries=0,interval=0)
        draft=self.root/'draft.txt';draft.write_text('新しい人物は手紙を読んだ。次の列車を待つことにした。',encoding='utf-8')
        result=audit_draft(self.out,draft,plan,context,Model(),retries=0,interval=0)
        self.assertEqual(result['status'],'model_review');self.assertEqual(len(result['draft_sha256']),64)


class DefaultCLIIntegration(Fixture):
    def test_full_default_pipeline_with_synthetic_http_endpoint(self):
        model=Model()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                prompt=body['messages'][-1]['content']
                if prompt.startswith('TASK=LEXICAL_IMPRESSION'):
                    result={'assessments':[]}
                else:result=model.ask(prompt)
                payload=json.dumps({'choices':[{'message':{'content':json.dumps(result,ensure_ascii=False)}}]},ensure_ascii=False).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            cmd=[sys.executable,str(SCRIPTS/'run.py'),str(self.corpus),'--out',str(self.out),
                 '--api-base',f'http://127.0.0.1:{server.server_address[1]}/llm/v1','--model','fixture',
                 '--no-stream','--no-auto-install','--morphology','none','--no-lexical-review',
                 '--progress-interval','0','--llm-workers','2','--canon-scope','novel:original']
            result=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',timeout=60)
            self.assertEqual(result.returncode,0,result.stderr[-6000:])
            for f in ('style_bible.md','narrative_bible.md','character_graph.sqlite3','story_audit.json'):
                self.assertTrue((self.out/f).is_file(),f)
            self.assertIn('Narrative Bible',(self.out/'style_bible.md').read_text(encoding='utf-8'))
        finally:server.shutdown();server.server_close();thread.join(3)


if __name__=='__main__':unittest.main()
