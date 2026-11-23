import httpx
import pytest

from yingsha_leads import fetching
from yingsha_leads.fetching import PageFetcher,FetchError


def mock_fetcher(monkeypatch,handler):
    monkeypatch.setattr(fetching,'validate_network',lambda url:url)
    fetcher=PageFetcher(); fetcher.client.close()
    fetcher.client=httpx.Client(transport=httpx.MockTransport(handler))
    clock=[100.0]; waits=[]
    monkeypatch.setattr(fetching.time,'monotonic',lambda:clock[0])
    def sleep(seconds): waits.append(seconds); clock[0]+=seconds
    monkeypatch.setattr(fetching.time,'sleep',sleep)
    return fetcher,waits


def test_redirect_must_not_fetch_robots_disallowed_path(monkeypatch):
    calls=[]
    def handler(request):
        calls.append(request.url.path)
        if request.url.path=='/robots.txt': return httpx.Response(200,text='User-agent: *\nDisallow: /private')
        if request.url.path=='/': return httpx.Response(302,headers={'location':'/private'})
        return httpx.Response(200,text='<title>Private page</title>',headers={'content-type':'text/html'})
    fetcher,_=mock_fetcher(monkeypatch,handler)
    try:
        with pytest.raises(FetchError,match='robots'): fetcher.fetch('https://brand.example/')
        assert calls==['/robots.txt','/']
    finally: fetcher.close()


def test_all_requests_respect_delay_including_robots_and_redirects(monkeypatch):
    calls=[]
    def handler(request):
        calls.append(request.url.path)
        if request.url.path=='/robots.txt': return httpx.Response(200,text='User-agent: *\nCrawl-delay: 4\nRequest-rate: 1/7')
        if request.url.path=='/': return httpx.Response(302,headers={'location':'/about'})
        return httpx.Response(200,text='<title>Brand manufacturer</title><body>Wholesale fabric company business contact us.</body>',headers={'content-type':'text/html'})
    fetcher,waits=mock_fetcher(monkeypatch,handler)
    try:
        assert fetcher.fetch('https://brand.example/')['url']=='https://brand.example/about'
        assert calls==['/robots.txt','/','/about']
        assert waits==[7,7]
        fetcher.fetch('https://brand.example/')
        assert len(calls)==3
    finally: fetcher.close()
