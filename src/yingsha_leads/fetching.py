"""Bounded public HTTP fetches, robots checks, and guarded connections."""
import ipaddress
import re
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit,urljoin
from urllib.robotparser import RobotFileParser
import httpx
import httpcore
from httpcore import SyncBackend

from .url_rules import normalize_url,host_of
from .extract import page_gate

USER_AGENT='YingshaLeadFinder/0.1 (business website research)'
FAKE_RANGE=ipaddress.ip_network('198.18.0.0/15')


class FetchError(RuntimeError):
    def __init__(self,message,blocked=False): super().__init__(message); self.blocked=blocked


def mac_fake_ip(ip):
    if sys.platform!='darwin': return False
    try:
        reply=subprocess.run(['/sbin/route','-n','get',ip],capture_output=True,text=True,timeout=2,shell=False)
        return reply.returncode==0 and bool(re.search(r'interface:\s+utun\d+',reply.stdout)) and bool(re.search(r'gateway:\s+198\.(?:18|19)\.',reply.stdout))
    except (OSError,subprocess.TimeoutExpired): return False


def resolve(host): return sorted({x[4][0] for x in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)})


def checked_ips(host,resolver=resolve,fake_ip_check=mac_fake_ip):
    try: ips=resolver(host)
    except OSError: raise FetchError('域名解析失败，请检查网络') from None
    if not ips: raise FetchError('域名没有可用地址')
    for address in ips:
        ip=ipaddress.ip_address(address)
        if ip.is_global: continue
        if ip.version==4 and ip in FAKE_RANGE and fake_ip_check(address): continue
        raise ValueError('目标解析到内网或不支持的代理地址，请改用已授权浏览器采集')
    return ips


def validate_network(url,resolver=resolve,fake_ip_check=mac_fake_ip):
    url=normalize_url(url)
    checked_ips(urlsplit(url).hostname,resolver,fake_ip_check)
    return url


class GuardedBackend(SyncBackend):
    def connect_tcp(self,host,port,timeout=None,local_address=None,socket_options=None):
        # Resolve/check immediately at connection time and connect the checked IP;
        # TLS keeps the original hostname via httpcore's TLS upgrade.
        ips=checked_ips(host)
        return super().connect_tcp(ips[0],port,timeout,local_address,socket_options)


class PageFetcher:
    def __init__(self):
        transport=httpx.HTTPTransport(retries=0)
        transport._pool._network_backend=GuardedBackend()
        self.client=httpx.Client(transport=transport,timeout=httpx.Timeout(15,connect=8),trust_env=False,headers={'User-Agent':USER_AGENT,'Accept':'text/html,application/xhtml+xml'})
        self.robot_cache={}; self.cache={}; self.last={}

    def close(self): self.client.close()

    def _rules(self,origin):
        if origin not in self.robot_cache:
            _,body,status=self._request(origin+'/robots.txt',robots=True)
            rules=RobotFileParser(); rules.set_url(origin+'/robots.txt')
            if status==404: rules.allow_all=True
            else: rules.parse(body.splitlines())
            self.robot_cache[origin]=rules
        return self.robot_cache[origin]

    def _request(self,url,robots=False):
        for _ in range(4):
            url=validate_network(url)
            p=urlsplit(url); origin=p.scheme+'://'+p.netloc
            delay=3
            if not robots:
                rules=self._rules(origin)
                if not rules.can_fetch(USER_AGENT,url): raise FetchError('该网站 robots.txt 不允许此路径采集',blocked=True)
                rate=rules.request_rate(USER_AGENT)
                delay=max(delay,rules.crawl_delay(USER_AGENT) or 0,rate.seconds/rate.requests if rate and rate.requests else 0)
                if delay>60: raise FetchError('网站要求较长抓取间隔，本次任务暂停',blocked=True)
            wait=delay-(time.monotonic()-self.last.get(origin,0))
            if wait>0: time.sleep(wait)
            try:
                with self.client.stream('GET',url,follow_redirects=False) as response:
                    if response.status_code in {301,302,303,307,308}:
                        location=response.headers.get('location')
                        if not location: raise FetchError('网站跳转地址缺失')
                        new=normalize_url(urljoin(url,location))
                        if host_of(new)!=host_of(url): raise FetchError('网站跳转到了其他域名，请核实后添加最终官网地址')
                        url=new; continue
                    if response.status_code in {401,403,429}: raise FetchError('网站要求登录、拒绝访问或限制频率；任务已暂停',blocked=True)
                    if robots and response.status_code==404: return url,'',404
                    if response.status_code>=400: raise FetchError(f'网站返回 HTTP {response.status_code}，未采集')
                    ctype=response.headers.get('content-type','').lower()
                    if not robots and not any(x in ctype for x in ['text/html','application/xhtml+xml']): raise FetchError('目标不是 HTML 网页，不下载附件')
                    limit=512000 if robots else 2_000_000
                    content=bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content)>limit: raise FetchError('页面超过采集大小上限')
                    return url,bytes(content).decode(response.encoding or 'utf-8',errors='replace'),response.status_code
            except (httpx.HTTPError,OSError,httpcore.NetworkError,httpcore.TimeoutException): raise FetchError('网络请求失败，请检查连接或改用浏览器采集') from None
            finally: self.last[origin]=time.monotonic()
        raise FetchError('网站跳转次数过多，未采集')

    def fetch(self,url):
        url=normalize_url(url)
        if url in self.cache and time.monotonic()-self.cache[url][0]<86400: return self.cache[url][1]
        final,html,_=self._request(url)
        gate=page_gate(final,html)
        if gate: raise FetchError(gate,blocked=True)
        result={'url':final,'html':html}
        self.cache[url]=(time.monotonic(),result)
        if len(self.cache)>200: self.cache.pop(next(iter(self.cache)))
        return result
