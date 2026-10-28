"""Optional DOM-only adapter for sandboxes which forbid browser networking.

Does not change browser policies or production code. Static assets are injected
as test fixtures; fetch is mocked with a Python HTTP client talking to the real
local API. This tests DOM behavior, NOT real-browser network/CSP integration.
The normal browser smoke tests never enable it in CI.
"""
import http.client
import json
import os
from pathlib import Path
import re

ASSETS=Path(__file__).resolve().parents[1]/'skills/pastiche-profile/scripts/story_workbench/web'


def load(page,server,token):
    if not os.environ.get('PASTICHE_COMPONENT_ADAPTER'):
        page.goto(f'http://127.0.0.1:{server.server_address[1]}/#token={token}')
        return
    def request(path, method, body):
        conn=http.client.HTTPConnection('127.0.0.1',server.server_address[1],timeout=5)
        conn.request(method,path,body=body.encode('utf-8') if isinstance(body,str) else body,headers={'X-Pastiche-Token':token,'Content-Type':'application/json'})
        r=conn.getresponse(); status=r.status; data=r.read(); conn.close()
        return {'status':status,'body':json.loads(data)}
    page.expose_function('fixture_request',request)
    html=ASSETS.joinpath('index.html').read_text(encoding='utf-8')
    html=re.sub(r'<script\b[^>]*>.*?</script>','',html,flags=re.S)
    html=re.sub(r'<link\b[^>]*>','',html)
    page.set_content(html)
    page.add_style_tag(path=str(ASSETS/'style.css'))
    page.evaluate('''() => { window.fetch=async(path,options={})=>{
       const r=await window.fixture_request(path,options.method||'GET',options.body||null);
       return {ok:r.status>=200&&r.status<300,status:r.status,json:async()=>r.body};
    }; }''')
    js=(ASSETS/'app.js').read_text(encoding='utf-8')
    # Opaque about:blank has no sessionStorage. Token handling is tested only in
    # the real-HTTP path; the adapter leaves this production code untouched.
    js=re.sub(r'^const token = .*$',"const token = '';",js,flags=re.M)
    page.add_script_tag(content=js)
