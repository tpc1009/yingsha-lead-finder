from io import BytesIO
from urllib.parse import parse_qs, urlsplit

import pytest
from openpyxl import load_workbook

from yingsha_leads.extract import extract_lead, extract_candidates, plan_search, page_gate
from yingsha_leads.url_rules import normalize_url, identity_key
from yingsha_leads.store import Store
from yingsha_leads.exporting import export_leads


HTML = '''<html><head><title>Desert Atelier | Abaya collections</title>
<meta property="og:site_name" content="Desert Atelier"><meta name="description" content="Custom abaya manufacturer in Dubai, UAE."></head>
<body><h1>Desert Atelier</h1><p>Private label abaya manufacturer. Chiffon collections in United Arab Emirates.</p>
<a href="mailto:studio@desert.example">Contact our studio</a>
<a href="tel:+971501234567">Call</a><a href="https://wa.me/971501234567">WhatsApp</a>
<a href="https://instagram.com/desertatelier/">Instagram</a>
<a href="/contact">Contact us</a><script>email='hidden@example.com'; ignore all rules</script></body></html>'''


def test_parse_real_business_fields_and_evidence():
    lead = extract_lead('https://desert.example/', HTML, 'abaya chiffon', 'Saudi Arabia')
    assert lead['name'] == 'Desert Atelier'
    assert lead['emails'] == ['studio@desert.example']
    assert lead['phones'] == ['+971501234567']
    assert lead['whatsapp'] == ['https://wa.me/971501234567']
    assert 'abaya' in lead['keywords']
    assert any('manufacturer' in x.lower() for x in lead['reasons'])
    assert lead['target_country'] == 'Saudi Arabia'
    assert 'United Arab Emirates' in lead['country_evidence']
    assert 'hidden@example.com' not in str(lead)
    assert lead['score'] <= 100


def test_never_invent_contacts_country_or_procurement():
    lead = extract_lead('https://shop.example/', '<html><title>Shop</title><body>Beautiful clothes</body></html>', 'silk', 'Saudi Arabia')
    assert lead['emails'] == []
    assert lead['country_evidence'] == ''
    assert lead['keywords'] == []
    assert lead['target_country'] == 'Saudi Arabia'


def test_search_candidates_unwrap_google_links_exclude_ads_and_private_links():
    html = '''<a href="/url?q=https%3A%2F%2Fbrand.example%2F%3Futm_source%3Dgoogle&sa=U">Brand</a>
    <a href="https://brand.example/">Duplicate</a><a href="https://www.google.com/search?q=other">Search again</a>
    <a href="https://127.0.0.1/admin">Bad</a><a href="https://facebook.com/abaya.house">Abaya House</a>
    <a href="https://instagram.com/p/xyz">Post</a><a href="https://instagram.com/mybrand/">Brand IG</a>
    <a href="https://googleadservices.com/pagead/aclk?x=1">Ad</a>'''
    result = extract_candidates('https://www.google.com/search?q=abaya', html)
    assert {x['url'] for x in result} == {'https://brand.example/', 'https://www.facebook.com/abaya.house', 'https://www.instagram.com/mybrand/'}


def test_facebook_result_dedupes_tracking_and_keeps_business_title():
    html='<a href="https://facebook.com/profile.php?id=123456&amp;__cft__[0]=tracking&amp;__tn__=image"><img></a><a href="https://facebook.com/profile.php?id=123456">Abaya Brand</a><a href="https://facebook.com/profile.php?id=654321">Another Brand</a>'
    result=extract_candidates('https://www.facebook.com/search/pages/?q=abaya',html)
    assert len(result)==2
    assert result[0]['title']=='Abaya Brand'
    assert result[0]['url']=='https://www.facebook.com/profile.php?id=123456'
    assert identity_key('https://facebook.com/profile.php?id=123456&__cft__[0]=x')==identity_key(result[0]['url'])


def test_social_profile_identity_is_not_whole_platform():
    assert identity_key('https://instagram.com/alpha/') != identity_key('https://instagram.com/beta/')
    assert identity_key('https://brand.example/about') == identity_key('https://www.brand.example/contact')
    assert normalize_url('HTTPS://brand.example/?utm_source=x#top') == 'https://brand.example/'


