import json
import threading
import time

import pytest

from yingsha_leads.browser import BrowserBridge, BrowserError
from yingsha_leads.fetching import validate_network, FetchError, PageFetcher
from yingsha_leads.jobs import JobManager
from yingsha_leads.store import Store
from test_core import HTML


def test_browser_is_fixed_cli_not_model_and_no_secrets():
    commands=[]
    def runner(command):
        commands.append(command)
        return json.dumps({'ok':True,'result':{'url':'https://brand.example/','html':HTML}})
    browser=BrowserBridge(runner=runner)
    result=browser.capture('ABC123')
    assert result['url']=='https://brand.example/'
    assert commands[0][:4]==['openclaw','browser','--browser-profile','chrome']
    assert 'evaluate' in commands[0]
    script=commands[0][commands[0].index('--fn')+1]
    assert 'document.cookie' not in script
    assert 'localStorage' not in script
    assert 'fetch(' not in script
    assert not any(x in commands[0] for x in ['agent','cookies','storage'])


def test_disconnected_browser_is_actionable_not_success():
    browser=BrowserBridge(runner=lambda args: json.dumps({'tabs':[]}))
    status=browser.probe()
    assert status['connected'] is False
    assert '标签页' in status['message']


def test_browser_security_gate_does_not_collect():
    browser=BrowserBridge(runner=lambda args: json.dumps({'ok':True,'result':{'error':'SECURITY_GATE'}}))
    with pytest.raises(BrowserError,match='验证'): browser.capture('ABC')


def test_browser_rejects_protocol_errors_and_target_injection():
    browser=BrowserBridge(runner=lambda args:'secret=NEVER_PRINT_THIS')
    with pytest.raises(BrowserError) as exc: browser.capture('ABC')
    assert 'NEVER_PRINT_THIS' not in str(exc.value)
    with pytest.raises(ValueError): browser.capture('--token bad')


def test_network_blocks_dns_private_and_redirect_destination():
    with pytest.raises(ValueError): validate_network('https://public.example/',resolver=lambda h:['127.0.0.1'])
    with pytest.raises(ValueError): validate_network('https://public.example/',resolver=lambda h:['10.1.1.1'])
    assert validate_network('https://public.example/',resolver=lambda h:['93.184.216.34'])
    with pytest.raises(ValueError): validate_network('https://public.example/',resolver=lambda h:['198.18.0.1'],fake_ip_check=lambda ip:False)
    assert validate_network('https://public.example/',resolver=lambda h:['198.18.0.1'],fake_ip_check=lambda ip:True)


class FixtureFetcher:
    def __init__(self): self.calls=[]; self.block=False
    def fetch(self,url):
        self.calls.append(url)
        if self.block: raise FetchError('需要验证',blocked=True)
        return {'url':url,'html':HTML}


def await_status(manager,jid,status):
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        job=manager.store.get_job(jid)
        if job['status'] in status: return job
        time.sleep(.02)
    raise AssertionError(manager.store.get_job(jid))


def test_jobs_complete_with_dedup_and_no_model(tmp_path):
    store=Store(tmp_path/'test.sqlite'); fetcher=FixtureFetcher()
    manager=JobManager(store,fetcher=fetcher,delay=0)
    try:
        job=manager.create(['https://brand.example/','https://brand.example/?utm_source=x'], 'http', '', 'abaya', 'UAE')
        done=await_status(manager,job['id'],{'completed'})
        assert done['total']==1
        assert done['done']==1 and done['failed']==0
        assert store.list_leads()['total']==1
    finally: manager.close()


def test_blocked_pauses_and_resume_keeps_progress(tmp_path):
    store=Store(tmp_path/'test.sqlite'); fetcher=FixtureFetcher(); fetcher.block=True
    manager=JobManager(store,fetcher=fetcher,delay=0)
    try:
        job=manager.create(['https://brand.example/'],'http','','abaya','UAE')
        blocked=await_status(manager,job['id'],{'blocked'})
        assert blocked['done']==0 and len(fetcher.calls)==1
        fetcher.block=False; manager.control(job['id'],'resume')
        done=await_status(manager,job['id'],{'completed'})
        assert done['done']==1
    finally: manager.close()


