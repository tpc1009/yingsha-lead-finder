import threading
import time
import json

import pytest
from fastapi.testclient import TestClient

from yingsha_leads.app import create_app
from yingsha_leads.browser import BrowserBridge, BrowserError
from yingsha_leads.jobs import JobManager
from yingsha_leads.store import Store


SOURCE = 'https://www.facebook.com/search/pages/?q=abaya%20Dubai'


def company_html(index=1, category='Clothing (Brand)', description='Abaya fashion in Dubai, UAE.'):
    return f'<html><title>Atelier {index} | Facebook</title><body><h1>Atelier {index}</h1><p>Page · {category}</p><p>{description}</p><a href="mailto:sales{index}@brand.example">Business email</a></body></html>'


class FacebookBrowser:
    validate_target = staticmethod(BrowserBridge.validate_target)

    def __init__(self, count=23):
        self.count=count; self.visited=[]; self.block=False; self.entered=None; self.release=None
        self.url=SOURCE

    def probe(self):
        return {'connected':True,'tabs':[{'targetId':'FACEBOOK','url':self.url,'title':'Abaya brands'}]}

    def capture(self, target):
        return {'url':self.url,'html':'<title>Abaya businesses</title><p>Public Facebook Pages search results</p>'+''.join(f'<a href="https://www.facebook.com/atelier{i}/">Atelier {i}</a>' for i in range(self.count))}

    def visit(self, target, url):
        self.visited.append(url)
        if self.entered: self.entered.set(); self.release.wait(3)
        if self.block: raise BrowserError('需要安全验证')
        self.url=url
        return {'url':url,'html':company_html(url.split('atelier')[-1].strip('/'))}


def wait_job(manager, jid, statuses=('completed',)):
    until=time.monotonic()+5
    while time.monotonic()<until:
        job=manager.store.get_job(jid)
        if job['status'] in statuses: return job
        time.sleep(.01)
    raise AssertionError(manager.store.get_job(jid))


def test_twenty_three_candidates_are_ten_ten_three_with_restart(tmp_path):
    store=Store(tmp_path/'db.sqlite'); browser=FacebookBrowser()
    manager=JobManager(store,browser=browser,delay=0)
    try:
        session=manager.import_facebook('FACEBOOK','abaya fabric','服装品牌','UAE')
        first=manager.next_facebook(session['id'],'FACEBOOK')
        assert wait_job(manager,first['id'])['done']==10
        manager.close()
        manager=JobManager(store,browser=browser,delay=0)
        assert len(browser.visited)==10
        second=manager.next_facebook(session['id'],'FACEBOOK')
        assert wait_job(manager,second['id'])['done']==10
        third=manager.next_facebook(session['id'],'FACEBOOK')
        assert wait_job(manager,third['id'])['done']==3
        assert [first['total'],second['total'],third['total']]==[10,10,3]
        assert len(set(browser.visited))==len(browser.visited)==23
        progress=manager.facebook.get(session['id'])
        assert progress['processed']==23 and progress['remaining']==0
        assert progress['matched']==23 and progress['active_job_id'] is None
        with pytest.raises(ValueError,match='处理完'): manager.next_facebook(session['id'],'FACEBOOK')
    finally: manager.close()


def test_reimport_appends_without_reordering_and_context_isolated(tmp_path):
    browser=FacebookBrowser(12); manager=JobManager(Store(tmp_path/'db'),browser=browser,delay=0)
    try:
        session=manager.import_facebook('FACEBOOK','abaya','服装品牌','UAE')
        job=manager.next_facebook(session['id'],'FACEBOOK'); wait_job(manager,job['id'])
        browser.url=SOURCE; browser.count=23
        again=manager.import_facebook('FACEBOOK','abaya','服装品牌','UAE')
        assert again['id']==session['id'] and again['total']==23
        assert again['processed']==10 and again['remaining']==13
        other=manager.import_facebook('FACEBOOK','abaya','制衣厂','UAE')
        assert other['id']!=session['id']
        assert other['skipped']==10
    finally: manager.close()