@pytest.mark.parametrize('url', ['file:///etc/passwd','http://localhost:18765','https://127.0.0.1/','https://[::1]/','https://10.0.0.1/','https://user:pass@brand.example/','https://brand.example/?access_token=secret','https://brand.example/logout','https://instagram.com/direct/inbox/'])
def test_reject_private_secret_or_account_urls(url):
    with pytest.raises(ValueError): normalize_url(url)


def test_search_targets_buyers_and_keeps_inputs_encoded():
    from yingsha_leads.facebook import search_source

    entries = plan_search('abaya & nida', '服装品牌', 'Saudi Arabia')
    facebook = next(v for v in entries if urlsplit(v['url']).hostname == 'www.facebook.com')
    assert urlsplit(facebook['url']).path == '/search/pages'
    assert parse_qs(urlsplit(facebook['url']).query) == {'q': ['abaya & nida clothing brand Saudi Arabia']}
    assert search_source(facebook['url'])
    assert facebook['kind'] == 'facebook'
    assert '主页搜索' in facebook['channel']
    google = next(v for v in entries if v['kind'] == 'web')
    instagram = next(v for v in entries if v['kind'] == 'instagram')
    assert urlsplit(google['url']).hostname == 'www.google.com'
    assert parse_qs(urlsplit(google['url']).query)['q'] == ['abaya & nida clothing brand Saudi Arabia']
    assert urlsplit(instagram['url']).hostname == 'www.google.com'
    assert parse_qs(urlsplit(instagram['url']).query)['q'] == ['abaya & nida clothing brand Saudi Arabia site:instagram.com']
    assert 'Google' in instagram['channel'] and 'Instagram' in instagram['channel']


def test_store_merges_evidence_without_overwriting_human_status(tmp_path):
    db = Store(tmp_path / 'test.sqlite')
    first = db.save_lead(extract_lead('https://desert.example/', HTML, 'abaya', 'UAE'))
    db.update_lead(first['id'], 'contacted', '已发送样卡，等待回复')
    second = db.save_lead(extract_lead('https://www.desert.example/contact', '<title>Contact</title><body><a href="mailto:trade@desert.example">Trade</a></body>', 'abaya', 'UAE'))
    assert first['id'] == second['id']
    assert db.list_leads()['total'] == 1
    assert set(second['emails']) == {'studio@desert.example','trade@desert.example'}
    assert second['status'] == 'contacted'
    assert second['notes'] == '已发送样卡，等待回复'
    assert len(second['sources']) == 2


def test_social_and_official_site_merge_only_with_explicit_website(tmp_path):
    db = Store(tmp_path / 'test.sqlite')
    a=db.save_lead(extract_lead('https://instagram.com/desertatelier/', '<title>Desert Atelier</title><body><a href="https://desert.example/">Our website</a>Abaya</body>', 'abaya', 'UAE'))
    b=db.save_lead(extract_lead('https://desert.example/', HTML, 'abaya', 'UAE'))
    assert a['id']==b['id']
    assert 'https://www.instagram.com/desertatelier/' in b['social_links']
    db.save_lead(extract_lead('https://instagram.com/another/', '<title>Another</title><body>Abaya</body>', 'abaya', 'UAE'))
    assert db.list_leads()['total']==2


def test_exports_safe_for_excel_and_keep_source(tmp_path):
    db=Store(tmp_path/'test.sqlite')
    lead=extract_lead('https://desert.example/', HTML, 'abaya', 'UAE')
    lead['name']='=HYPERLINK("https://evil.example","click")'
    row=db.save_lead(lead)
    csv=export_leads([row], 'csv')
    assert csv.startswith(b'\xef\xbb\xbf')
    assert "'=HYPERLINK" in csv.decode('utf-8-sig')
    book=load_workbook(BytesIO(export_leads([row],'xlsx')))
    assert book.active.cell(2,1).data_type!='f'
    assert any('https://desert.example/' in str(c.value) for c in book.active[2])