def test_social_batch_rejected_in_both_modes(tmp_path):
    manager=JobManager(Store(tmp_path/'test.sqlite'),fetcher=FixtureFetcher(),delay=0)
    try:
        for mode in ['http','browser']:
            with pytest.raises(ValueError,match='社交'):
                manager.create(['https://instagram.com/brand/'],mode,'ABC','abaya','UAE')
    finally: manager.close()


def test_pause_during_fetch_does_not_advance_next_page(tmp_path):
    entered=threading.Event(); release=threading.Event()
    class SlowFetcher(FixtureFetcher):
        def fetch(self,url):
            self.calls.append(url); entered.set(); release.wait(2)
            return {'url':url,'html':HTML}
    fetcher=SlowFetcher(); manager=JobManager(Store(tmp_path/'test.sqlite'),fetcher=fetcher,delay=0)
    try:
        job=manager.create(['https://one.example/','https://two.example/'],'http','','abaya','UAE')
        assert entered.wait(2)
        manager.control(job['id'],'pause'); release.set()
        await_status(manager,job['id'],{'paused'})
        time.sleep(.1)
        assert len(fetcher.calls)==1
        assert manager.store.get_job(job['id'])['status']=='paused'
    finally: release.set(); manager.close()


def test_restart_recovers_without_automatic_fetch(tmp_path):
    store=Store(tmp_path/'test.sqlite')
    store.save_job({'status':'running','items':[{'url':'https://brand.example','status':'running'}]})
    fetcher=FixtureFetcher(); manager=JobManager(store,fetcher=fetcher,delay=0)
    try:
        assert store.jobs()[0]['status']=='paused'
        assert store.jobs()[0]['items'][0]['status']=='pending'
        assert not fetcher.calls
    finally: manager.close()


@pytest.mark.parametrize('pause_before_block',[False,True])
def test_blocked_browser_stops_other_queued_jobs(tmp_path,pause_before_block):
    entered=threading.Event(); release=threading.Event()
    class BlockBrowser:
        calls=[]
        validate_target=staticmethod(BrowserBridge.validate_target)
        def visit(self,target,url):
            self.calls.append(url); entered.set(); release.wait(2)
            raise BrowserError('需要安全验证')
    browser=BlockBrowser(); manager=JobManager(Store(tmp_path/'test.sqlite'),browser=browser,fetcher=FixtureFetcher(),delay=0)
    try:
        first=manager.create(['https://one.example/'],'browser','ABC','','')
        assert entered.wait(2)
        second=manager.create(['https://two.example/'],'browser','ABC','','')
        if pause_before_block: manager.control(first['id'],'pause')
        release.set(); await_status(manager,first['id'],{'paused' if pause_before_block else 'blocked'}); time.sleep(.1)
        assert browser.calls==['https://one.example/']
        assert manager.store.get_job(second['id'])['status']=='paused'
        with pytest.raises(ValueError,match='阻断'):
            manager.control(second['id'],'resume')
        with pytest.raises(ValueError,match='阻断'):
            manager.create(['https://three.example/'],'browser','ABC','','')
    finally: release.set(); manager.close()


def test_restart_recovers_paused_inflight_browser_and_can_resume(tmp_path):
    store=Store(tmp_path/'test.sqlite')
    job=store.save_job({'status':'paused','mode':'browser','target_id':'ABC','product':'','country':'','total':1,'done':0,'failed':0,'items':[{'url':'https://brand.example/','status':'running'}]})
    class Browser:
        def visit(self,target,url): return {'url':url,'html':HTML}
    manager=JobManager(store,browser=Browser(),fetcher=FixtureFetcher(),delay=0)
    try:
        assert store.get_job(job['id'])['items'][0]['status']=='pending'
        assert not manager.browser_busy()
        manager.control(job['id'],'resume')
        assert await_status(manager,job['id'],{'completed'})['done']==1
        assert not manager.browser_busy()
    finally: manager.close()
