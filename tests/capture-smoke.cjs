"use strict";

// Execute the production DOM capture function inside a real browser. Every
// document and subresource is fulfilled here; no external site is contacted.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || path.join(os.homedir(), "Library/Caches/ms-playwright/chromium-1234/chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing");
const scriptFile = path.resolve(__dirname, "../src/yingsha_leads/scripts/capture.js");
const documentHtml = (body, title = "Fixture business page", metadata = "") => `<!doctype html><html><head><meta charset="utf-8"><title>${title}</title>${metadata}</head><body>${body}</body></html>`;

const cases = [
  {
    name: "Facebook Pages search keeps public business result cards for candidate extraction",
    url: "https://www.facebook.com/search/pages/?q=Dubai%20abaya",
    html: documentHtml('<main><div role="article"><span style="display:contents"><a href="https://www.facebook.com/profile.php?id=123456">PUBLIC_BUSINESS_PAGE</a></span><span style="display:contents">VISIBLE_CONTENTS_TEXT</span><div hidden><span style="display:contents">SECRET_HIDDEN_CONTENTS</span></div><p>Abaya wholesale in Dubai</p></div></main>', 'Facebook Pages search'),
    verify(result) {
      assert.equal(result.error, undefined);
      assert.match(result.html, /PUBLIC_BUSINESS_PAGE/);
      assert.match(result.html, /profile\.php\?id=123456/);
      assert.match(result.html, /VISIBLE_CONTENTS_TEXT/);
      assert.doesNotMatch(result.html, /SECRET_HIDDEN_CONTENTS/);
    },
  },
  {
    name: "Facebook personal home feed is not a business page",
    url: "https://www.facebook.com/",
    html: documentHtml('<main><p>SECRET_PERSONAL_FEED</p></main>', 'Facebook'),
    expected: "NOT_COMPANY_PAGE",
  },
  {
    name: "Google body-only unusual traffic message triggers security gate",
    url: "https://www.google.com/sorry/index",
    html: documentHtml('<p>Our systems have detected unusual traffic from your computer network.</p>', 'Google'),
    expected: "SECURITY_GATE",
  },
  {
    name: "Facebook body-only temporary block triggers security gate",
    url: "https://www.facebook.com/fixture.atelier/",
    html: documentHtml('<main><p>You are temporarily blocked. We restrict certain activity to protect our community.</p></main>', 'Facebook'),
    expected: "SECURITY_GATE",
  },
  {
    name: "Facebook permission denial stops before collecting page content",
    url: "https://www.facebook.com/fixture.atelier/",
    html: documentHtml('<main><p>You do not have permission to view this content. Please contact the page owner.</p></main>', 'Facebook'),
    expected: "SECURITY_GATE",
  },
  {
    name: "public business contacts survive; hidden/form/editable/script secrets are removed",
    url: "https://business-fixture.example/about",
    html: documentHtml(`
      <main>
        <h1>Fixture Textile Company</h1>
        <p>Abaya fabric wholesale business in Dubai, UAE.</p>
        <a href="mailto:business@fixture-company.example" data-secret="SECRET_ATTRIBUTE" onclick="alert('SECRET_EVENT')">business@fixture-company.example</a>
        <a href="tel:+971501234567">Business telephone +971 50 123 4567</a>
        <article>VISIBLE_COMPANY_ARTICLE: official fabric catalogue</article>
        <div hidden>SECRET_HIDDEN_ATTRIBUTE</div>
        <div style="display:none">SECRET_DISPLAY_NONE</div>
        <div style="visibility:hidden">SECRET_VISIBILITY_HIDDEN</div>
        <input value="SECRET_VISIBLE_INPUT">
        <input type="hidden" value="SECRET_HIDDEN_INPUT">
        <input type="password" hidden value="SECRET_HIDDEN_PASSWORD">
        <textarea>SECRET_TEXTAREA</textarea>
        <div contenteditable="true">SECRET_EDITABLE<div>SECRET_EDITABLE_CHILD</div></div>
        <select><option>SECRET_SELECT</option></select>
        <button>SECRET_BUTTON</button>
        <script>window.syntheticFixtureValue = "SECRET_SCRIPT";</script>
      </main>`, "Fixture Textile Company", '<meta property="og:site_name" content="Fixture Textile Company"><meta name="description" content="Public abaya fabric business">'),
    verify(result) {
      assert.equal(result.url, this.url);
      assert.equal(result.error, undefined);
      assert.match(result.html, /Fixture Textile Company/);
      assert.match(result.html, /business@fixture-company\.example/);
      assert.match(result.html, /tel:\+971501234567/);
      assert.match(result.html, /VISIBLE_COMPANY_ARTICLE/);
      assert.match(result.html, /Public abaya fabric business/);
      assert.doesNotMatch(result.html, /SECRET_|<input|<textarea|<script|contenteditable|onclick|data-secret/);
    },
  },
  {
    name: "Instagram business summary remains while articles, comments and outside-main content are removed",
    url: "https://www.instagram.com/fixture_atelier/",
    html: documentHtml(`
      <aside>SECRET_OUTSIDE_MAIN</aside>
      <main><h1>Fixture Atelier</h1><p>Visible abaya fabric brand business@fixture-company.example</p>
        <a href="https://business-fixture.example/">Official website</a>
        <article><p>SECRET_SOCIAL_ARTICLE</p><a href="mailto:commenter@example.com">SECRET_ARTICLE_CONTACT</a></article>
        <div role="article">SECRET_ROLE_ARTICLE</div>
        <section aria-label="Comments"><span>SECRET_ENGLISH_COMMENT</span></section>
        <section aria-label="评论"><span>SECRET_CHINESE_COMMENT</span></section>
        <div role="dialog" hidden>SECRET_HIDDEN_DIALOG</div>
      </main>`),
    verify(result) {
      assert.equal(result.url, this.url);
      assert.equal(result.error, undefined);
      assert.match(result.html, /Visible abaya fabric brand/);
      assert.match(result.html, /business@fixture-company\.example/);
      assert.match(result.html, /https:\/\/business-fixture\.example\//);
      assert.doesNotMatch(result.html, /SECRET_|commenter@example\.com/);
    },
  },
  {
    name: "visible Facebook role dialog stops collection",
    url: "https://www.facebook.com/fixture.atelier/",
    html: documentHtml('<main><h1>Fixture business</h1><p>Public business summary.</p><div role="dialog">SECRET_DIALOG_CONTENT</div></main>'),
    expected: "SOCIAL_DIALOG",
  },
  {
    name: "visible native Instagram dialog stops collection",
    url: "https://www.instagram.com/fixture_atelier/",
    html: documentHtml('<main><h1>Fixture business</h1><p>Public business summary.</p><dialog open>SECRET_NATIVE_DIALOG_CONTENT</dialog></main>'),
    expected: "SOCIAL_DIALOG",
  },
  {
    name: "followers URL stops collection before returning personal content",
    url: "https://www.instagram.com/fixture_atelier/followers/",
    html: documentHtml('<main><h1>Followers</h1><p>SECRET_FOLLOWER_LIST</p></main>'),
    expected: "PRIVATE_PAGE",
  },
  {
    name: "visible password field triggers security gate",
    url: "https://business-fixture.example/private-area",
    html: documentHtml('<h1>Company account</h1><input type="password" value="SECRET_PASSWORD"><p>Business information behind sign in.</p>'),
    expected: "SECURITY_GATE",
  },
  {
    name: "English verification challenge triggers security gate",
    url: "https://business-fixture.example/",
    html: documentHtml('<h1>Please verify you are human</h1><p>SECRET_VERIFICATION_DATA</p>'),
    expected: "SECURITY_GATE",
  },
  {
    name: "Chinese verification challenge triggers security gate",
    url: "https://business-fixture.example/",
    html: documentHtml('<h1>请完成安全验证</h1><p>SECRET_CHINESE_VERIFICATION_DATA</p>'),
    expected: "SECURITY_GATE",
  },
  {
    name: "security challenge title triggers security gate",
    url: "https://business-fixture.example/",
    html: documentHtml('<p>SECRET_CHALLENGE_DATA</p>', 'Just a moment'),
    expected: "SECURITY_GATE",
  },
  {
    name: "social login route triggers security gate",
    url: "https://www.instagram.com/accounts/login/",
    html: documentHtml('<h1>Log in</h1><p>SECRET_LOGIN_DATA</p>'),
    expected: "SECURITY_GATE",
  },
];

(async () => {
  const capture = await fs.readFile(scriptFile, "utf8");
  const browser = await chromium.launch({ executablePath, headless: true });
  const context = await browser.newContext({ serviceWorkers: "block" });
  let activeFixture;
  let fulfilled = 0;
  await context.route("**/*", async (route) => {
    fulfilled++;
    const document = route.request().resourceType() === "document" && route.request().url() === activeFixture?.url;
    await route.fulfill(document ? { status: 200, contentType: "text/html; charset=utf-8", body: activeFixture.html } : { status: 204, body: "" });
  });
  try {
    for (const fixture of cases) {
      activeFixture = fixture;
      const page = await context.newPage();
      try {
        await page.goto(fixture.url, { waitUntil: "domcontentloaded" });
        const result = await page.evaluate(`(${capture})()`);
        if (fixture.expected) assert.deepEqual(result, { error: fixture.expected }, fixture.name);
        else fixture.verify(result);
        console.log(`PASS ${fixture.name}`);
      } finally { await page.close(); }
    }
    assert.ok(fulfilled >= cases.length, "Every test document must be locally fulfilled");
    console.log(`PASS ${cases.length} production capture.js DOM scenarios; all ${fulfilled} browser requests fulfilled locally; no real account or website accessed.`);
  } finally { await context.close(); await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
