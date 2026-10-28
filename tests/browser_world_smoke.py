"""Real browser world review flow; Playwright is only a test dependency."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

SCRIPTS=Path(__file__).resolve().parents[1]/'skills/pastiche-profile/scripts'
sys.path.insert(0,str(SCRIPTS))
from story_workbench.server import make_server
from story_workbench.store import Store
from workbench import demo
from playwright.sync_api import sync_playwright, expect
from component_adapter import load


def main():
    with tempfile.TemporaryDirectory() as temporary:
        out=Path(temporary)
        demo(out)
        server,token=make_server(out,0)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        errors=[]
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True,executable_path=os.environ.get('PLAYWRIGHT_EXECUTABLE_PATH'))
                page=browser.new_page(viewport={'width':1560,'height':1050})
                page.on('pageerror',lambda e:errors.append(str(e)))
                load(page,server,token)
                page.wait_for_selector('button.claim')
                page.get_by_role('button',name='世界観',exact=True).click()
                page.select_option('#scope','demo:original')
                expect(page.locator('#claims button.claim')).to_have_count(4)
                rule=page.locator('#claims button.claim').filter(has_text='充填された資源')
                rule.click()
                expect(page.locator('#inspector')).to_contain_text('潮石が充電されている')
                expect(page.locator('#inspector')).to_contain_text('灯台内では予備電源を使用できる')
                expect(page.locator('#inspector mark')).to_contain_text('浮航術')
                page.get_by_text('主張の訂正・期間の設定',exact=True).click()
                page.get_by_label('例外（1行に1件）').fill('灯台の予備電源を使う場合は例外')
                page.get_by_label('判断理由',exact=True).fill('合成テスト：例外の説明を明確化')
                page.get_by_role('button',name='訂正して反映',exact=True).click()
                expect(page.locator('#message')).to_contain_text('判断を履歴')
                page.select_option('#worldMode','canon')
                page.get_by_role('button',name='執筆用に出力',exact=True).click()
                expect(page.locator('#message')).to_contain_text('世界観 4件')
                context=json.loads((out/'writer_context.json').read_text(encoding='utf-8'))
                assert context['world_mode']=='canon'
                assert len(context['world']['claims'])==4
                assert any(c['exceptions']==['灯台の予備電源を使う場合は例外'] and c['status']=='human_approved' for c in context['world']['claims'])
                page.get_by_role('button',name='使用停止',exact=True).click()
                expect(page.locator('#message')).to_contain_text('判断を履歴')
                page.get_by_role('button',name='執筆用に出力',exact=True).click()
                expect(page.locator('#message')).to_contain_text('世界観 3件')
                assert len(json.loads((out/'writer_context.json').read_text(encoding='utf-8'))['world']['claims'])==3
                page.get_by_role('button',name='確認済みにする',exact=True).click()
                expect(page.locator('#message')).to_contain_text('判断を履歴')
                expect(page.locator('#claims button.claim')).to_have_count(4)
                if os.environ.get('WORLD_SCREENSHOT'):
                    page.screenshot(path=os.environ['WORLD_SCREENSHOT'],full_page=True)
                page.select_option('#entityType','organization')
                expect(page.locator('#claims button.claim')).to_have_count(2)
                page.set_viewport_size({'width':640,'height':900})
                assert page.locator('#graph').is_visible()
                browser.close()
            assert not errors,errors
            print('PASS: '+('DOM adapter' if os.environ.get('PASTICHE_COMPONENT_ADAPTER') else 'real Chromium HTTP')+', world graph, source highlights, exception correction, revoke/restore, canon export and type filter')
        finally:
            server.shutdown();server.server_close();worker.join(3)


if __name__=='__main__':main()
