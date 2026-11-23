import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time

from .url_rules import normalize_url
from .desktop_runtime import cli_environment


class BrowserError(RuntimeError):
    def __init__(self,message,code=''):
        super().__init__(message)
        self.code=code


class BrowserBridge:
    def __init__(self,runner=None):
        self.runner=runner; self.lock=threading.RLock()

    def _run(self,action,*args):
        if action not in {'tabs','evaluate','navigate'}: raise ValueError('不允许的浏览器操作')
        environment=cli_environment()
        executable=shutil.which('openclaw',path=environment['PATH'])
        if not self.runner and not executable:
            raise BrowserError('未找到 OpenClaw。请确认已安装 OpenClaw 和 Node，并重新打开工具。')
        command=['openclaw' if self.runner else executable,'browser','--browser-profile','chrome','--timeout','30000',action,*args,'--json']
        try:
            if self.runner: output=self.runner(command)
            else:
                proc=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=40,shell=False,env=environment)
                if proc.returncode:
                    if 'blocked by policy' in proc.stderr:
                        raise BrowserError('OpenClaw 未允许访问该域名；请检查精确域名许可后继续。工具不会更改浏览器权限。')
                    raise BrowserError('无法连接 OpenClaw，请启动网关并在 Chrome 扩展里授权目标标签页')
                output=proc.stdout
            data=json.loads(output)
        except (OSError,subprocess.TimeoutExpired): raise BrowserError('浏览器连接超时或 OpenClaw 未安装，请检查网关和扩展') from None
        except (ValueError,TypeError): raise BrowserError('浏览器返回格式不兼容，请检查 OpenClaw 和扩展版本') from None
        if not isinstance(data,dict) or data.get('ok') is False or data.get('error') or data.get('blockedByDialog'):
            raise BrowserError('浏览器读取受阻，请检查当前页面、登录状态和弹窗后继续')
        return data

    def probe(self):
        try:
            data=self._run('tabs'); tabs=[]
            if not isinstance(data.get('tabs'),list): raise BrowserError('浏览器标签页响应格式不兼容')
            for t in data['tabs']:
                if not isinstance(t,dict): continue
                tid=t.get('targetId') or t.get('id')
                if not tid: continue
                url=t.get('url') or ''
                if url:
                    try: url=normalize_url(url)
                    except ValueError: continue
                tabs.append({'targetId':tid,'title':str(t.get('title') or '已授权标签页')[:180],'url':url})
            return {'connected':bool(tabs),'profile':'chrome','tabs':tabs,'message':'请选择本次采集的标签页' if tabs else '未接入可采集标签页。请在 Chrome 登录目标网站，并在 OpenClaw 扩展里授权该标签页。'}
        except BrowserError as exc: return {'connected':False,'profile':'chrome','tabs':[],'message':str(exc)}

    @staticmethod
    def validate_target(target):
        if not isinstance(target,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}',target): raise ValueError('请先选择已授权的浏览器标签页')

    def capture(self,target):
        self.validate_target(target)
        script=(Path(__file__).parent/'scripts/capture.js').read_text(encoding='utf-8')
        with self.lock: data=self._run('evaluate','--target-id',target,'--fn',script)
        result=data.get('result')
        if data.get('ok') is not True or not isinstance(result,dict): raise BrowserError('没有取得有效页面，请检查扩展连接')
        errors={'SECURITY_GATE':'页面需要登录或安全验证，请在浏览器完成后继续','PRIVATE_PAGE':'请选择企业主页，不读取私信、账户或成员名单','NOT_COMPANY_PAGE':'当前是社交平台首页，请打开具体公开企业主页后采集','SOCIAL_DIALOG':'社交页面有弹层，请手动关闭弹层并确认企业主页后采集','NOT_READY':'页面尚未加载完成，请稍后采集','PAGE_TOO_LARGE':'页面内容过多，请打开具体企业主页后采集'}
        if result.get('error'): raise BrowserError(errors.get(result['error'],'页面暂时无法读取'),code=result['error'])
        try: url=normalize_url(result.get('url',''))
        except ValueError as exc: raise BrowserError(str(exc)) from None
        if not isinstance(result.get('html'),str) or not result['html'] or len(result['html'])>1_500_000: raise BrowserError('页面内容为空或超出范围')
        return {'url':url,'html':result['html']}

    def visit(self,target,url):
        self.validate_target(target); url=normalize_url(url)
        with self.lock:
            navigation=self._run('navigate',url,'--target-id',target)
            try: destination=normalize_url(navigation.get('url') or url)
            except ValueError as exc: raise BrowserError('页面跳转到登录或非公开页面，请在浏览器检查后继续') from exc
            for attempt in range(3):
                if attempt: time.sleep(1)
                try:
                    page=self.capture(target)
                    if page['url']!=destination:
                        raise BrowserError('页面仍在跳转或所选标签页已被切换，请检查后继续',code='NOT_READY')
                    return page
                except BrowserError as exc:
                    if exc.code!='NOT_READY' or attempt==2: raise
