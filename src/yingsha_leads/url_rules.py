"""Canonical public-page identities. No authentication material is retained."""
import ipaddress
import base64
import binascii
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit, unquote, urljoin

SOCIAL = {'facebook.com', 'instagram.com', 'fb.com', 'fb.watch', 'instagr.am', 'linkedin.com', 'tiktok.com', 'x.com', 'twitter.com'}
SEARCH = {'google.com', 'bing.com', 'duckduckgo.com', 'googleadservices.com', 'googleusercontent.com', 'gstatic.com'}
TRACKING = {'fbclid', 'gclid', 'igsh', 'igshid', 'ref', 'ref_src', 'ved', 'sa', 'mibextid'}
SENSITIVE = re.compile(r'(?:token|password|passwd|secret|session|auth|code|cookie|signature|key)', re.I)
PRIVATE_PATH = re.compile(r'/(?:admin|wp-admin|login|logout|signin|signup|accounts|account|password|reset-password|oauth|auth|direct|messages|messenger|inbox|settings|checkpoint|challenge|followers|following|groups|members)(?:/|$)', re.I)


def host_of(url):
    return (urlsplit(url).hostname or '').lower().removeprefix('www.')


def channel_of(url):
    h=host_of(url)
    for domain in SOCIAL:
        if h==domain or h.endswith('.'+domain): return domain.split('.')[0]
    return 'website'


def is_social(url):
    return channel_of(url)!='website'


def is_search(url):
    h=host_of(url)
    return any(h==d or h.endswith('.'+d) for d in SEARCH) or bool(re.fullmatch(r'(?:www\.)?google\.[a-z.]{2,10}', h))


def normalize_url(value):
    if not isinstance(value,str) or len(value)>2048: raise ValueError('网址过长或格式无效')
    value=value.strip()
    if any(ord(c)<32 for c in value) or '\\' in value: raise ValueError('网址包含无效字符')
    p=urlsplit(value)
    if p.scheme.lower() not in {'http','https'} or not p.hostname or p.username or p.password:
        raise ValueError('只支持不含密码的 HTTP/HTTPS 公共网页')
    try:
        host=p.hostname.encode('idna').decode('ascii').lower().rstrip('.')
        port=p.port
    except (ValueError,UnicodeError): raise ValueError('网址主机无效') from None
    if port not in (None,80,443): raise ValueError('仅支持网站标准端口 80/443')
    if '.' not in host or host.endswith(('.local','.localhost','.internal','.lan')) or host=='localhost':
        raise ValueError('不采集本机和内网地址')
    try: ip=ipaddress.ip_address(host)
    except ValueError:
        if re.fullmatch(r'[0-9xXa-fA-F.]+',host): raise ValueError('不支持数字形式的主机')
    else:
        if not ip.is_global: raise ValueError('不采集本机和内网地址')
    path=p.path or '/'
    if PRIVATE_PATH.search(unquote(path)): raise ValueError('请选择企业展示页面，不采集账户或管理页面')
    pairs=parse_qsl(p.query,keep_blank_values=True)
    if any(SENSITIVE.search(k) for k,v in pairs): raise ValueError('网址包含认证或敏感参数，请使用公开页面地址')
    pairs=[(k,v) for k,v in pairs if not k.lower().startswith(('utm_','__cft__','__tn__','__xts__')) and k.lower() not in TRACKING]
    if host in {'instagram.com','www.instagram.com','m.instagram.com'}: host='www.instagram.com'
    if host in {'facebook.com','www.facebook.com','m.facebook.com','mbasic.facebook.com'}: host='www.facebook.com'
    return urlunsplit((p.scheme.lower(),host,path,urlencode(pairs),''))


def identity_key(url):
    url=normalize_url(url); p=urlsplit(url)
    if is_social(url): return host_of(url)+p.path.rstrip('/').lower()+('?' + p.query if p.query else '')
    return host_of(url)


def unwrap_link(base,href):
    raw=urljoin(base,href)
    p=urlsplit(raw); q=dict(parse_qsl(p.query))
    if (is_search(raw) and p.path in {'/url','/imgres'}) or host_of(raw) in {'l.facebook.com','lm.facebook.com','l.instagram.com'}:
        raw=q.get('q') or q.get('url') or q.get('u') or raw
    elif host_of(raw)=='bing.com' and p.path.rstrip('/')=='/ck/a':
        encoded=q.get('u','')
        if encoded.startswith('a1'):
            try:
                token=encoded[2:]
                raw=base64.b64decode(token+'='*(-len(token)%4),altchars=b'-_',validate=True).decode('utf-8')
            except (ValueError,UnicodeError,binascii.Error): raise ValueError('搜索结果链接无效') from None
    return normalize_url(raw)


def candidate_url(url):
    url=normalize_url(url); p=urlsplit(url); path=p.path.strip('/').lower()
    if is_search(url): return False
    if re.search(r'\.(?:pdf|jpg|png|gif|zip|mp4|svg|css|js|webp)$',p.path,re.I): return False
    if is_social(url):
        if not path: return False
        if path.split('/')[0] in {'p','reel','reels','explore','stories','search','watch','groups','share','sharer.php','sharer','intent','hashtag','tags','followers','following','help','policies','privacy','legal','terms'}: return False
        if any(x in path.split('/') for x in ('posts','videos','photos','followers','following')): return False
    return True
