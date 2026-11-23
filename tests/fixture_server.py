"""Local UI fixture service; never opens OpenClaw or uses the production data folder."""
from pathlib import Path
from contextlib import asynccontextmanager
import argparse
import sys
import tempfile
import time

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from yingsha_leads.app import create_app
from yingsha_leads.browser import BrowserBridge, BrowserError


SEARCH_URL = "https://www.google.com/search?q=abaya+fabric+UAE"
COMPANY_URL = "https://fixture-textiles.example/"
SOCIAL_URL = "https://www.instagram.com/fixture_atelier/"
FACEBOOK_SEARCH_URL = "https://www.facebook.com/search/pages/?q=abaya+Dubai"
FACEBOOK_SEARCH_HTML = '<html><body><h1>Abaya clothing brands in Dubai</h1>' + ''.join(
    f'<a href="https://www.facebook.com/fixture_atelier_{index}">Atelier {index}</a>'
    for index in range(23)
) + '</body></html>'
SEARCH_HTML = f"""<!doctype html><html><head><title>Fixture search</title></head>
<body><h1>Abaya fabric company search results in UAE</h1>
<a href="{COMPANY_URL}">Fixture Atelier official website</a>
<a href="{SOCIAL_URL}">Fixture Atelier Instagram business page</a>
<p>Test search results include a company website and an independently selected social page.</p>
</body></html>"""
COMPANY_HTML = f"""<!doctype html><html><head><title>Fixture Atelier</title></head>
<body><h1>Fixture Atelier &lt;img src=x onerror=alert(1)&gt;</h1>
<p>Abaya fabric wholesale manufacturer and private label clothing in Dubai, UAE.</p>
<a href="mailto:business@fixture-textiles.example">Business email</a>
<a href="tel:+971501234567">Business telephone</a>
<a href="https://wa.me/971501234567">WhatsApp</a>
<a href="https://www.instagram.com/independent_fixture_link/">A social link on this page</a>
<p>Official commercial contact details for a synthetic browser test only.</p>
</body></html>"""
SOCIAL_HTML = f"""<!doctype html><html><head><title>Fixture Social Atelier</title></head>
<body><h1>Fixture Social Atelier</h1><p>Abaya fabric clothing brand in Dubai, UAE.</p>
<a href="{COMPANY_URL}">Official website</a>
<a href="mailto:social@fixture-textiles.example">Public business email</a>
<p>This is a synthetic business profile used to verify confirmed current-page capture.</p>
</body></html>"""


class FixtureBrowser:
    validate_target = staticmethod(BrowserBridge.validate_target)

    def __init__(self):
        self.facebook_page = {"url": FACEBOOK_SEARCH_URL, "html": FACEBOOK_SEARCH_HTML}

    def probe(self):
        return {"connected": True, "profile": "chrome", "message": "测试连接：选择一个模拟的已授权标签页。", "tabs": [
            {"targetId": "FIXTURESEARCH", "title": "Fixture search results", "url": SEARCH_URL},
            {"targetId": "FIXTURESOCIAL", "title": "Fixture Social Atelier", "url": SOCIAL_URL},
            {"targetId": "FIXTUREFACEBOOK", "title": "Facebook business search", "url": self.facebook_page["url"]},
            {"targetId": "FIXTUREPROFILE", "title": "Facebook business profile", "url": "https://www.facebook.com/profile.php?id=123456"},
            {"targetId": "FIXTUREUNKNOWN", "title": "Google tab with unreadable address", "url": ""},
        ]}

    def capture(self, target_id):
        self.validate_target(target_id)
        if target_id == "FIXTURESEARCH":
            return {"url": SEARCH_URL, "html": SEARCH_HTML}
        if target_id == "FIXTURESOCIAL":
            return {"url": SOCIAL_URL, "html": SOCIAL_HTML}
        if target_id == "FIXTUREFACEBOOK":
            return self.facebook_page
        raise BrowserError("测试标签页不存在")

    def visit(self, target_id, url):
        self.validate_target(target_id)
        time.sleep(0.15)
        if target_id == "FIXTUREFACEBOOK":
            index = url.rstrip('/').rsplit('_', 1)[-1]
            self.facebook_page = {"url": url, "html": f'''<html><head><title>Atelier {index}</title></head>
                <body><h1>Atelier {index}</h1><p>Page · Clothing (Brand)</p>
                <p>Our brand makes abaya fashion clothing in Dubai, UAE.</p>
                <a href="mailto:atelier{index}@fixture-textiles.example">Public business email</a></body></html>'''}
            return self.facebook_page
        return {"url": url, "html": COMPANY_HTML}


class FixtureFetcher:
    def fetch(self, url):
        time.sleep(0.15)
        return {"url": url, "html": COMPANY_HTML}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18768)
    args = parser.parse_args()
    temporary = tempfile.TemporaryDirectory(prefix="yingsha-ui-fixture-")
    with temporary as directory:
        print(f"Fixture data only: {directory}", flush=True)
        app = create_app(Path(directory), browser=FixtureBrowser(), fetcher=FixtureFetcher(), delay=0.1)
        original_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def fixture_lifespan(application):
            try:
                async with original_lifespan(application):
                    yield
            finally:
                # Uvicorn may re-raise SIGTERM after lifespan shutdown; clean
                # fixture data before that signal bypasses the outer context.
                temporary.cleanup()

        app.router.lifespan_context = fixture_lifespan
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