def test_double_click_does_not_allocate_two_batches(tmp_path):
    browser=FacebookBrowser(); browser.entered=threading.Event(); browser.release=threading.Event()
    manager=JobManager(Store(tmp_path/'db'),browser=browser,delay=0)
    try:
        session=manager.import_facebook('FACEBOOK','abaya','服装品牌','UAE')
        job=manager.next_facebook(session['id'],'FACEBOOK')
        assert browser.entered.wait(2)
        with pytest.raises(ValueError,match='本批'): manager.next_facebook(session['id'],'FACEBOOK')
        assert len(manager.store.jobs())==1
        manager.control(job['id'],'stop'); browser.release.set()
    finally: browser.release.set(); manager.close()


def test_gate_stops_batch_and_blocks_other_browser_jobs(tmp_path):
    browser=FacebookBrowser(); browser.block=True
    manager=JobManager(Store(tmp_path/'db'),browser=browser,delay=0)
    try:
        session=manager.import_facebook('FACEBOOK','abaya','服装品牌','UAE')
        job=manager.next_facebook(session['id'],'FACEBOOK')
        blocked=wait_job(manager,job['id'],('blocked',))
        assert len(browser.visited)==1 and blocked['done']==0
        assert manager.store.list_leads()['total']==0
        with pytest.raises(ValueError): manager.next_facebook(session['id'],'FACEBOOK')
        with pytest.raises(ValueError,match='阻断'): manager.create(['https://brand.example/'],'browser','FACEBOOK','','')
        browser.block=False; manager.control(job['id'],'resume')
        assert wait_job(manager,job['id'])['done']==10
        assert manager.facebook.get(session['id'])['remaining']==13
    finally: manager.close()


def test_stop_releases_unprocessed_candidates_without_saving_inflight(tmp_path):
    browser=FacebookBrowser(12); browser.entered=threading.Event(); browser.release=threading.Event()
    manager=JobManager(Store(tmp_path/'db'),browser=browser,delay=0)
    try:
        session=manager.import_facebook('FACEBOOK','abaya','服装品牌','UAE')
        job=manager.next_facebook(session['id'],'FACEBOOK')
        assert browser.entered.wait(2)
        manager.control(job['id'],'stop'); browser.release.set()
        until=time.monotonic()+2
        while manager.inflight and time.monotonic()<until: time.sleep(.01)
        assert manager.store.list_leads()['total']==0
        assert manager.facebook.get(session['id'])['remaining']==12
        browser.entered=None
        following=manager.next_facebook(session['id'],'FACEBOOK')
        assert wait_job(manager,following['id'])['done']==10
    finally: browser.release.set(); manager.close()


@pytest.mark.parametrize('url',[
    'https://www.facebook.com/atelier/posts/123', 'https://www.facebook.com/people/Jane/123/',
    'https://www.facebook.com/groups/123', 'https://www.facebook.com/search/pages/?q=abaya',
    'https://www.facebook.com/home.php', 'https://www.facebook.com/me',
    'https://www.facebook.com/events/', 'https://www.facebook.com/marketplace/',
    'https://www.facebook.com/profile.php?id=123&sk=friends',
    'https://www.facebook.com.evil.example/brand/', 'https://www.instagram.com/atelier/'
])
def test_batch_profile_filter_is_strict(url):
    from yingsha_leads.facebook import is_facebook_profile
    assert not is_facebook_profile(url)


