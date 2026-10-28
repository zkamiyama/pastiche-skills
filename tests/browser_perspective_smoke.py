"""Real browser: inspect and revoke source-perspective records (neutral fixture)."""
from pathlib import Path
import sys
import os
import tempfile
import threading
SCRIPTS = Path(__file__).resolve().parents[1]/'skills/pastiche-profile/scripts'
sys.path.insert(0,str(SCRIPTS))
from story_workbench.store import Store, segments
from story_workbench.server import make_server
from playwright.sync_api import sync_playwright, expect
from component_adapter import load


def main():
    with tempfile.TemporaryDirectory() as tmp:
        out=Path(tmp);path=out/'fiction.txt'
        path.write_text('アキは物がかごにあると思っている。ベルが物を箱に移したことは知らない。',encoding='utf-8')
        s=Store(out/'character_graph.sqlite3')
        try:
            src,body=s.register_source(path,'fixture:novel');units=segments(body)
            q=s.evidence_for(src,units,[units[0]['id']],len(body))
            a=s.entity(src['id'],'アキ',src['scope']);obj=s.entity(src['id'],'物',src['scope'],entity_type='artifact')
            cid=s.add_claim({'domain':'epistemic','kind':'perspective','subject':a,'object':obj,
                'holder':a,'proposition_subject':obj,'predicate':'location','value':'かご',
                'stance':'affirmed','basis':'perception','speaker':None,'event_id':None,
                'epistemic':'inferred','scope':src['scope'],'observed_at':1,'evidence_ids':[q]})
        finally:s.close()
        server,token=make_server(out,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        errors=[]
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True, executable_path=os.environ.get("PLAYWRIGHT_EXECUTABLE_PATH"));page=browser.new_page(viewport={'width':1440,'height':1000})
                page.on('pageerror',lambda e:errors.append(str(e)));load(page,server,token)
                page.get_by_role('button',name='認識・信念',exact=True).click()
                expect(page.locator('#queueTitle')).to_contain_text('認識・信念')
                page.locator('button.claim').first.click();expect(page.locator('#inspector')).to_contain_text('取得経路: perception')
                expect(page.locator('#inspector mark')).to_contain_text('かご')
                page.select_option('#scope','fixture:novel');page.select_option('#holderFilter',a)
                expect(page.locator('#count')).to_contain_text('1件')
                page.get_by_role('button',name='使用停止',exact=True).click()
                expect(page.locator('#message')).to_contain_text('判断を履歴')
                expect(page.locator('#count')).to_contain_text('0件')
                page.get_by_role('button',name='執筆用に出力',exact=True).click()
                expect(page.locator('#message')).to_contain_text('認識・信念 0件')
                browser.close()
            assert not errors,errors
            print('PASS: source-perspective view, holder filter, evidence and revocation over real HTTP (or explicitly opted-in DOM adapter)')
        finally:server.shutdown();server.server_close();thread.join(3)

if __name__=='__main__':main()
