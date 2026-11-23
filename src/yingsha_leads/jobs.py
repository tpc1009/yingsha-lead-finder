import threading
import time

from .browser import BrowserBridge,BrowserError
from .fetching import PageFetcher,FetchError
from .url_rules import normalize_url,is_social,is_search,host_of
from .extract import extract_lead,page_gate
from .store import now
from .facebook import is_facebook_profile,classify,search_source
from .facebook_store import FacebookSessions
from .extract import extract_candidates

BROWSER_MODES={'browser','facebook'}


class JobManager:
    def __init__(self,store,browser=None,fetcher=None,delay=5):
        self.store=store; self.browser=browser or BrowserBridge(); self.fetcher=fetcher or PageFetcher(); self.delay=delay
        self.lock=threading.RLock(); self.wake=threading.Event(); self.shutdown=threading.Event(); self.inflight=None
        self.facebook=FacebookSessions(store)
        self.facebook_delay=10 if delay==5 else delay
        self.last_facebook_visit=0
        self.store.recover_jobs()
        self.thread=threading.Thread(target=self._work,name='lead-collector',daemon=True); self.thread.start()

    def close(self):
        self.shutdown.set(); self.wake.set(); self.thread.join(timeout=3)
        if not self.thread.is_alive() and hasattr(self.fetcher,'close'): self.fetcher.close()

    def _browser_blocked(self,target_id,exclude=None):
        return any(j['id']!=exclude and j['mode'] in BROWSER_MODES and j['target_id']==target_id
                   and j['status'] not in {'stopped','completed'}
                   and (j['status']=='blocked' or any(i['status']=='blocked' for i in j['items']))
                   for j in self.store.jobs())

    def create(self,urls,mode,target_id,product,country):
        if mode not in {'http','browser'}: raise ValueError('采集方式无效')
        if not urls or len(urls)>100: raise ValueError('每次添加1～100个网址')
        normalized=list(dict.fromkeys(normalize_url(u) for u in urls))
        if any(is_social(u) for u in normalized): raise ValueError('社交平台不进入批量任务，请打开对应主页，逐页确认后使用“保存当前企业”')
        if any(is_search(u) for u in normalized): raise ValueError('搜索结果页请先提取候选网址，不加入官网采集队列')
        if mode=='browser': self.browser.validate_target(target_id)
        with self.lock:
            if mode=='browser' and self._browser_blocked(target_id):
                raise ValueError('该浏览器标签页存在验证阻断，请先处理并恢复或停止原任务')
            job=self.store.save_job({'status':'queued','mode':mode,'target_id':target_id,'product':product,'country':country,'total':len(normalized),'done':0,'failed':0,'message':'等待采集','created_at':now(),'items':[{'url':u,'status':'pending','message':''} for u in normalized]})
            self.wake.set(); return job

    def import_facebook(self,target_id,product,buyer_type,country):
        with self.lock:
            if self.browser_busy(): raise ValueError('浏览器正在采集，请先暂停并等待当前页完成')
            if self._browser_blocked(target_id): raise ValueError('该标签页存在验证阻断，请先处理原任务')
            page=self.browser.capture(target_id)
            source=search_source(page['url'])
            gate=page_gate(page['url'],page['html'])
            if gate: raise BrowserError(gate)
            return self.facebook.ingest(source,extract_candidates(page['url'],page['html']),product,buyer_type,country)

    def next_facebook(self,sid,target_id,retry=False):
        self.browser.validate_target(target_id)
        with self.lock:
            session=self.facebook.get(sid)
            if session['active_job_id']: raise ValueError('请先完成、继续或停止本批，再处理下一批')
            if self.browser_busy(): raise ValueError('浏览器正在使用，请先结束或暂停当前任务并等待当前页完成')
            if self._browser_blocked(target_id): raise ValueError('该标签页存在验证阻断，请先处理原任务')
            job=self.facebook.reserve(sid,target_id,retry=retry)
            if not job: raise ValueError('没有可重试的失败项' if retry else '当前结果已处理完，请回到搜索结果加载更多后导入')
            self.wake.set(); return job

    def control(self,jid,action):
        with self.lock:
            job=self.store.get_job(jid)
            if action=='pause' and job['status'] in {'queued','running'}: job.update(status='paused',message='已暂停；正在读取的单页可能完成保存，不再开始下一页')
            elif action=='resume' and job['status'] in {'paused','blocked'}:
                if job['mode'] in BROWSER_MODES and self._browser_blocked(job['target_id'],exclude=jid):
                    raise ValueError('该标签页仍有验证阻断，请先恢复或停止原任务')
                for item in job['items']:
                    if item['status'] in {'blocked','running'}: item['status']='pending'; item['message']=''
                job.update(status='queued',message='等待继续')
            elif action=='stop' and job['status'] not in {'completed','stopped'}: job.update(status='stopped',message='任务已停止，已保存的线索保留')
            else: raise ValueError('当前任务状态不支持此操作')
            result=self.store.save_job(job); self.wake.set(); return result

    def browser_busy(self):
        return bool(self.inflight and self.inflight['mode'] in BROWSER_MODES) or any(j['mode'] in BROWSER_MODES and j['status'] in {'running','queued'} for j in self.store.jobs())

    def _active(self,jid):
        return not self.shutdown.is_set() and self.store.get_job(jid)['status']=='running'

    def _work(self):
        while not self.shutdown.is_set():
            self.wake.wait(.5); self.wake.clear()
            with self.lock:
                jobs=[j for j in reversed(self.store.jobs()) if j['status'] in {'queued','running'}]
                if not jobs: continue
                job=jobs[0]; job['status']='running'; job['message']='正在采集'; self.store.save_job(job)
            self._run_job(job['id'])
            self.wake.set()

    def _run_job(self,jid):
        while self._active(jid):
            with self.lock:
                job=self.store.get_job(jid)
                if self.shutdown.is_set() or job['status']!='running': return
                index=next((i for i,item in enumerate(job['items']) if item['status']=='pending'),None)
                if index is None:
                    message=f'完成：成功 {job["done"]}，失败 {job["failed"]}'
                    if job['mode']=='facebook': message=f'本批完成：匹配保存 {job["done"]}，待核实 {job.get("review",0)}，跳过 {job.get("skipped",0)}，失败 {job["failed"]}'
                    job.update(status='completed',message=message)
                    self.store.save_job(job); return
                item=job['items'][index]; item['status']='running'; self.store.save_job(job); self.inflight=job
                if job['mode']=='facebook' and (self.store.has_identity(item['url']) or self.facebook.seen_final(job['session_id'],item['url'])):
                    item.update(status='skipped',message='该主页已有处理记录，跳过重复地址')
                    job['skipped']=sum(i['status']=='skipped' for i in job['items'])
                    self.store.save_job(job); self.inflight=None
                    continue
            status='done'; message='已保存'; blocked=False
            lead=None; duplicate_url=''; final_url=''
            try:
                if job['mode']=='facebook':
                    while self._active(jid):
                        remaining=self.facebook_delay-(time.monotonic()-self.last_facebook_visit)
                        if remaining<=0: break
                        self.shutdown.wait(min(.25,remaining))
                    if not self._active(jid):
                        with self.lock:
                            current=self.store.get_job(jid); current['items'][index]['status']='pending'; self.store.save_job(current); self.inflight=None
                        return
                    self.last_facebook_visit=time.monotonic()
                reply=self.browser.visit(job['target_id'],item['url']) if job['mode'] in BROWSER_MODES else self.fetcher.fetch(item['url'])
                final=normalize_url(reply['url'])
                if host_of(final)!=host_of(item['url']): raise FetchError('页面跳转到了其他域名，请核实最终官网',blocked=True)
                gate=page_gate(final,reply['html'])
                if gate: raise FetchError(gate,blocked=True)
                if job['mode']=='facebook' and not is_facebook_profile(final):
                    raise ValueError('跳转目标不是可采集的 Facebook 企业主页，已跳过')
                if job['mode']=='facebook': final_url=final
                lead=extract_lead(final,reply['html'],job['product'],job['country'])
                if job['mode']=='facebook':
                    screening=classify(lead,reply['html'],job['buyer_type'])
                    status={'matched':'done','review':'review','skipped':'skipped'}[screening['decision']]
                    message=screening['reason']
                    lead.update(screening=screening['decision'],screening_reason=message,buyer_type=job['buyer_type'])
                    lead['reasons'].append(message)
                    if status=='skipped': lead=None
                    if self.store.has_identity(final) or self.facebook.seen_final(job['session_id'],final):
                        duplicate_url=final; lead=None; status='skipped'; message='该主页的规范地址已有处理记录，已跳过重复候选'
                else:
                    with self.lock:
                        if self.store.get_job(jid)['status']!='stopped': self.store.save_lead(lead)
                if job['mode']=='http':
                    for link in lead.get('contact_pages',[]):
                        if not self._active(jid): break
                        if host_of(link)!=host_of(final): continue
                        try:
                            page=self.fetcher.fetch(link)
                            if host_of(page['url'])!=host_of(final): continue
                            self.store.save_lead(extract_lead(page['url'],page['html'],job['product'],job['country']))
                        except FetchError as exc:
                            if exc.blocked: raise
                            message='主页已保存；联系页面未能读取'
                        except ValueError: message='主页已保存；联系页面不在采集范围'
            except (FetchError,BrowserError) as exc:
                blocked=isinstance(exc,BrowserError) or exc.blocked; status='blocked' if blocked else 'failed'; message=str(exc)
            except ValueError as exc: status='failed'; message=str(exc)
            except Exception: status='failed'; message='页面处理失败，未保存该页；可检查页面后重新添加'
            with self.lock:
                current=self.store.get_job(jid); current_item=current['items'][index]
                if current['mode']=='facebook':
                    if current['status']=='stopped':
                        status='pending'; message='已停止，未处理完成的候选保留到后续批次'
                current_item.update(status=status,message=message)
                if final_url: current_item['final_url']=final_url
                current['done']=sum(i['status']=='done' for i in current['items']); current['failed']=sum(i['status']=='failed' for i in current['items'])
                if current['mode']=='facebook':
                    current['review']=sum(i['status']=='review' for i in current['items'])
                    current['skipped']=sum(i['status']=='skipped' for i in current['items'])
                if blocked and current['status'] not in {'paused','stopped'}: current.update(status='blocked',message=message)
                if current['mode']=='facebook':
                    with self.store.lock,self.store.connect() as connection:
                        connection.execute('BEGIN IMMEDIATE')
                        if lead is not None and status in {'done','review'}:
                            self.store.save_lead(lead,connection=connection,aliases=(item['url'],))
                        if duplicate_url and current['status']!='stopped':
                            self.store.add_alias(duplicate_url,item['url'],connection)
                        self.store.save_job(current,connection=connection)
                else: self.store.save_job(current)
                self.inflight=None
                if blocked:
                    for waiting in self.store.jobs():
                        same_browser=job['mode'] in BROWSER_MODES and waiting['mode'] in BROWSER_MODES and waiting['target_id']==job['target_id']
                        same_site=job['mode']=='http' and waiting['mode']=='http' and any(host_of(i['url'])==host_of(item['url']) for i in waiting['items'] if i['status']=='pending')
                        if waiting['id']!=jid and waiting['status']=='queued' and (same_browser or same_site):
                            waiting.update(status='paused',message='关联页面遇到登录验证或访问限制，等待人工确认后继续')
                            self.store.save_job(waiting)
            if not self._active(jid): return
            if self.shutdown.wait(self.delay): return
