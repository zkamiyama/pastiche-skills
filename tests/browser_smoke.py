"""Real Chromium/HTTP integration; test-only Playwright, never a runtime dependency."""
from pathlib import Path
import sys
import tempfile
import threading

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/pastiche-profile/scripts'
sys.path.insert(0, str(SCRIPTS))
from story_workbench.store import Store, segments
from story_workbench.server import make_server
from playwright.sync_api import sync_playwright


def main():
    with tempfile.TemporaryDirectory() as directory:
        out = Path(directory)
        source = out / 'synthetic.txt'
        source.write_text('🌅ミナはレンに時刻を尋ねた。\nレンは静かに答えた。', encoding='utf-8')
        store = Store(out / 'character_graph.sqlite3')
        src, body = store.register_source(source, 'demo:original')
        units = segments(body)
        evidence = store.evidence_for(src, units, [units[0]['id']], len(body))
        a, b = [store.entity('test', name, src['scope']) for name in ('ミナ', 'レン')]
        claim = store.add_claim({'subject': a, 'object': b, 'predicate': 'asks',
                'kind': 'relationship', 'value': '返事を求める', 'scope': src['scope'],
                'epistemic': 'explicit', 'evidence_ids': [evidence], 'observed_at': .5})
        store.close()
        server, token = make_server(out, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        errors = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                page.on('pageerror', lambda error: errors.append(str(error)))
                url = f'http://127.0.0.1:{server.server_address[1]}/#token={token}'
                page.goto(url)
                page.wait_for_selector('button.claim')
                page.locator('button.claim').first.click()
                page.wait_for_selector('#inspector mark')
                assert '🌅ミナ' in page.locator('#inspector mark').first.inner_text()
                page.get_by_role('button', name='承認', exact=True).click()
                page.wait_for_function("document.getElementById('message').textContent.includes('判断を履歴')")
                page.select_option('#scope', 'demo:original')
                page.get_by_role('button', name='承認済みを出力', exact=True).click()
                page.wait_for_function("document.getElementById('message').textContent.includes('writer_context.json')")
                assert (out / 'writer_context.json').is_file()
                store = Store(out / 'character_graph.sqlite3')
                assert store.claim(claim)['status'] == 'human_approved'
                store.close()
                page.get_by_role('button', name='物語構造', exact=True).click()
                page.wait_for_function("document.getElementById('storyContent').textContent.includes('物語分析はまだありません')")
                page.get_by_role('button', name='人物グラフ', exact=True).click()
                page.set_viewport_size({'width': 640, 'height': 900})
                assert page.locator('#graph').is_visible()
                browser.close()
            assert not errors, errors
            print('PASS: real Chromium, token auth, evidence highlight, approval, export, responsive view')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)


if __name__ == '__main__':
    main()