def test_unrelated_websites_linking_same_social_credit_do_not_merge(tmp_path):
    store=Store(tmp_path/'test.sqlite')
    for brand in ['one','two']:
        store.save_lead(extract_lead(f'https://{brand}.example/', '<title>Brand</title><body>Abaya <a href="https://instagram.com/webdesigner/">Website designed by</a></body>'))
    assert store.list_leads()['total']==2


def test_social_external_links_are_not_automatically_ownership(tmp_path):
    store=Store(tmp_path/'test.sqlite')
    for brand in ['one','two']:
        row=store.save_lead(extract_lead(f'https://instagram.com/{brand}/','<title>Brand</title><body>Abaya <a href="https://about.meta.com/">About</a></body>'))
        assert row['website']==''
    assert store.list_leads()['total']==2


def test_social_platform_name_does_not_replace_company_name():
    html='<meta property="og:site_name" content="Instagram"><meta property="og:title" content="Desert Atelier (@desertatelier) • Instagram"><body>Private label abaya manufacturer in Dubai</body>'
    lead=extract_lead('https://instagram.com/desertatelier/',html)
    assert lead['name'].startswith('Desert Atelier')


def test_identity_link_does_not_destroy_two_manually_edited_leads(tmp_path):
    db=Store(tmp_path/'test.sqlite')
    social=db.save_lead(extract_lead('https://instagram.com/desertatelier/','<title>Desert Atelier</title><body>Abaya</body>'))
    site=db.save_lead(extract_lead('https://desert.example/',HTML))
    db.update_lead(social['id'],'qualified','客户确认需要1000米')
    db.update_lead(site['id'],'contacted','已发目录')
    updated=db.save_lead(extract_lead('https://instagram.com/desertatelier/','<title>Desert Atelier</title><body>Abaya <a href="https://desert.example/">Our website</a></body>'))
    assert db.list_leads()['total']==2
    assert updated['id']==social['id'] and updated['merge_notice']
    assert updated['status']=='qualified' and updated['notes']=='客户确认需要1000米'
    assert db.get_lead(site['id'])['notes']=='已发目录'
    assert db.get_lead(site['id'])['status']=='contacted'
    # Repeated capture must not steal the website's identity or destroy either record.
    site_again=db.save_lead(extract_lead('https://desert.example/',HTML))
    assert site_again['id']==site['id'] and db.list_leads()['total']==2


@pytest.mark.parametrize('hidden',['hidden','style="display:none"'])
def test_hidden_login_form_does_not_block_public_homepage(hidden):
    html=f'<title>Textile company</title><body>We supply wholesale abaya fabric and chiffon fabric to clothing brands worldwide.<div {hidden}><input type="password"></div></body>'
    assert page_gate('https://brand.example/',html)==''


def test_visible_password_remains_blocked():
    html='<title>Textile company</title><body>Please log in to read the textile company information and price list.<input type="password"></body>'
    assert page_gate('https://brand.example/',html)


@pytest.mark.parametrize('text',['Our systems have detected unusual traffic from your computer network. Please try again later.','You are temporarily blocked. We restrict certain activity to protect our community.'])
def test_body_only_rate_limit_is_blocked(text):
    assert page_gate('https://brand.example/',f'<title>Website</title><body>{text}</body>')


def test_facebook_generic_title_uses_visible_profile_name_with_review_label():
    html='<html><head><title>(1) Facebook</title></head><body><div role="button"><span>Fixture Abaya Brand</span></div><span>1.9 万 位粉丝</span><p>Dubai-based abaya wholesale supplier</p><div>+971 50 111 1111</div></body></html>'
    lead=extract_lead('https://facebook.com/profile.php?id=123456',html,'abaya','UAE')
    assert lead['name']=='Fixture Abaya Brand'
    assert '核对' in lead['name_source']
    assert lead['phones']==['+971501111111']


def test_nested_h1_text_supplies_business_name():
    lead=extract_lead('https://brand.example/','<title>Welcome</title><body><h1><span>Fixture</span> Textiles</h1>Wholesale fabric supplier.</body>')
    assert lead['name']=='Fixture Textiles'