def test_business_filter_keeps_related_and_flags_uncertain_not_personal():
    from yingsha_leads.facebook import classify
    from yingsha_leads.extract import extract_lead
    url='https://www.facebook.com/atelier/'
    def decision(html, buyer='服装品牌'):
        return classify(extract_lead(url,html,'abaya fabric','UAE'),html,buyer)['decision']
    assert decision(company_html())=='matched'
    assert decision(company_html(category='Business service',description='Contact our team for details.'))=='review'
    assert decision(company_html(category='Restaurant',description='Pizza and pasta in Dubai.'))=='skipped'
    personal='<title>Jane | Facebook</title><h1>Jane</h1><p>I love abaya fashion and travel in Dubai.</p><p>Friends · Photos · Personal blog</p>'
    assert decision(personal)=='skipped'
    assert decision(company_html(category='Textile Company',description='Abaya fabric factory wholesale.'),buyer='制衣厂')=='review'
    assert decision(company_html(category='Clothing manufacturer',description='We manufacture abaya garments.'),buyer='制衣厂')=='matched'
    assert decision(company_html(category='Textile Company',description='Abaya fabric factory wholesale.'),buyer='服装品牌')=='review'


def test_import_requires_search_page_and_filters_social_content(tmp_path):
    browser=FacebookBrowser(); browser.url='https://www.facebook.com/atelier/'
    manager=JobManager(Store(tmp_path/'db'),browser=browser,delay=0)
    try:
        with pytest.raises(ValueError,match='搜索结果'): manager.import_facebook('FACEBOOK','abaya','服装品牌','UAE')
        assert not manager.facebook.list()
    finally: manager.close()


def test_social_api_full_batch_persistence_and_csrf(tmp_path):
    browser=FacebookBrowser(12)
    with TestClient(create_app(tmp_path,browser=browser,delay=0),base_url='http://127.0.0.1') as client:
        payload={'target_id':'FACEBOOK','product':'abaya','buyer_type':'服装品牌','country':'UAE'}
        assert client.post('/api/facebook/import',json=payload).status_code==403
        headers={'X-CSRF-Token':client.get('/api/status').json()['csrf_token']}
        imported=client.post('/api/facebook/import',json=payload,headers=headers)
        assert imported.status_code==200,imported.text
        sid=imported.json()['session']['id']
        reply=client.post(f'/api/facebook/sessions/{sid}/next',json={'target_id':'FACEBOOK'},headers=headers)
        assert reply.status_code==200,reply.text
        assert reply.json()['job']['total']==10
        wait_job(client.app.state.manager,reply.json()['job']['id'])
        assert client.get('/api/facebook/sessions').json()['sessions'][0]['remaining']==2
    with TestClient(create_app(tmp_path,browser=browser,delay=0),base_url='http://127.0.0.1') as client:
        assert client.get('/api/facebook/sessions').json()['sessions'][0]['processed']==10
        assert len(browser.visited)==10


def test_mixed_batch_reports_reasons_and_does_not_invent_country(tmp_path):
    class Mixed(FacebookBrowser):
        def visit(self,target,url):
            self.visited.append(url)
            i=int(url.split('atelier')[-1].strip('/'))
            if i==0: html=company_html(category='Clothing (Brand)',description='Abaya fashion in Saudi Arabia.')
            elif i==1: html=company_html(category='Business service',description='Ask our team about our services.')
            elif i==2: html=company_html(category='Restaurant',description='Pizza in Dubai.')
            else: raise ValueError('这个页面已不存在')
            return {'url':url,'html':html}
    browser=Mixed(4); manager=JobManager(Store(tmp_path/'db'),browser=browser,delay=0)
    try:
        s=manager.import_facebook('FACEBOOK','abaya','服装品牌','UAE')
        job=manager.next_facebook(s['id'],'FACEBOOK')
        done=wait_job(manager,job['id'])
        assert [done[k] for k in ('done','review','skipped','failed')]==[1,1,1,1]
        leads=manager.store.list_leads()['items']
        assert len(leads)==2
        matched=next(l for l in leads if l['screening']=='matched')
        assert matched['status']=='new' and matched['target_country']=='UAE'
        assert 'Saudi Arabia' in matched['country_evidence']
    finally: manager.close()


