"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

const root = path.resolve(__dirname, "..");
const base = "http://127.0.0.1:18768";
const artifacts = process.env.BROWSER_ARTIFACTS || path.join(os.tmpdir(), "yingsha-leads-browser-smoke");
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || path.join(os.homedir(), "Library/Caches/ms-playwright/chromium-1234/chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing");
const reports = [];

async function until(check, description, timeout = 15000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (await check()) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out: ${description}`);
}

async function startFixture() {
  try { await fetch(`${base}/api/status`); throw new Error("Fixture port 18768 is already in use; refusing to reuse its data."); }
  catch (error) { if (!error.cause) throw error; }
  const child = spawn(path.join(root, ".venv/bin/python"), ["tests/fixture_server.py"], { cwd: root, stdio: ["ignore", "pipe", "pipe"] });
  let output = "";
  child.stdout.on("data", (data) => { output += data.toString(); });
  child.stderr.on("data", (data) => { output += data.toString(); });
  try {
    await until(async () => {
      if (child.exitCode !== null) throw new Error(`Fixture exited: ${output}`);
      try { return (await fetch(`${base}/api/status`)).ok; } catch { return false; }
    }, "fixture server ready", 20000);
  } catch (error) { child.kill("SIGTERM"); throw error; }
  return { child, output: () => output };
}

async function stopFixture(fixture) {
  if (fixture.child.exitCode !== null) return;
  const exited = new Promise((resolve) => fixture.child.once("exit", resolve));
  fixture.child.kill("SIGTERM");
  await exited;
}

async function noOverflow(page, stage) {
  const metrics = await page.evaluate(() => ({ viewport: innerWidth, scroll: document.documentElement.scrollWidth, body: document.body.scrollWidth }));
  assert.ok(metrics.scroll <= metrics.viewport && metrics.body <= metrics.viewport, `${stage}: horizontal overflow ${JSON.stringify(metrics)}`);
}

async function screenshot(page, viewport, stage, fullPage = true) {
  await noOverflow(page, `${viewport} ${stage}`);
  if (fullPage) await page.evaluate(() => window.scrollTo(0, 0));
  const file = path.join(artifacts, `${viewport}-${stage}.png`);
  await page.screenshot({ path: file, fullPage });
  return file;
}

async function flow(browser, viewport) {
  const fixture = await startFixture();
  const context = await browser.newContext({ viewport: viewport.size, acceptDownloads: true, locale: "zh-CN" });
  const page = await context.newPage();
  const errors = [], requestPaths = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error" && !message.text().includes("404") && !message.text().includes("503")) errors.push(message.text()); });
  page.on("request", (request) => requestPaths.push(new URL(request.url()).pathname));
  try {
    await page.goto(base);
    await until(async () => await page.locator("#stat-leads").textContent() === "0", "initial empty status");
    assert.equal(requestPaths.filter((url) => url === "/api/browser").length, 0, "Browser must not be probed automatically");
    await screenshot(page, viewport.name, "search-empty");
    await page.locator('[data-view="leads"]').click();
    await page.getByRole("heading", { name: "第一条线索，从一次搜索开始" }).waitFor();
    await screenshot(page, viewport.name, "leads-empty");
    await page.locator('[data-view="jobs"]').click();
    await page.getByRole("heading", { name: "还没有采集任务" }).waitFor();
    await noOverflow(page, `${viewport.name} jobs-empty`);
    await page.locator('[data-view="search"]').click();
    await page.locator("#search-submit").click();
    await until(async () => await page.locator("#search-results .query-link").count() === 3, "search links generated");
    assert.equal(await page.locator("#search-results a").first().getAttribute("target"), "_blank");
    assert.match(await page.locator("#search-results a").first().getAttribute("rel"), /noopener/);
    const facebookLink = page.locator('.query-card[data-kind="facebook"] .query-link');
    const facebookUrl = new URL(await facebookLink.getAttribute("href"));
    assert.equal(facebookUrl.hostname, "www.facebook.com");
    assert.equal(facebookUrl.pathname, "/search/pages");
    assert.equal(facebookUrl.searchParams.get("q"), "abaya fabric clothing brand UAE");
    assert.match(await page.locator('.query-card[data-kind="instagram"]').textContent(), /Google.*Instagram/);
    await page.locator("#country").fill("Saudi Arabia");
    assert.equal(await page.locator("#search-results-section").isVisible(), false, "Changed conditions must hide stale search links");
    assert.equal(await page.locator("#search-changed").isVisible(), true);
    await page.locator("#search-submit").click();
    await facebookLink.waitFor({ state: "visible" });
    assert.equal(new URL(await facebookLink.getAttribute("href")).searchParams.get("q"), "abaya fabric clothing brand Saudi Arabia");
    const openFacebook = page.locator('#facebook-search-open');
    assert.equal(await openFacebook.count(), 1, "Facebook primary action must directly open search");
    assert.equal(await openFacebook.getAttribute('href'), await facebookLink.getAttribute('href'));
    const browserRequestsBeforeOpen = requestPaths.filter(url => ['/api/browser','/api/facebook/import','/api/jobs'].includes(url)).length;
    // Fulfill the destination locally: this test never searches real Facebook.
    await context.route('https://www.facebook.com/search/pages?*', route => route.fulfill({contentType:'text/html',body:'<title>Fixture Facebook search</title><p>Local test response</p>'}));
    const [searchTab] = await Promise.all([page.waitForEvent('popup'), openFacebook.click()]);
    await searchTab.waitForURL('https://www.facebook.com/search/pages?*', {waitUntil:'domcontentloaded'});
    assert.equal(new URL(searchTab.url()).searchParams.get('q'), 'abaya fabric clothing brand Saudi Arabia');
    assert.equal(await page.locator('#browser-dialog').evaluate(dialog => dialog.open), false, "Opening search must not show the tab picker");
    assert.equal(requestPaths.filter(url => ['/api/browser','/api/facebook/import','/api/jobs'].includes(url)).length, browserRequestsBeforeOpen, "Opening search must not connect, import, or collect");
    await searchTab.close();
    await context.unroute('https://www.facebook.com/search/pages?*');
    await screenshot(page, viewport.name, "search-entries");
    await page.locator("#country").fill("UAE");
    await page.locator("#search-submit").click();
    await facebookLink.waitFor({ state: "visible" });

    // Import remains a separate explicit action and requires a selected tab.
    await page.locator("#facebook-import").click();
    await until(async () => await page.locator("#browser-dialog").evaluate((dialog) => dialog.open), "missing tab opens connection dialog");
    await page.locator('#browser-target option[value="FIXTURESEARCH"]').waitFor({ state: "attached" });
    assert.equal(await page.locator('#browser-target option[value="FIXTUREPROFILE"]').isDisabled(), true, "A single Facebook profile must be disabled for import");
    assert.match(await page.locator('#browser-target option[value="FIXTUREPROFILE"]').textContent(), /单个.*主页/);
    assert.equal(await page.locator('#browser-target option[value="FIXTURESOCIAL"]').isDisabled(), true, "Instagram profile cannot supply Facebook search candidates");
    assert.equal(await page.locator('#browser-target option[value="FIXTUREUNKNOWN"]').isDisabled(), true, "Unreadable addresses cannot be guessed from their title");
    assert.equal(await page.locator('#browser-target option[value="FIXTUREFACEBOOK"]').isDisabled(), false);
    assert.equal(await page.locator('#browser-target option[value="FIXTURESEARCH"]').isDisabled(), false);
    // Even a synthetic change event cannot enable confirmation for a disabled choice.
    await page.locator('#browser-target').evaluate((select) => { select.value='FIXTUREPROFILE'; select.dispatchEvent(new Event('change',{bubbles:true})); });
    assert.equal(await page.locator('#confirm-browser').isDisabled(), true);
    assert.equal(requestPaths.filter((url) => url === "/api/facebook/import").length, 0, "No import until user selects a tab");
    await page.keyboard.press("Escape");
    assert.equal(await page.locator("#facebook-next").isDisabled(), true, "Collecting requires an imported session first");

    await page.locator("#open-browser").click();
    await page.locator('#browser-target option[value="FIXTURESEARCH"]').waitFor({ state: "attached" });
    await until(async () => await page.locator('#browser-target').isEnabled(), "general tab list refreshed");
    assert.equal(await page.locator('#browser-target option[value="FIXTUREPROFILE"]').isDisabled(), false, "General selection still supports saving one business profile");
    await page.locator("#browser-target").selectOption("FIXTUREPROFILE");
    await page.locator("#confirm-browser").click();
    await page.locator("#facebook-import").click();
    await until(async () => await page.locator("#browser-dialog").evaluate((dialog) => dialog.open), "previously selected profile prompts a valid search-page choice");
    await until(async () => await page.locator('#browser-target option[value="FIXTUREPROFILE"]').isDisabled(), "refreshed profile remains unavailable for import");
    await page.locator("#close-browser").click();
    await page.locator("#open-browser").click();
    await until(async () => await page.locator('#browser-target').isEnabled(), "manual tab selection ready");
    await page.locator("#browser-target").selectOption("FIXTURESEARCH");
    await page.locator("#confirm-browser").click();
    assert.equal(requestPaths.filter((url) => url === "/api/facebook/import").length, 0, "Cancelled pending actions must not run after later manual connection");
    await page.locator("#facebook-import").click();
    await until(async () => (await page.locator("#facebook-feedback").textContent()).includes("没有可采集"), "import error is shown next to Facebook buttons");
    const feedbackInView = await page.locator("#facebook-feedback").evaluate((node) => { const rect=node.getBoundingClientRect(); return rect.top>=0 && rect.bottom<=innerHeight; });
    assert.equal(feedbackInView, true, "Import feedback must be visible at current scroll position");
    const importsBeforePageChange = requestPaths.filter((url) => url === "/api/facebook/import").length;
    await page.route("**/api/browser", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected:true,tabs:[{targetId:"FIXTURESEARCH",title:"Page changed to Facebook company",url:"https://www.facebook.com/atelier"}] }) }), { times: 1 });
    await page.locator("#facebook-import").click();
    await until(async () => await page.locator("#browser-dialog").evaluate((dialog) => dialog.open), "changed address is checked again before import");
    assert.equal(await page.locator('#browser-target option[value="FIXTURESEARCH"]').isDisabled(), true);
    assert.equal(await page.locator('#confirm-browser').isDisabled(), true);
    assert.match(await page.locator('#browser-message').textContent(), /已找到 0 个/);
    assert.equal(requestPaths.filter((url) => url === "/api/facebook/import").length, importsBeforePageChange, "Changed company profile must not reach import API");
    await page.locator("#close-browser").click();
    await page.locator("#open-browser").click();
    await until(async () => await page.locator('#browser-target').isEnabled(), "original fixture tabs refreshed");
    await page.locator("#browser-target").selectOption("FIXTURESEARCH");
    await page.locator("#confirm-browser").click();
    await page.locator("#capture-candidates").click();
    await until(async () => await page.locator(".candidate-row").count() === 2, "candidate extraction");
    await page.locator("#candidate-select-all").check();
    await screenshot(page, viewport.name, "candidates");
    await page.locator("#prepare-candidates").click();
    assert.match(await page.locator("#job-urls").inputValue(), /instagram/);
    await page.locator("#start-job").click();
    await until(async () => (await page.locator("#global-alert-text").textContent()).includes("无法添加社交主页"), "mixed social URL rejected with specific guidance");
    assert.equal((await (await page.request.get(`${base}/api/jobs`)).json()).jobs.length, 0);
    await page.locator("#job-urls").fill("https://fixture-textiles.example/");
    await page.locator("#start-job").click();
    await until(async () => (await page.locator("#jobs-list").textContent()).includes("已完成"), "website job completed");
    assert.match(await page.locator("#jobs-list").textContent(), /成功 1 \/ 1 · 失败 0/);
    await screenshot(page, viewport.name, "job-completed");

    await page.locator('[data-view="leads"]').click();
    await page.locator(".lead-name").first().waitFor();
    assert.equal(await page.locator("#leads-content img").count(), 0, "Harvested HTML must remain text");
    assert.match(await page.locator(".lead-name").first().textContent(), /<img src=x onerror=alert\(1\)>/);
    await screenshot(page, viewport.name, "leads");
    await page.locator(".lead-name").first().click();
    await page.locator("#detail-notes").waitFor();
    assert.equal(await page.locator("#detail-notes").getAttribute("maxlength"), "5000", "Notes limit must match the API");
    assert.equal(await page.getByText("页面中的社交链接", { exact: true }).count(), 1, "Social links must not imply verified ownership");
    await screenshot(page, viewport.name, "lead-detail-overview", false);
    await page.locator("#detail-status").selectOption("qualified");
    await page.locator("#detail-notes").fill("已核对来源，计划下周跟进。仅测试数据。");
    await page.getByRole("button", { name: "保存跟进记录" }).click();
    await page.locator("#lead-dialog .dialog-feedback.success").waitFor();
    await screenshot(page, viewport.name, "lead-detail", false);
    await page.keyboard.press("Escape");
    assert.equal(await page.locator("#lead-dialog").evaluate((dialog) => dialog.open), false, "Escape must close the detail dialog");
    await page.locator(".lead-name").first().click();
    await page.locator("#detail-notes").waitFor();
    assert.equal(await page.locator("#detail-status").inputValue(), "qualified");
    assert.match(await page.locator("#detail-notes").inputValue(), /已核对来源/);
    await page.keyboard.press("Escape");

    await page.locator("#lead-status-filter").selectOption("qualified");
    await until(async () => (await page.locator("#lead-total").textContent()).includes("共 1 条"), "qualified filter");
    for (const format of ["csv", "xlsx"]) {
      const [download] = await Promise.all([page.waitForEvent("download"), page.locator(`#export-${format}`).click()]);
      const target = path.join(artifacts, `${viewport.name}-export.${format}`);
      await download.saveAs(target);
      const data = await fs.readFile(target);
      if (format === "csv") assert.match(data.toString("utf8"), /business@fixture-textiles\.example/);
      else assert.equal(data.subarray(0, 2).toString(), "PK");
    }
    await page.locator("#lead-search").fill("this-company-does-not-exist");
    await page.locator("#filter-leads").click();
    await page.getByRole("heading", { name: "没有符合条件的线索" }).waitFor();
    await noOverflow(page, `${viewport.name} filtered-empty`);
    await page.getByRole("button", { name: "清除筛选" }).click();
    await page.locator(".lead-name").first().waitFor();

    await page.locator('[data-view="search"]').click();
    await page.route("**/api/browser", async (route) => { await new Promise((resolve) => setTimeout(resolve, 250)); await route.continue(); }, { times: 1 });
    await page.locator("#open-browser").click();
    assert.equal(await page.locator("#browser-target").isDisabled(), true, "Stale tab choices must be disabled during refresh");
    await page.locator('#browser-target option[value="FIXTURESOCIAL"]').waitFor({ state: "attached" });
    await page.locator("#browser-target").selectOption("FIXTURESOCIAL");
    await page.locator("#confirm-browser").click();
    await page.locator("#capture-lead").click();
    await until(async () => (await page.locator("#global-alert-text").textContent()).includes("已保存公开资料"), "confirmed social current-page capture");

    await page.locator('[data-view="jobs"]').click();
    await page.locator("#job-urls").fill("https://browser-fixture.example/");
    await page.locator('input[name="job-mode"][value="browser"]').check();
    assert.match(await page.locator("#browser-mode-note").textContent(), /替换所选标签页内容/);
    await page.locator("#start-job").click();
    await until(async () => { const text = await page.locator("#jobs-list .job-card").first().textContent(); return text.includes("已登录浏览器采集") && text.includes("已完成"); }, "browser website job completed");
    assert.match(await page.locator("#jobs-list .job-card").first().textContent(), /已登录浏览器采集/);

    await page.locator('[data-view="search"]').click();
    const importsBeforeEntry = requestPaths.filter((url) => url === "/api/facebook/import").length;
    await page.locator("#facebook-import").click();
    await page.locator('#browser-target option[value="FIXTUREFACEBOOK"]').waitFor({ state: "attached" });
    assert.equal(requestPaths.filter((url) => url === "/api/facebook/import").length, importsBeforeEntry, "Entry action must ask for the new search tab even with an old tab selected");
    await page.locator("#browser-target").selectOption("FIXTUREFACEBOOK");
    await page.locator("#confirm-browser").click();
    await until(async () => (await page.locator("#facebook-session-summary").textContent()).includes("已处理 0 · 剩余 23"), "entry action imports selected Facebook search results");
    assert.equal((await (await page.request.get(`${base}/api/jobs`)).json()).jobs.filter((job) => job.mode === "facebook").length, 0, "Import must not begin visiting companies");
    assert.match(await page.locator("#facebook-next").textContent(), /开始采集/);
    const importBounds = await page.locator("#facebook-import").boundingBox();
    const collectBounds = await page.locator("#facebook-next").boundingBox();
    assert.ok(collectBounds.y - (importBounds.y + importBounds.height) >= 80, "Import must precede collection in distinct, separated steps");
    await page.locator("#facebook-next").click();
    const batch = page.locator(".facebook-active-batch");
    const loading = batch.locator(".facebook-batch-loading");
    await loading.waitFor({ state: "visible" });
    assert.match(await loading.textContent(), /本批已处理 \d+ \/ 10/);
    await until(async () => (await loading.locator(".facebook-loading-spinner").boundingBox())?.width >= 48, "Collection needs a prominent loading indicator");
    await batch.getByRole("button", { name: "暂停", exact: true }).click();
    await batch.getByRole("button", { name: "继续本批", exact: true }).waitFor({ state: "visible" });
    assert.equal(await loading.count(), 0, "Paused batches must not show an active spinner");
    await batch.getByRole("button", { name: "继续本批", exact: true }).click();
    await loading.waitFor({ state: "visible" });
    await batch.scrollIntoViewIfNeeded();
    await screenshot(page, viewport.name, "facebook-collecting", false);
    await until(async () => (await page.locator("#facebook-session-summary").textContent()).includes("已处理 10 · 剩余 13"), "Facebook first 10 completed");
    assert.equal(await loading.count(), 0, "Completed batches must remove the loading indicator");
    assert.match(await page.locator("#facebook-next").textContent(), /继续下 10 个/);
    await screenshot(page, viewport.name, "facebook-first-batch");
    const probeCount = requestPaths.filter((url) => url === "/api/browser").length;
    await page.reload();
    await until(async () => (await page.locator("#facebook-session-summary").textContent()).includes("已处理 10 · 剩余 13"), "Facebook session restored after reload");
    assert.equal(requestPaths.filter((url) => url === "/api/browser").length, probeCount, "Reload must not automatically probe browser");
    await page.locator("#facebook-next").click();
    await until(async () => await page.locator("#browser-dialog").evaluate((dialog) => dialog.open), "next batch reconnects after reload");
    await page.locator('#browser-target option[value="FIXTUREFACEBOOK"]').waitFor({ state: "attached" });
    assert.equal(await page.locator('#browser-target option[value="FIXTUREFACEBOOK"]').isDisabled(), false, "Continuing a saved batch can reuse the tab now showing its last company profile");
    await page.locator("#browser-target").selectOption("FIXTUREFACEBOOK");
    await page.locator("#confirm-browser").click();
    await until(async () => (await page.locator("#facebook-session-summary").textContent()).includes("已处理 20 · 剩余 3"), "Facebook next 10 completed from saved candidates");
    await page.locator("#facebook-next").click();
    await until(async () => (await page.locator("#facebook-session-summary").textContent()).includes("已处理 23 · 剩余 0"), "Facebook final 3 completed");
    assert.equal(await page.locator("#facebook-next").isDisabled(), true);
    const fbJobs = (await (await page.request.get(`${base}/api/jobs`)).json()).jobs.filter((job) => job.mode === "facebook");
    assert.deepEqual(fbJobs.map((job) => job.items.length).sort((a, b) => a - b), [3, 10, 10]);
    assert.equal(new Set(fbJobs.flatMap((job) => job.items.map((item) => item.url))).size, 23);
    assert.equal(fbJobs.flatMap((job) => job.items).filter((item) => item.status === "done").length, 23);
    await screenshot(page, viewport.name, "facebook-completed");

    await page.route("**/api/browser", (route) => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "测试：浏览器未连接，请检查扩展。" }) }), { times: 1 });
    await page.locator("#open-browser").click();
    await until(async () => (await page.locator("#browser-message").textContent()).includes("测试：浏览器未连接"), "visible browser error");
    assert.equal(await page.locator("#confirm-browser").isDisabled(), true);
    assert.equal(await page.locator("#refresh-browser").isEnabled(), true);
    await screenshot(page, viewport.name, "browser-error", false);
    await page.keyboard.press("Escape");

    const status = await (await page.request.get(`${base}/api/status`)).json();
    assert.equal(status.model_calls, 0);
    assert.match(status.data_dir, /yingsha-ui-fixture-/);
    // The deliberate non-Facebook search import above returns one validation 400.
    const expectedValidationError = "Failed to load resource: the server responded with a status of 400 (Bad Request)";
    assert.deepEqual(errors, [expectedValidationError], `Unexpected browser errors: ${errors.join("\n")}`);
    reports.push({ viewport: viewport.name, size: viewport.size, status: "passed", browserRequests: requestPaths.filter((url) => url === "/api/browser").length, modelCalls: status.model_calls, dataDir: status.data_dir });
    console.log(`PASS ${viewport.name}: empty states, search, browser selection, mixed candidates, HTTP/browser jobs, safe rendering, notes/status persistence, filters, CSV/XLSX, social current-page capture, Facebook 10/10/3 with reload and dedup, error recovery, no horizontal overflow.`);
  } catch (error) {
    await page.screenshot({ path: path.join(artifacts, `${viewport.name}-failure.png`), fullPage: true });
    console.error(fixture.output());
    throw error;
  } finally { await context.close(); await stopFixture(fixture); }
}

(async () => {
  await fs.mkdir(artifacts, { recursive: true });
  const browser = await chromium.launch({ executablePath, headless: true });
  try {
    // This local workbench is used on desktop; no mobile optimization requested.
    for (const viewport of [{ name: "desktop", size: { width: 1440, height: 1000 } }]) {
      await flow(browser, viewport);
      await assert.rejects(fs.access(reports.at(-1).dataDir), { code: "ENOENT" }, "Fixture temporary data must be removed on shutdown");
    }
    await fs.writeFile(path.join(artifacts, "report.json"), JSON.stringify(reports, null, 2));
    console.log(`Artifacts: ${artifacts}`);
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
