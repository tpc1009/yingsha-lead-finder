"""Deterministic Scrapling parsing; no language model or remote enrichment."""
import re
from urllib.parse import urlsplit, urljoin, urlencode
from lxml import html as lhtml
from scrapling import Selector
from .url_rules import normalize_url, host_of, channel_of, is_social, is_search, candidate_url, unwrap_link

EMAIL=re.compile(r'(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,24}\b',re.I)
COUNTRIES={'United Arab Emirates':r'\b(?:United Arab Emirates|UAE|Dubai|Abu Dhabi)\b', 'Saudi Arabia':r'\b(?:Saudi Arabia|Riyadh|Jeddah|KSA)\b', 'United Kingdom':r'\b(?:United Kingdom|London)\b', 'United States':r'\b(?:United States|USA)\b', 'Turkey':r'\b(?:Turkey|Türkiye|Istanbul)\b', 'China':r'\b(?:China|Suzhou|Shanghai)\b', 'India':r'\b(?:India|Mumbai|Delhi)\b', 'Pakistan':r'\b(?:Pakistan|Karachi|Lahore)\b', 'Bangladesh':r'\b(?:Bangladesh|Dhaka)\b'}
ALIASES={'阿巴亚':'abaya','雪纺':'chiffon','针织':'knit','真丝':'silk','弹力':'stretch','面料':'fabric','棉':'cotton'}
ROLES={'自有品牌':'private label','生产制造':'manufacturer','工厂':'factory','批发业务':'wholesale','定制业务':'custom','进口业务':'importer'}


def clean_selector(html):
    if not html or len(html)>3_000_000: raise ValueError('页面为空或过大，请选具体企业页面')
    root=lhtml.fromstring(html)
    for node in root.xpath('//script|//style|//noscript|//template|//*[@hidden]|//*[@aria-hidden="true"]'):
        node.drop_tree()
    for node in root.xpath('//*[@style]'):
        if re.search(r'display\s*:\s*none|visibility\s*:\s*hidden',node.get('style',''),re.I): node.drop_tree()
    return Selector(lhtml.tostring(root,encoding='unicode'),adaptive=False)


def text_content(sel):
    return re.sub(r'\s+',' ',sel.get_all_text(separator=' ',strip=True)).strip()


def page_gate(url,html):
    path=urlsplit(url).path.lower()
    if re.search(r'/(login|accounts/login|checkpoint|challenge|signin)(/|$)',path): return '需要登录，请在浏览器完成后继续'
    raw=Selector(html)
    title=raw.css('title::text').get('') or ''
    cleaned=clean_selector(html)
    body=text_content(cleaned)[:20000]
    if re.search(r'(?:do not|don.t) have permission to (?:view|access)|you (?:can.t|cannot) access this (?:page|content)|您无权|你无权|没有权限(?:查看|访问)',body,re.I):
        return '页面没有查看权限，请在浏览器检查后继续'
    if cleaned.css('input[type="password"]') or re.search(r'just a moment|access denied|verify you are human|security check|unusual traffic',title,re.I):
        return '需要登录或安全验证，请在浏览器处理后继续'
    if re.search(r'verify you are human|confirm you are human|our systems have detected unusual traffic|temporarily blocked|we restrict certain activity|too many requests|请完成安全验证|请验证您是真人|检测到异常流量|请求过于频繁|操作过于频繁',body,re.I):
        return '页面要求安全验证，任务已暂停'
    if len(body)<30: return '页面内容不足，可能尚未加载或需要登录'
    return ''


def extract_candidates(url,html):
    sel=clean_selector(html); result=[]; seen={}
    for link in sel.css('a[href]'):
        try: dest=unwrap_link(url,link.attrib.get('href',''))
        except ValueError: continue
        if not candidate_url(dest): continue
        title=text_content(link)[:180]
        if dest in seen:
            if title and seen[dest]['title']==host_of(dest): seen[dest]['title']=title
            continue
        if not is_search(url) and not is_social(url) and identity_key_local(dest)==identity_key_local(url): continue
        item={'url':dest,'title':title or host_of(dest),'channel':channel_of(dest)}
        seen[dest]=item; result.append(item)
        if len(result)>=100: break
    return result


def identity_key_local(url):
    from .url_rules import identity_key
    return identity_key(url)


def plan_search(product,buyer_type,country):
    if not product.strip(): raise ValueError('请填写产品关键词')
    buyer_map={'服装品牌':'clothing brand','制衣厂':'garment manufacturer','面料进口商':'fabric importer','批发商':'wholesaler','面料批发商':'fabric wholesaler'}
    product=' '.join(ALIASES.get(t,t) for t in product.split())
    basic=' '.join([product.strip(),buyer_map.get(buyer_type,buyer_type).strip(),country.strip()]).strip()
    entries=[
        ('facebook','Facebook 主页搜索','https://www.facebook.com/search/pages',basic),
        ('web','Google 搜索企业官网','https://www.google.com/search',basic),
        ('instagram','Google 查找 Instagram','https://www.google.com/search',basic+' site:instagram.com'),
    ]
    return [{'kind':kind,'channel':name,'query':q,'url':base+'?'+urlencode({'q':q})} for kind,name,base,q in entries]