def test_general_batch_does_not_accept_facebook_mode(tmp_path):
    manager=JobManager(Store(tmp_path/'db'),browser=FacebookBrowser(),delay=0)
    try:
        with pytest.raises(ValueError):
            manager.create(['https://www.facebook.com/atelier'],'facebook','FACEBOOK','abaya','UAE')
    finally: manager.close()


def test_search_pagination_keeps_same_session():
    from yingsha_leads.facebook import search_source
    assert search_source('https://www.google.com/search?q=abaya+site%3Afacebook.com&start=10')==search_source('https://www.google.com/search?q=abaya+site%3Afacebook.com')


def test_reimport_and_new_batch_preserve_existing_manual_notes(tmp_path):
    browser=FacebookBrowser(1); manager=JobManager(Store(tmp_path/'db'),browser=browser,delay=0)
    try:
        s=manager.import_facebook('FACEBOOK','abaya','服装品牌','UAE')
        j=manager.next_facebook(s['id'],'FACEBOOK'); wait_job(manager,j['id'])
        lead=manager.store.list_leads()['items'][0]
        manager.store.update_lead(lead['id'],'qualified','已联系，对方需要样品')
        browser.url=SOURCE
        manager.import_facebook('FACEBOOK','abaya','服装品牌','UAE')
        with pytest.raises(ValueError): manager.next_facebook(s['id'],'FACEBOOK')
        assert len(browser.visited)==1
        assert manager.store.get_lead(lead['id'])['notes']=='已联系，对方需要样品'
    finally: manager.close()


def test_source_alias_and_screening_survive_single_page_update(tmp_path):
    from yingsha_leads.extract import extract_lead
    store=Store(tmp_path/'db')
    lead=extract_lead('https://www.facebook.com/atelier/',company_html(),'abaya','UAE')
    saved=store.save_lead({**lead,'screening':'review','screening_reason':'需要核对客户类型','buyer_type':'制衣厂'})
    store.save_lead(lead)
    assert store.get_lead(saved['id'])['screening']=='review'


def test_personal_category_does_not_borrow_business_words_from_bio():
    from yingsha_leads.facebook import classify
    from yingsha_leads.extract import extract_lead
    html=company_html(category='Public figure',description='I love abaya fashion and traveling with my friends.')
    assert classify(extract_lead('https://www.facebook.com/jane',html,'abaya','UAE'),html,'服装品牌')['decision']=='skipped'
    html=company_html().replace('Page · Clothing (Brand)','<span>Page ·</span><span>Clothing (Brand)</span>')
    assert classify(extract_lead('https://www.facebook.com/atelier',html,'abaya','UAE'),html,'服装品牌')['decision']=='matched'


def test_redirect_alias_skips_reserved_duplicate_and_is_remembered(tmp_path):
    class AliasBrowser(FacebookBrowser):
        def visit(self,target,url):
            self.visited.append(url)
            return {'url':'https://www.facebook.com/atelier','html':company_html()}
    browser=AliasBrowser(); manager=JobManager(Store(tmp_path/'db'),browser=browser,delay=0)
    aliases=['https://www.facebook.com/profile.php?id=123','https://www.facebook.com/atelier']
    try:
        s=manager.facebook.ingest(SOURCE,[{'url':u} for u in aliases],'abaya','服装品牌','UAE')
        job=wait_job(manager,manager.next_facebook(s['id'],'FACEBOOK')['id'])
        assert browser.visited==aliases[:1]
        assert (job['done'],job['skipped'])==(1,1)
        other=manager.facebook.ingest(SOURCE,[{'url':aliases[0]}],'abaya','批发商','UAE')
        assert other['skipped']==1 and other['remaining']==0
    finally: manager.close()


