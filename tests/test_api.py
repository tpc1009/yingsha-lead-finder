import json
import time
from fastapi.testclient import TestClient

from yingsha_leads.app import create_app
from yingsha_leads.browser import BrowserBridge
from test_runtime import FixtureFetcher
from test_core import HTML


def make_client(tmp_path):
    def browser_reply(args):
        if 'tabs' in args: return json.dumps({'tabs':[{'targetId':'ABC','url':'https://brand.example/','title':'Brand'}]})
        return json.dumps({'ok':True,'result':{'url':'https://brand.example/','html':HTML}})
    return TestClient(create_app(tmp_path,browser=BrowserBridge(runner=browser_reply),fetcher=FixtureFetcher(),delay=0),base_url='http://127.0.0.1')


def test_api_full_capture_edit_search_export(tmp_path):
    with make_client(tmp_path) as client:
        status=client.get('/api/status').json(); assert status['model_calls']==0
        headers={'X-CSRF-Token':status['csrf_token']}
        assert client.get('/').status_code==200
        assert client.get('/api/browser').json()['connected'] is True
        search=client.post('/api/search',json={'product':'abaya','buyer_type':'服装品牌','country':'UAE'},headers=headers)
        assert len(search.json()['queries'])==3
        saved=client.post('/api/capture',json={'target_id':'ABC','kind':'lead','product':'abaya','country':'UAE'},headers=headers)
        assert saved.status_code==200, saved.text
        lid=saved.json()['lead']['id']
        assert client.get('/api/leads').json()['total']==1
        edit=client.patch(f'/api/leads/{lid}',json={'status':'qualified','notes':'确认是制衣厂'},headers=headers)
        assert edit.json()['lead']['notes']=='确认是制衣厂'
        assert client.get('/api/leads?status=qualified').json()['total']==1
        assert client.get('/api/leads?status=new').json()['total']==0
        export=client.get('/api/export?format=xlsx&status=qualified')
        assert export.status_code==200 and export.content.startswith(b'PK')


def test_local_api_rejects_cross_site_and_missing_csrf(tmp_path):
    with make_client(tmp_path) as client:
        data={'product':'abaya','buyer_type':'brand','country':'UAE'}
        assert client.post('/api/search',json=data).status_code==403
        assert client.get('/api/status',headers={'Host':'evil.example'}).status_code==403
        assert client.get('/api/status',headers={'Origin':'https://evil.example'}).status_code==403
        assert client.get('/api/export?format=csv',headers={'Sec-Fetch-Site':'cross-site'}).status_code==403
        token=client.get('/api/status').json()['csrf_token']
        assert client.post('/api/search',json=data,headers={'X-CSRF-Token':token,'Origin':'https://evil.example'}).status_code==403


def test_api_jobs_persist_and_validate_social(tmp_path):
    with make_client(tmp_path) as client:
        headers={'X-CSRF-Token':client.get('/api/status').json()['csrf_token']}
        bad=client.post('/api/jobs',json={'urls':['https://instagram.com/brand/'],'mode':'browser','target_id':'ABC','product':'abaya','country':'UAE'},headers=headers)
        assert bad.status_code==400
        good=client.post('/api/jobs',json={'urls':['https://brand.example/'],'mode':'http','product':'abaya','country':'UAE'},headers=headers)
        assert good.status_code==200
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            jobs=client.get('/api/jobs').json()['jobs']
            if jobs[0]['status']=='completed': break
            time.sleep(.02)
        assert jobs[0]['status']=='completed'
        assert client.get('/api/leads').json()['total']==1
    with make_client(tmp_path) as client:
        assert client.get('/api/leads').json()['total']==1


def test_social_followers_source_not_eligible_for_either_capture(tmp_path):
    browser=BrowserBridge(runner=lambda a:json.dumps({'ok':True,'result':{'url':'https://instagram.com/brand/followers/','html':'<title>Followers</title><a href="https://instagram.com/one/">Person One</a><a href="https://instagram.com/two/">Person Two</a>'}}))
    with TestClient(create_app(tmp_path,browser=browser,fetcher=FixtureFetcher()),base_url='http://127.0.0.1') as client:
        headers={'X-CSRF-Token':client.get('/api/status').json()['csrf_token']}
        for kind in ['candidates','lead']:
            response=client.post('/api/capture',json={'kind':kind,'target_id':'ABC'},headers=headers)
            assert response.status_code in {400,503}


def test_note_limit_returns_readable_error_without_losing_saved_note(tmp_path):
    with make_client(tmp_path) as client:
        headers={'X-CSRF-Token':client.get('/api/status').json()['csrf_token']}
        lead=client.post('/api/capture',json={'target_id':'ABC','kind':'lead'},headers=headers).json()['lead']
        url=f'/api/leads/{lead["id"]}'
        assert client.patch(url,json={'status':'new','notes':'x'*5000},headers=headers).status_code==200
        reply=client.patch(url,json={'status':'new','notes':'x'*5001},headers=headers)
        assert reply.status_code==400 and isinstance(reply.json()['detail'],str)
        assert client.get(url).json()['lead']['notes']=='x'*5000