def extract_lead(url,html,product='',country=''):
    url=normalize_url(url); sel=clean_selector(html); text=text_content(sel)
    headings=sel.css('h1')
    names=[sel.css('meta[property="og:site_name"]::attr(content)').get(''),text_content(headings[0]) if headings else '',sel.css('meta[property="og:title"]::attr(content)').get(''),sel.css('title::text').get('')]
    if is_social(url): names=names[1:]+names[:1]
    names=[re.sub(r'^\(\d+\)\s*','',n.strip()) for n in names if n]
    names=[n for n in names if n and n.strip().lower() not in {'instagram','facebook','linkedin','tiktok','twitter','x'}]
    name_source='页面标题或标题区域' if names else '未识别企业名称，请核对来源'
    # Some Facebook business pages render their display name as a button with
    # no h1/OG metadata. Use the visible profile intro only, and label this
    # fallback for human review rather than save "Facebook" as the company.
    if not names and is_social(url):
        bodies=sel.css('body')
        lines=[s.strip() for s in (bodies[0].get_all_text(separator='\n',strip=True) if bodies else '').splitlines() if s.strip()]
        if lines and 2<=len(lines[0])<=120 and re.search(r'followers|粉丝',' '.join(lines[:8]),re.I):
            names=[lines[0]]; name_source='页面简介开头（请核对）'
    name=next((n.strip() for n in names if n and n.strip()),host_of(url))[:200]
    name=re.split(r'\s[|–—]\s',name)[0]
    desc=sel.css('meta[name="description"]::attr(content)').get('') or ''
    searchable=text+' '+desc
    emails=set(EMAIL.findall(searchable)); phones=set(); whatsapp=set(); socials=set(); external=[]; contacts=[]
    if is_social(url): socials.add(url)
    for a in sel.css('a[href]'):
        href=a.attrib.get('href','')
        if href.lower().startswith('mailto:'): emails.update(EMAIL.findall(href.split('?',1)[0]))
        if href.lower().startswith('tel:'):
            phone=re.sub(r'[^+0-9]','',href[4:])
            if 7<=len(phone.lstrip('+'))<=16: phones.add(phone)
        try: link=unwrap_link(url,href)
        except ValueError: continue
        h=host_of(link)
        if h in {'wa.me','api.whatsapp.com','web.whatsapp.com'}:
            whatsapp.add(link); continue
        if is_social(link) and candidate_url(link): socials.add(link)
        elif not is_search(link) and candidate_url(link):
            if host_of(link)!=host_of(url):
                label=text_content(a)
                explicit_website=bool(re.search(r'\b(?:our website|official website|company website|brand website|visit our site)\b|官网|官方网站',label,re.I)) or a.attrib.get('rel')=='me'
                if explicit_website and not any(h==d or h.endswith('.'+d) for d in ['meta.com','linktr.ee','facebook.net','youtube.com','youtu.be','threads.net','threads.com']): external.append(link)
            elif re.search(r'contact|about|联系|关于',a.attrib.get('href','')+' '+text_content(a),re.I): contacts.append(link)
    # Only labelled visible telephone text; avoid treating dates/order numbers as phones.
    for match in re.finditer(r'(?:phone|tel(?:ephone)?|call|电话)\s*[:：]?\s*(\+?[0-9][0-9 ()-]{6,24}[0-9])',searchable,re.I):
        n=re.sub(r'[^+0-9]','',match.group(1))
        if 7<=len(n.lstrip('+'))<=16: phones.add(n)
    for match in re.finditer(r'(?<![\w+])\+[1-9][0-9 ()-]{6,24}[0-9](?!\d)',searchable):
        n=re.sub(r'[^+0-9]','',match.group(0))
        if 7<=len(n.lstrip('+'))<=15: phones.add(n)
    website=urlsplit(url).scheme+'://'+urlsplit(url).netloc+'/' if not is_social(url) else ''
    if is_social(url) and len({host_of(x) for x in external})==1: website=external[0]
    tokens=[]
    for term in re.split(r'[,，;；\s]+',product.strip().lower()):
        if len(term)>=2: tokens.extend([term,ALIASES.get(term,term)])
    keywords=sorted({t for t in tokens if re.search(r'(?<!\w)'+re.escape(t)+r'(?!\w)',searchable,re.I) or (re.search('[\u4e00-\u9fff]',t) and t in searchable)})
    reasons=[f'页面出现产品词：{k}' for k in keywords]
    for label,word in ROLES.items():
        if re.search(r'\b'+re.escape(word)+r'\b',searchable,re.I): reasons.append(f'{label}证据：{word}')
    evidence=[]
    for label,pattern in COUNTRIES.items():
        m=re.search(pattern,searchable,re.I)
        if m: evidence.append(f'{label}（页面提及 {m.group(0)}）')
    score=min(100,len(keywords)*15+(len(reasons)-len(keywords))*12+(10 if emails or phones or whatsapp else 0))
    return {'name':name,'name_source':name_source,'website':website,'source_url':url,'channel':channel_of(url),
        'emails':sorted({e.lower() for e in emails if not e.lower().endswith(('.png','.jpg','.webp','.svg'))})[:20],
        'phones':sorted(phones)[:20],'whatsapp':sorted(whatsapp)[:10],'social_links':sorted(socials)[:20],
        'keywords':keywords,'score':score,'reasons':reasons,'country_evidence':'；'.join(evidence),
        'target_country':country.strip()[:100],'contact_pages':list(dict.fromkeys(contacts))[:2]}