def test_permission_error_blocks_batch_before_next_candidate(tmp_path):
    class Denied(FacebookBrowser):
        def visit(self,target,url):
            self.visited.append(url)
            return {'url':url,'html':'<p>You do not have permission to view this content. Please contact the page owner.</p>'}
    browser=Denied(2); manager=JobManager(Store(tmp_path/'db'),browser=browser,delay=0)
    try:
        s=manager.import_facebook('FACEBOOK','abaya','服装品牌','UAE')
        j=manager.next_facebook(s['id'],'FACEBOOK')
        wait_job(manager,j['id'],('blocked',))
        assert len(browser.visited)==1 and manager.store.list_leads()['total']==0
    finally: manager.close()


def test_bing_wrapped_results_decode_to_public_facebook_candidate():
    import base64
    from yingsha_leads.extract import extract_candidates
    url='https://www.facebook.com/atelier'
    encoded=base64.urlsafe_b64encode(url.encode()).decode().rstrip('=')
    results=extract_candidates('https://www.bing.com/search?q=abaya',f'<a href="/ck/a?u=a1{encoded}">Atelier</a>')
    assert [r['url'] for r in results]==[url]


def test_failed_items_require_explicit_retry_and_retry_is_bounded(tmp_path):
    class Failing(FacebookBrowser):
        fail=True
        def visit(self,target,url):
            if self.fail:
                self.visited.append(url)
                raise ValueError('模拟暂时解析失败')
            return super().visit(target,url)
    browser=Failing(12)
    with TestClient(create_app(tmp_path,browser=browser,delay=0),base_url='http://127.0.0.1') as client:
        manager=client.app.state.manager
        s=manager.import_facebook('FACEBOOK','abaya','服装品牌','UAE')
        for size in [10,2]:
            assert wait_job(manager,manager.next_facebook(s['id'],'FACEBOOK')['id'])['failed']==size
        assert len(browser.visited)==12
        headers={'X-CSRF-Token':client.get('/api/status').json()['csrf_token']}
        path=f'/api/facebook/sessions/{s["id"]}/retry'
        assert client.post(path,json={'target_id':'FACEBOOK'}).status_code==403
        browser.fail=False
        for size in [10,2]:
            reply=client.post(path,json={'target_id':'FACEBOOK'},headers=headers)
            assert reply.status_code==200,reply.text
            assert wait_job(manager,reply.json()['job']['id'])['done']==size
        assert manager.facebook.get(s['id'])['failed']==0
        assert client.post(path,json={'target_id':'FACEBOOK'},headers=headers).status_code==400


def test_skipped_profile_redirect_alias_is_not_visited_twice(tmp_path):
    class AliasBrowser(FacebookBrowser):
        def visit(self,target,url):
            self.visited.append(url)
            return {'url':'https://www.facebook.com/restaurant','html':company_html(category='Restaurant',description='Pizza and pasta in Dubai.')}
    browser=AliasBrowser(); manager=JobManager(Store(tmp_path/'db'),browser=browser,delay=0)
    try:
        aliases=['https://www.facebook.com/profile.php?id=123','https://www.facebook.com/restaurant']
        s=manager.facebook.ingest(SOURCE,[{'url':u} for u in aliases],'abaya','服装品牌','UAE')
        job=wait_job(manager,manager.next_facebook(s['id'],'FACEBOOK')['id'])
        assert job['skipped']==2 and browser.visited==aliases[:1]
        assert manager.store.list_leads()['total']==0
    finally: manager.close()


def test_manually_saved_failed_candidate_becomes_skipped_on_retry(tmp_path):
    from yingsha_leads.extract import extract_lead
    class Failing(FacebookBrowser):
        def visit(self,target,url): raise ValueError('模拟解析失败')
    manager=JobManager(Store(tmp_path/'db'),browser=Failing(1),delay=0)
    try:
        s=manager.import_facebook('FACEBOOK','abaya','服装品牌','UAE')
        job=wait_job(manager,manager.next_facebook(s['id'],'FACEBOOK')['id'])
        assert job['failed']==1
        manager.store.save_lead(extract_lead(job['items'][0]['url'],company_html(),'abaya','UAE'))
        with pytest.raises(ValueError,match='没有可重试'):
            manager.next_facebook(s['id'],'FACEBOOK',retry=True)
        state=manager.facebook.get(s['id'])
        assert (state['failed'],state['skipped'],state['remaining'])==(0,1,0)
    finally: manager.close()


