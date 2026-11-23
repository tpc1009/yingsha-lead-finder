"""Bounded Facebook discovery and explainable business screening, without models."""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit, unquote

from .extract import clean_selector, text_content
from .url_rules import normalize_url, host_of

RESERVED = {'me','home.php','friends','events','marketplace','gaming','watch','reel','reels',
            'photo','photo.php','photos','video','videos','story.php','stories','permalink.php',
            'posts','groups','search','share','sharer','sharer.php','help','policies','privacy',
            'terms','legal','login','recover','settings','pages','people','business','ads',
            'notifications','bookmarks','saved','fundraisers','messages','about','directory'}


def is_facebook_profile(url):
    try:
        p=urlsplit(normalize_url(url))
    except ValueError:
        return False
    if p.hostname!='www.facebook.com': return False
    path=unquote(p.path).strip('/').lower()
    query=dict(parse_qsl(p.query))
    if path=='profile.php':
        return set(query)=={'id'} and bool(re.fullmatch(r'\d+',query['id']))
    if query: return False
    parts=path.split('/')
    if len(parts)==3 and parts[0]=='pages':
        return bool(parts[1] and parts[2].isdigit())
    return len(parts)==1 and path not in RESERVED and bool(re.fullmatch(r'[a-z0-9._-]+',path))


def profile_url(url):
    url=normalize_url(url)
    if not is_facebook_profile(url): raise ValueError('只接受 Facebook 公开主页候选')
    p=urlsplit(url)
    return urlunsplit(('https','www.facebook.com',p.path.rstrip('/') or '/',p.query,''))


def search_source(url):
    """Keep query identity, dropping pagination so loaded result pages can append."""
    p=urlsplit(normalize_url(url)); host=host_of(url)
    query=dict(parse_qsl(p.query)); path=p.path.rstrip('/')
    fb=host=='facebook.com' and path=='/search/pages'
    engine=(host=='bing.com' or bool(re.fullmatch(r'google\.[a-z.]{2,10}',host))) and path=='/search'
    if not (fb or engine) or not query.get('q','').strip():
        raise ValueError('请连接 Google 搜索结果或 Facebook 公共主页（Pages）搜索结果页')
    keys=('q','filters') if fb else ('q','hl','gl','lr','cr')
    canonical=urlunsplit(('https',p.netloc,path,urlencode(sorted((k,query[k]) for k in keys if query.get(k))),''))
    return canonical


ROLE_WORDS = {
    '服装品牌':r'clothing|fashion|boutique|apparel|garment|designer|服装|服饰|时装|品牌',
    '制衣厂':r'garment\s+(?:manufacturer|factory)|clothing\s+(?:manufacturer|factory)|manufacturer|factory|制衣|服装厂',
    '面料进口商':r'importer|importing|imports|进口',
    '批发商':r'wholesale|wholesaler|批发',
}
ROLE_ALIASES={'clothing brand':'服装品牌','garment manufacturer':'制衣厂','fabric importer':'面料进口商','wholesaler':'批发商','面料批发商':'批发商'}
BUSINESS=r'clothing|fashion|apparel|textile|fabric|boutique|garment|manufacturer|wholesale|company|business service|shopping|retail|store|supplier|importer|restaurant|cafe|bakery|real estate|服装|纺织|面料|批发|商店|公司|餐厅|零售'
UNRELATED=r'\b(?:restaurant|pizzeria|bakery|dentist|real estate agency|car dealership)\b|餐厅|牙科|房地产'


def classify(lead, html, buyer_type):
    sel=clean_selector(html)
    text=text_content(sel)[:25000]
    lower=text.casefold()
    # Keep text-node boundaries: a later personal bio must not become a category.
    lines=[line.strip() for line in sel.get_all_text(separator='\n',strip=True).splitlines() if line.strip()]
    category=''
    for index,line in enumerate(lines):
        marker=re.fullmatch(r'(?:page\s*[·•:：\-]|公共主页\s*[·•:：\-]?)\s*(.{0,100})',line,re.I)
        if marker:
            category=marker.group(1).strip() or (lines[index+1] if index+1<len(lines) else '')
            break
    personal=bool(re.search(r'personal blog|personal profile|个人博客|个人主页|digital creator|数字创作者',lower))
    business=bool(category and re.search(BUSINESS,category,re.I))
    if not business:
        # Business language alone in an individual's bio is insufficient.
        business=bool(re.search(r'\b(?:we (?:supply|manufacture|sell)|our (?:company|store|brand|factory)|official (?:business|store|brand))\b|本公司|本店',lower)
                      and re.search(BUSINESS,lower,re.I))
    if personal or not business:
        return {'decision':'skipped','reason':'未找到足够的企业主页证据，未保存个人资料'}
    if re.search(UNRELATED,category or text,re.I):
        return {'decision':'skipped','reason':'页面业务类别明显不属于本次面料或服装客户范围'}
    role=ROLE_ALIASES.get(buyer_type.strip().casefold(),buyer_type.strip())
    pattern=ROLE_WORDS.get(role)
    # A fabric manufacturer is not evidence of a garment factory or clothing brand.
    fabric_only=bool(re.search(r'fabric|textile|面料|纺织',lower)) and not re.search(r'clothing|garment|apparel|fashion|服装|服饰',lower)
    role_match=bool(pattern and re.search(pattern,lower,re.I))
    if fabric_only and role in {'服装品牌','制衣厂'}: role_match=False
    has_product=any(k not in {'fabric','fabrics','面料'} for k in lead.get('keywords',[]))
    if has_product and role_match:
        return {'decision':'matched','reason':'具有企业证据，页面产品词和客户类型匹配；采购需求仍待确认'}
    return {'decision':'review','reason':'具有企业证据，但产品或客户类型信息不足，需后续核实'}
