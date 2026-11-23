from contextlib import asynccontextmanager
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from fastapi import FastAPI,Request,HTTPException
from fastapi.responses import FileResponse,JSONResponse,Response
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel,Field

from . import __version__
from .browser import BrowserBridge,BrowserError
from .store import Store
from .jobs import JobManager
from .extract import extract_candidates,extract_lead,plan_search,page_gate
from .url_rules import is_search,candidate_url
from .exporting import export_leads


def default_data_dir():
    return Path.home()/'Library/Application Support/Yingsha Lead Finder'


class SearchInput(BaseModel):
    product:str=Field(default='',max_length=200)
    buyer_type:str=Field(default='',max_length=120)
    country:str=Field(default='',max_length=100)


class CaptureInput(SearchInput):
    target_id:str=Field(max_length=120)
    kind:str='lead'


class JobInput(SearchInput):
    urls:list[str]=Field(min_length=1,max_length=100)
    mode:str='http'
    target_id:str=Field(default='',max_length=120)


class EditInput(BaseModel):
    status:str
    notes:str=Field(default='',max_length=5000)


class FacebookNextInput(BaseModel):
    target_id:str=Field(min_length=1,max_length=120)


class JobControlInput(BaseModel):
    target_id:str=Field(default='',max_length=120)


def create_app(data_dir=None,browser=None,fetcher=None,delay=5):
    folder=Path(data_dir) if data_dir else default_data_dir()
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    store=Store(folder/'leads.sqlite'); store.path.chmod(0o600)
    bridge=browser or BrowserBridge(); csrf=secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        app.state.manager=JobManager(store,browser=bridge,fetcher=fetcher,delay=delay)
        yield
        app.state.manager.close()

    app=FastAPI(title='颖纱客户线索工具',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.state.store=store

    @app.middleware('http')
    async def local_only(request,call_next):
        host=request.headers.get('host','')
        try: hostname=urlsplit('http://'+host).hostname
        except ValueError: hostname=''
        if hostname not in {'127.0.0.1','localhost'}: return JSONResponse({'detail':'仅允许本机访问'},status_code=403)
        origin=request.headers.get('origin')
        if origin and origin!=str(request.base_url).rstrip('/'):
            return JSONResponse({'detail':'不允许跨站访问本地工具'},status_code=403)
        if request.headers.get('sec-fetch-site')=='cross-site':
            return JSONResponse({'detail':'不允许跨站访问本地工具'},status_code=403)
        if request.method not in {'GET','HEAD','OPTIONS'} and not secrets.compare_digest(request.headers.get('x-csrf-token',''),csrf):
            return JSONResponse({'detail':'页面凭证失效，请刷新工具页面后重试'},status_code=403)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers['Cache-Control']='no-store'
        return response

    @app.exception_handler(ValueError)
    async def bad_value(request,exc): return JSONResponse({'detail':str(exc)},status_code=400)

    @app.exception_handler(BrowserError)
    async def browser_error(request,exc): return JSONResponse({'detail':str(exc)},status_code=503)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):
        return JSONResponse({'detail':'填写内容格式或长度不符合要求：备注最多5000字，每次最多100个网址，请检查表单'},status_code=400)

    static=Path(__file__).parent/'static'
    app.mount('/static',StaticFiles(directory=static),name='static')

    @app.get('/')
    def home(): return FileResponse(static/'index.html')

    @app.get('/api/status')
    def status():
        jobs=store.jobs()
        return {'app':'yingsha-lead-finder','version':__version__,'model_calls':0,'engine':'Scrapling 0.5',
            'csrf_token':csrf,'data_dir':str(folder),'stats':{'leads':store.list_leads()['total'],'pending':sum(sum(i['status']=='pending' for i in j['items']) for j in jobs if j['status'] not in {'stopped','completed'}),'running':sum(j['status'] in {'queued','running'} for j in jobs)}}

    @app.get('/api/browser')
    def browser_status(): return bridge.probe()

    @app.post('/api/search')
    def search(data:SearchInput): return {'queries':plan_search(data.product,data.buyer_type,data.country)}

    @app.post('/api/capture')
    def capture(data:CaptureInput):
        if data.kind not in {'candidates','lead'}: raise ValueError('采集类型无效')
        manager=app.state.manager
        with manager.lock:
            if manager.browser_busy(): raise HTTPException(409,'浏览器批量任务正在使用标签页，请先暂停任务并等当前页完成')
            page=bridge.capture(data.target_id)
        gate=page_gate(page['url'],page['html'])
        if gate: raise BrowserError(gate)
        if data.kind=='candidates': return {'candidates':extract_candidates(page['url'],page['html'])}
        if is_search(page['url']) or not candidate_url(page['url']): raise ValueError('请打开具体企业主页；搜索结果请使用“提取候选”')
        lead=store.save_lead(extract_lead(page['url'],page['html'],data.product,data.country))
        return {'lead':lead,'message':lead.get('merge_notice') or '已保存公开资料；同一企业自动合并，采购资格仍需核实'}

    @app.post('/api/jobs')
    def create_job(data:JobInput):
        return {'job':app.state.manager.create(data.urls,data.mode,data.target_id,data.product,data.country)}

    @app.get('/api/jobs')
    def jobs(): return {'jobs':store.jobs()}

    @app.get('/api/facebook/sessions')
    def facebook_sessions(): return {'sessions':app.state.manager.facebook.list()}

    @app.post('/api/facebook/import')
    def import_facebook(data:CaptureInput):
        session=app.state.manager.import_facebook(data.target_id,data.product,data.buyer_type,data.country)
        return {'session':session}

    @app.post('/api/facebook/sessions/{sid}/next')
    def next_facebook(sid:int,data:FacebookNextInput):
        manager=app.state.manager
        job=manager.next_facebook(sid,data.target_id)
        return {'job':job,'session':manager.facebook.get(sid)}

    @app.post('/api/facebook/sessions/{sid}/retry')
    def retry_facebook(sid:int,data:FacebookNextInput):
        manager=app.state.manager
        job=manager.next_facebook(sid,data.target_id,retry=True)
        return {'job':job,'session':manager.facebook.get(sid)}

    @app.post('/api/jobs/{jid}/{action}')
    def control(jid:int,action:str,data:JobControlInput|None=None):
        manager=app.state.manager
        with manager.lock:
            job=store.get_job(jid)
            if action=='resume' and job['mode']=='facebook':
                if data is None or data.target_id!=job['target_id']:
                    raise ValueError('请连接并选择本批原来的浏览器标签页后继续；更换标签页需先停止本批')
            return {'job':manager.control(jid,action)}

    @app.get('/api/leads')
    def leads(q:str='',status:str='',page:int=1): return store.list_leads(q[:200],status,page)

    @app.get('/api/leads/{lid}')
    def lead(lid:int): return {'lead':store.get_lead(lid)}

    @app.patch('/api/leads/{lid}')
    def edit(lid:int,data:EditInput): return {'lead':store.update_lead(lid,data.status,data.notes)}

    @app.get('/api/export')
    def export(format:str='xlsx',q:str='',status:str=''):
        rows=store.list_leads(q[:200],status,all_rows=True)['items']
        data=export_leads(rows,format)
        media='text/csv; charset=utf-8' if format=='csv' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        return Response(data,media_type=media,headers={'Content-Disposition':f'attachment; filename="yingsha-leads.{format}"'})

    return app