def test_stop_during_browser_read_then_restart_keeps_candidate_pending(tmp_path):
    from yingsha_leads.facebook_store import FacebookSessions
    store=Store(tmp_path/'db'); sessions=FacebookSessions(store)
    session=sessions.ingest(SOURCE,[{'url':'https://www.facebook.com/atelier'}],'abaya','服装品牌','UAE')
    job=sessions.reserve(session['id'],'FACEBOOK')
    job['status']='stopped'; job['items'][0]['status']='running'; store.save_job(job)
    store.recover_jobs()
    assert sessions.get(session['id'])['remaining']==1
    recovered=sessions.reserve(session['id'],'FACEBOOK')
    assert recovered['total']==1 and recovered['items'][0]['status']=='pending'
    assert store.list_leads()['total']==0


def test_batch_source_contact_and_progress_save_are_atomic(tmp_path):
    from yingsha_leads.extract import extract_lead
    store=Store(tmp_path/'db')
    with pytest.raises(RuntimeError):
        with store.connect() as connection:
            store.save_lead(extract_lead('https://www.facebook.com/atelier/',company_html()),connection=connection)
            raise RuntimeError('simulated interruption before item completion')
    assert store.list_leads()['total']==0


def test_resume_api_requires_the_original_connected_tab(tmp_path):
    browser=FacebookBrowser(); browser.block=True
    with TestClient(create_app(tmp_path,browser=browser,delay=0),base_url='http://127.0.0.1') as client:
        headers={'X-CSRF-Token':client.get('/api/status').json()['csrf_token']}
        manager=client.app.state.manager
        s=manager.import_facebook('FACEBOOK','abaya','服装品牌','UAE')
        j=manager.next_facebook(s['id'],'FACEBOOK'); wait_job(manager,j['id'],('blocked',))
        for body in ({},{'target_id':'OTHER'}):
            r=client.post(f'/api/jobs/{j["id"]}/resume',json=body,headers=headers)
            assert r.status_code==400
            assert manager.store.get_job(j['id'])['status']=='blocked'
        browser.block=False
        r=client.post(f'/api/jobs/{j["id"]}/resume',json={'target_id':'FACEBOOK'},headers=headers)
        assert r.status_code==200
        wait_job(manager,j['id'])


def test_browser_visit_waits_for_unready_document_but_never_retries_gate(monkeypatch):
    monkeypatch.setattr('time.sleep',lambda seconds:None)
    calls=[]
    def runner(args):
        calls.append(args)
        if 'navigate' in args:return json.dumps({'ok':True,'url':'https://www.facebook.com/atelier/'})
        evaluations=sum('evaluate' in c for c in calls)
        if evaluations==1:return json.dumps({'ok':True,'result':{'error':'NOT_READY'}})
        return json.dumps({'ok':True,'result':{'url':'https://www.facebook.com/atelier/','html':company_html()}})
    result=BrowserBridge(runner=runner).visit('FACEBOOK','https://www.facebook.com/atelier/')
    assert result['html']==company_html()
    assert sum('evaluate' in c for c in calls)==2
    calls.clear()
    def gated(args):
        calls.append(args)
        if 'navigate' in args:return json.dumps({'ok':True,'url':'https://www.facebook.com/atelier/'})
        return json.dumps({'ok':True,'result':{'error':'SECURITY_GATE'}})
    with pytest.raises(BrowserError):BrowserBridge(runner=gated).visit('FACEBOOK','https://www.facebook.com/atelier/')
    assert sum('evaluate' in c for c in calls)==1
