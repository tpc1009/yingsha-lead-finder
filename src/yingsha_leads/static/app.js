"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = {
    csrf: "", view: "search", browser: null, targetId: "", candidates: [], searchKey: "",
    jobs: [], jobsLoaded: false, pollTimer: null, jobsRequest: null, jobActions: new Set(),
    page: 1, total: 0, pageSize: 25, filters: { q: "", status: "" },
    leadRequest: 0, detailRequest: 0, currentLead: null, captureBusy: false,
    facebookSessions: [], facebookSessionId: "", facebookLoaded: false, facebookRequest: null, facebookBusy: false, pendingFacebookAction: "",
  };
  try { state.facebookSessionId = localStorage.getItem("yingsha-facebook-session") || ""; } catch { /* Saved sessions remain available from the server. */ }
  const leadStatuses = { new: "待跟进", contacted: "已联系", replied: "已回复", qualified: "有效线索", archived: "已归档" };
  const jobStatuses = { queued: "等待采集", running: "正在采集", paused: "已暂停", blocked: "等待处理", completed: "已完成", stopped: "已停止", pending: "待处理", done: "已完成", review: "待复核", failed: "采集失败", skipped: "已跳过" };
  const screeningLabels = { matched: "符合规则", review: "待复核" };
  const statusColors = { running: "green", completed: "green", qualified: "green", new: "neutral", contacted: "blue", replied: "green", archived: "neutral", queued: "neutral", paused: "orange", blocked: "orange", stopped: "neutral", failed: "red" };
  const viewNames = { search: "搜索找客户", jobs: "采集任务", leads: "客户线索" };
  let statusRequest = null;

  function el(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  }

  function safeUrl(value) {
    try {
      const url = new URL(String(value));
      return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : "";
    } catch { return ""; }
  }

  function isSocialBatchUrl(value) {
    const hostname = new URL(value).hostname.toLowerCase().replace(/\.$/, "");
    return ["facebook.com", "instagram.com", "fb.com", "fb.watch", "instagr.am"].some((domain) => hostname === domain || hostname.endsWith(`.${domain}`));
  }

  function link(value, label, className = "") {
    const href = safeUrl(value);
    if (!href) return el("span", className, label || value || "未提供");
    const anchor = el("a", className, label || value);
    anchor.href = href;
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer";
    return anchor;
  }

  function siteName(value) {
    try { return new URL(value).hostname.replace(/^www\./, ""); }
    catch { return value || "未提供网址"; }
  }

  function dateLabel(value) {
    if (!value) return "时间未记录";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
  }

  function array(value) { return Array.isArray(value) ? value.filter((item) => item !== null && item !== undefined) : []; }
  function number(value) { const n = Number(value); return Number.isFinite(n) ? n : 0; }
  function chip(text, color = "neutral") { return el("span", `chip ${color}`, text); }

  function announce(message) { $("announcement").textContent = message; }

  function report(message, success = false) {
    $("global-alert-text").textContent = String(message);
    $("global-alert").classList.toggle("success", success);
    $("global-alert").hidden = false;
    const dialog = document.querySelector("dialog[open]");
    if (dialog) {
      let alert = dialog.querySelector(".dialog-feedback");
      if (!alert) { alert = el("div", "alert dialog-feedback"); alert.setAttribute("role", "alert"); dialog.querySelector(".dialog-heading").after(alert); }
      alert.classList.toggle("success", success);
      alert.textContent = String(message);
    }
    announce(message);
  }

  function errorMessage(error) { return error instanceof Error ? error.message : "操作未完成，请重试。"; }

  async function api(path, options = {}) {
    const method = options.method || "GET";
    if (method !== "GET" && !state.csrf) await loadStatus();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 90000);
    try {
      const headers = { Accept: "application/json" };
      if (method !== "GET") { headers["Content-Type"] = "application/json"; headers["X-CSRF-Token"] = state.csrf; }
      const response = await fetch(path, { ...options, method, headers, credentials: "same-origin", signal: controller.signal });
      if (options.download && response.ok) return { blob: await response.blob(), disposition: response.headers.get("Content-Disposition") };
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(typeof data?.detail === "string" ? data.detail : `请求未完成（${response.status}），请稍后重试。`);
      if (!data) throw new Error("本地服务返回了无法识别的响应，请检查服务是否正常。");
      return data;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("请求超时。请检查本地服务或浏览器连接后重试。");
      if (error instanceof TypeError) throw new Error("无法连接本地服务。请确认工具仍在运行，再刷新页面。");
      throw error;
    } finally { clearTimeout(timer); }
  }

  async function busy(button, label, action) {
    if (button.disabled) return;
    const children = [...button.childNodes];
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.textContent = label;
    try { return await action(); }
    catch (error) { report(errorMessage(error)); }
    finally { button.replaceChildren(...children); button.disabled = false; button.removeAttribute("aria-busy"); }
  }

  function empty(container, title, description, symbol = "▦", action) {
    const box = el("div", "empty-state");
    const icon = el("span", "empty-symbol", symbol); icon.setAttribute("aria-hidden", "true");
    box.append(icon, el("h3", "", title), el("p", "", description));
    if (action) { const button = el("button", "button", action.label); button.type = "button"; button.addEventListener("click", action.run); box.append(button); }
    container.replaceChildren(box);
  }

  async function loadStatus() {
    if (statusRequest) return statusRequest;
    statusRequest = (async () => {
      try {
        const data = await api("/api/status");
        state.csrf = data.csrf_token || "";
        $("stat-leads").textContent = number(data.stats?.leads).toLocaleString("zh-CN");
        $("nav-lead-count").textContent = number(data.stats?.leads).toLocaleString("zh-CN");
        $("stat-running").textContent = number(data.stats?.running);
        $("stat-pending").textContent = number(data.stats?.pending);
        $("stat-model").replaceChildren(document.createTextNode(String(number(data.model_calls))), el("span", "", "次"));
        $("runtime-dot").className = "status-dot connected";
        $("runtime-title").textContent = "本地服务已就绪";
        $("version-label").textContent = data.version ? `v${data.version}` : "本地工作台";
        $("engine-label").textContent = `${data.engine || "Scrapling"} · 本地存储`;
        return data;
      } catch (error) {
        $("runtime-dot").className = "status-dot error";
        $("runtime-title").textContent = "本地服务连接失败";
        throw error;
      } finally { statusRequest = null; }
    })();
    return statusRequest;
  }

  function showView(name, focus = false) {
    if (!viewNames[name]) name = "search";
    state.view = name;
    document.querySelectorAll(".view").forEach((view) => { view.hidden = view.id !== `view-${name}`; });
    document.querySelectorAll(".nav-item").forEach((button) => {
      const selected = button.dataset.view === name;
      button.classList.toggle("active", selected);
      if (selected) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
    });
    $("breadcrumb-view").textContent = viewNames[name];
    document.title = `${viewNames[name]} · 颖纱客户线索`;
    if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
    if (focus) { $("main").focus({ preventScroll: true }); window.scrollTo({ top: 0, behavior: "instant" }); }
    if (name === "jobs" || (name === "search" && state.jobsLoaded)) loadJobs().catch((error) => report(errorMessage(error)));
    if (name === "leads") loadLeads().catch((error) => report(errorMessage(error)));
  }

  function selectedTab() { return array(state.browser?.tabs).find((tab) => tab.targetId === state.targetId); }

  function needsFacebookSearch(action) { return action === "import"; }
  function facebookTabReason(tab) {
    if (!tab?.url) return "地址未能读取，请在该页重新连接扩展后刷新";
    try {
      const url = new URL(tab.url);
      const host = url.hostname.toLowerCase().replace(/^www\./, "");
      const path = url.pathname.replace(/\/$/, "");
      const query = url.searchParams.get("q")?.trim();
      if (!['http:', 'https:'].includes(url.protocol)) return "不是公开网页";
      if (host === "facebook.com") {
        if (path === "/search/pages" && query) return "";
        if (path.startsWith("/search")) return "请切换到 Facebook 的“主页 / Pages”搜索分类";
        if (!path || path === "/home.php") return "Facebook 首页，请先搜索并选择“主页 / Pages”";
        return "单个主页或内容页，不能导入搜索列表；企业主页可使用“保存当前企业”";
      }
      if ((host === "bing.com" || /^google\.[a-z.]{2,10}$/.test(host)) && path === "/search" && query) return "";
      return "不是 Facebook 主页搜索或 Google / Bing 搜索结果页";
    } catch { return "地址无效，请重新连接标签页"; }
  }
  function browserChoiceAllowed(tab, action = state.pendingFacebookAction) {
    return Boolean(tab && (!needsFacebookSearch(action) || !facebookTabReason(tab)));
  }
  function updateBrowserConfirmation() {
    const tab = array(state.browser?.tabs).find((item) => item.targetId === $("browser-target").value);
    $("confirm-browser").disabled = !state.browser?.connected || $("browser-target").disabled || !browserChoiceAllowed(tab);
  }

  function updateBrowserSelection() {
    const tab = selectedTab();
    const selected = Boolean(tab && state.browser?.connected);
    const label = selected ? tab.title || siteName(tab.url) : "尚未选择浏览器标签页";
    $("selected-tab-label").textContent = label;
    $("selected-tab-label").title = selected ? tab.url || label : label;
    $("selected-tab-label").previousElementSibling.classList.toggle("connected", selected);
    $("browser-dot").classList.toggle("connected", selected);
    $("browser-button-label").textContent = selected ? "浏览器已连接" : "连接浏览器";
    $("job-browser-target").textContent = selected ? `所选页面：${label}` : "尚未选择标签页，请先点击右上角“连接浏览器”。";
    $("facebook-target-label").textContent = selected ? `所选页面：${label}` : "尚未选择浏览器标签页";
    $("facebook-target-label").title = selected ? tab.url || label : label;
    $("facebook-browser-dot").classList.toggle("connected", selected);
    renderFacebook();
    renderJobs();
  }

  async function refreshBrowser(action = state.pendingFacebookAction) {
    $("browser-message").textContent = "正在检查 OpenClaw 连接和已授权的标签页…";
    $("confirm-browser").disabled = true;
    $("browser-target").disabled = true;
    try {
      const data = await api("/api/browser");
      if ($("browser-dialog").open) action = state.pendingFacebookAction;
      state.browser = data;
      const tabs = array(data.tabs);
      const previous = state.targetId;
      $("browser-target").replaceChildren(new Option("请选择一个标签页", ""));
      const searchOnly = needsFacebookSearch(action);
      tabs.forEach((tab) => {
        const reason = searchOnly ? facebookTabReason(tab) : "";
        const option = new Option(`${tab.title || "未命名页面"} · ${siteName(tab.url)}${reason ? ` — 不可用于导入：${reason}` : searchOnly ? " — 搜索结果，可导入" : ""}`, tab.targetId);
        option.disabled = Boolean(reason);
        $("browser-target").append(option);
      });
      if (data.connected && tabs.some((tab) => tab.targetId === previous && browserChoiceAllowed(tab, action))) $("browser-target").value = previous;
      else state.targetId = "";
      $("browser-target").disabled = !data.connected || !tabs.length;
      updateBrowserConfirmation();
      const eligible = tabs.filter((tab) => browserChoiceAllowed(tab, action)).length;
      $("browser-message").textContent = searchOnly && data.connected && tabs.length
        ? `本次导入只接受搜索结果页：已找到 ${eligible} 个。单个主页及无法识别地址的页面已禁用。请打开 Facebook“主页 / Pages”搜索结果并连接扩展后刷新。`
        : data.message || (data.connected && tabs.length ? `已找到 ${tabs.length} 个可用标签页，请选择要操作的页面。` : "尚未发现已授权标签页。请在 Chrome 中打开页面并连接 OpenClaw 扩展，再点击刷新。");
      updateBrowserSelection();
    } catch (error) {
      state.browser = null; state.targetId = ""; updateBrowserSelection();
      $("browser-target").replaceChildren(new Option("连接失败，请刷新重试", ""));
      $("browser-target").disabled = true;
      $("browser-message").textContent = errorMessage(error);
      throw error;
    }
  }

  function closeDialog(dialog) { if (dialog.id === "browser-dialog") state.pendingFacebookAction = ""; dialog.close(); dialog.querySelector(".dialog-feedback")?.remove(); }
  function openBrowserDialog(action = "", refresh = true) {
    state.pendingFacebookAction = action;
    $("browser-dialog-title").textContent = needsFacebookSearch(action) ? "选择可导入的搜索结果页" : "选择已授权的标签页";
    $("browser-dialog").querySelector(".dialog-feedback")?.remove();
    $("browser-dialog").showModal();
    if (refresh) busy($("refresh-browser"), "检查中…", refreshBrowser);
    else updateBrowserConfirmation();
  }
  function requireTab() { if (!selectedTab() || !state.browser?.connected) throw new Error("请先点击右上角“连接浏览器”，并选择已授权的目标标签页。"); return state.targetId; }
  function searchContext() { return { product: $("product").value.trim(), country: $("country").value.trim() }; }
  function searchCriteria() { return { ...searchContext(), buyer_type: $("buyer-type").value.trim() }; }

  function renderSearchEntries(queries) {
    const help = {
      facebook: ["直接打开 Facebook，自动带入上面的搜索内容。", "点击下方按钮即可查看主页搜索结果。需要采集时，再使用下面的“导入当前搜索结果”。"],
      web: ["通过 Google 查找企业官网。", "连接搜索结果页 → 下方“从当前页提取候选” → 勾选官网并采集。"],
      instagram: ["通过 Google 查找 Instagram 企业主页。", "打开具体企业主页并连接 → 下方“保存当前企业”，逐个采集。"],
    };
    $("search-results").replaceChildren();
    queries.forEach((query) => {
      const card = el("article", "query-card"); card.dataset.kind = query.kind || "web";
      const anchor = link(query.url, "", "query-link");
      const content = el("div");
      content.append(el("strong", "", query.channel || "网页搜索"), el("p", "", query.query));
      const arrow = el("span", "", "↗"); arrow.setAttribute("aria-hidden", "true");
      anchor.replaceChildren(content, arrow); card.append(anchor);
      const [destination, next] = help[query.kind] || help.web;
      card.append(el("p", "query-destination", destination), el("p", "query-next-step", `下一步：${next}`));
      if (query.kind === "facebook") {
        const openSearch = link(query.url, "打开 Facebook 并搜索 ↗", "button primary query-continue");
        openSearch.id = "facebook-search-open";
        card.append(openSearch);
      }
      $("search-results").append(card);
    });
    if (!queries.length) empty($("search-results"), "暂未生成搜索入口", "请补充产品、客户类型和目标市场，再试一次。", "⌕");
    renderFacebook();
  }

  function selectedFacebookSession() { return state.facebookSessions.find((session) => String(session.id) === state.facebookSessionId); }
  function selectFacebookSession(id) {
    state.facebookSessionId = id ? String(id) : "";
    try { localStorage.setItem("yingsha-facebook-session", state.facebookSessionId); } catch { /* Persistence is optional in restricted browser contexts. */ }
  }

  async function loadFacebookSessions(fresh = false) {
    if (state.facebookRequest) {
      if (!fresh) return state.facebookRequest;
      await state.facebookRequest.catch(() => {});
    }
    state.facebookRequest = (async () => {
      try {
        const data = await api("/api/facebook/sessions");
        state.facebookSessions = array(data.sessions); state.facebookLoaded = true;
        if (!selectedFacebookSession()) selectFacebookSession(state.facebookSessions[0]?.id);
        renderFacebook();
      } catch (error) {
        state.facebookLoaded = false; renderFacebook();
        if (!state.facebookSessions.length) $("facebook-session-summary").replaceChildren(el("p", "muted small", "搜索会话暂时无法加载，请点击“刷新会话”重试。"));
        throw error;
      } finally { state.facebookRequest = null; }
    })();
    return state.facebookRequest;
  }

  function rememberFacebookSession(session) {
    state.facebookSessions = [session, ...state.facebookSessions.filter((item) => String(item.id) !== String(session.id))];
    state.facebookLoaded = true; selectFacebookSession(session.id); renderFacebook();
  }

  function jobActionButtons(job) {
    const actions = el("div", "button-group");
    const actionTypes = ["queued", "running"].includes(job.status) ? ["pause", "stop"] : ["paused", "blocked"].includes(job.status) ? ["resume", "stop"] : [];
    actionTypes.forEach((action) => {
      const button = el("button", "button compact", { pause: "暂停", resume: job.mode === "facebook" ? "继续本批" : "继续采集", stop: "停止" }[action]);
      const wrongTarget = job.mode === "facebook" && action === "resume" && (!selectedTab() || !state.browser?.connected || job.target_id !== state.targetId);
      button.type = "button";
      button.disabled = state.jobActions.has(job.id) || (job.mode === "facebook" && state.facebookBusy) || wrongTarget;
      if (wrongTarget) button.title = "请重新连接本批原标签页，再继续本批；切换标签页不会转移任务。";
      button.addEventListener("click", () => busy(button, "处理中…", async () => {
        if (state.jobActions.has(job.id)) return;
        const payload = {};
        if (job.mode === "facebook" && action === "resume") {
          payload.target_id = requireTab();
          if (payload.target_id !== job.target_id) throw new Error("所选标签页与本批原标签页不同。请重新连接原标签页，或停止本批后在新标签页继续下一批。");
        }
        state.jobActions.add(job.id); renderJobs(); renderFacebook();
        try {
          await api(`/api/jobs/${encodeURIComponent(job.id)}/${action}`, { method: "POST", body: JSON.stringify(payload) });
          await loadJobs(true); await loadStatus(); announce("任务状态已更新。");
        } finally { state.jobActions.delete(job.id); renderJobs(); renderFacebook(); }
      })); actions.append(button);
    });
    return actions;
  }

  function renderFacebook() {
    const session = selectedFacebookSession();
    const activeJob = session?.active_job_id ? state.jobs.find((job) => String(job.id) === String(session.active_job_id)) : null;
    const browserBusy = state.jobs.some((job) => ["browser", "facebook"].includes(job.mode) && ["queued", "running"].includes(job.status));
    const busyNow = state.facebookBusy || state.captureBusy;
    const canUseTab = Boolean(selectedTab() && state.browser?.connected);
    const locked = busyNow || browserBusy || !state.facebookLoaded;
    $("facebook-retry").hidden = number(session?.failed) <= 0;
    $("facebook-import").disabled = locked || Boolean(session?.active_job_id);
    $("facebook-next").disabled = locked || !session || Boolean(session.active_job_id) || number(session.remaining) <= 0;
    $("facebook-retry").disabled = locked || !session || Boolean(session.active_job_id) || number(session.failed) <= 0;
    $("facebook-session").disabled = busyNow || !state.facebookSessions.length;
    $("facebook-refresh").disabled = busyNow;
    const firstBatch = number(session?.processed) === 0;
    $("facebook-next").textContent = state.facebookBusy === "next" ? "正在开启采集…" : session?.active_job_id ? "本批尚未结束" : firstBatch ? "开始采集（最多 10 个）" : "继续下 10 个";
    $("facebook-batch-hint").textContent = !session ? "请先完成第 1 步，导入搜索结果。" : session.active_job_id ? "本批进度与暂停、停止操作在下方。" : number(session.remaining) > 0 ? `待处理 ${number(session.remaining)} 个，本批最多采集 ${Math.min(10, number(session.remaining))} 个。沿用所选会话的搜索条件。` : "候选已处理完。需要更多结果时，返回第 1 步重新导入。";
    $("facebook-retry").textContent = state.facebookBusy === "retry" ? "正在创建失败项重试…" : "重试失败项（最多10个）";
    $("facebook-import").textContent = state.facebookBusy === "import" ? "正在导入搜索结果…" : "导入当前搜索结果";
    ["facebook-next", "facebook-retry", "facebook-import"].forEach((id) => $(id).setAttribute("aria-busy", String(Boolean(state.facebookBusy))));
    $("capture-candidates").disabled = busyNow || browserBusy;
    $("capture-lead").disabled = busyNow || browserBusy;
    const picker = $("facebook-session");
    picker.replaceChildren();
    if (!state.facebookSessions.length) picker.append(new Option(state.facebookLoaded ? "尚无搜索会话，先导入当前搜索结果" : "正在加载搜索会话…", ""));
    state.facebookSessions.forEach((item) => picker.append(new Option(`${item.product || "未设产品"} · ${item.buyer_type || "未设客户类型"} · ${item.country || "未设市场"} · 剩余 ${number(item.remaining)} · #${item.id}`, String(item.id))));
    picker.value = state.facebookSessionId;
    const summary = $("facebook-session-summary"); summary.replaceChildren();
    if (!session) {
      summary.append(el("p", "muted small", state.facebookLoaded ? "导入成功后，这里会显示候选数量、搜索条件和采集进度。" : "正在读取已保存的候选与进度…"));
      return;
    }
    const heading = el("div", "facebook-session-heading");
    heading.append(el("strong", "", `搜索会话 #${session.id}`), chip(session.active_job_id ? jobStatuses[session.active_status] || "本批处理中" : number(session.remaining) > 0 ? "可继续下一批" : "当前候选已处理完", session.active_job_id ? statusColors[session.active_status] : "neutral"));
    summary.append(heading);
    const context = el("dl", "facebook-context");
    [["产品关键词", session.product], ["客户类型", session.buyer_type], ["目标市场", session.country]].forEach(([label, value]) => { const field = el("div"); field.append(el("dt", "", label), el("dd", "", value || "未设定")); context.append(field); });
    summary.append(context);
    const source = el("div", "facebook-source"); source.append(el("span", "", "原搜索页："), link(session.source_url, session.source_url)); summary.append(source);
    summary.append(el("p", "facebook-counts", `候选总数 ${number(session.total)} · 已处理 ${number(session.processed)} · 剩余 ${number(session.remaining)}`));
    const outcomes = el("div", "facebook-outcomes");
    outcomes.append(chip(`符合规则 ${number(session.matched)}`, "green"), chip(`待复核 ${number(session.review)}`, "orange"), chip(`已跳过 ${number(session.skipped)}`), chip(`失败 ${number(session.failed)}`, number(session.failed) ? "red" : "neutral")); summary.append(outcomes);
    if (number(session.failed) > 0) summary.append(el("p", "field-help", "失败项不会自动重试。确认浏览器连接或页面问题已处理后，可点击“重试失败项”，每批最多重试 10 个。"));
    if (session.active_job_id) {
      const batch = el("div", "facebook-active-batch");
      batch.append(el("strong", "", `当前批次 #${session.active_job_id}${activeJob ? ` · 最多 ${number(activeJob.total)} 个候选` : ""}`));
      const activeStatus = activeJob?.status || session.active_status;
      if (["queued", "running"].includes(activeStatus)) {
        const loading = el("div", "facebook-batch-loading");
        const spinner = el("span", "facebook-loading-spinner"); spinner.setAttribute("aria-hidden", "true");
        const detail = el("div", "facebook-loading-detail");
        detail.append(el("strong", "", activeStatus === "queued" ? "准备采集，请稍候…" : "正在采集，请稍候…"));
        if (activeJob) {
          const total = number(activeJob.total);
          const processed = Math.min(total, number(activeJob.done) + number(activeJob.review) + number(activeJob.skipped) + number(activeJob.failed));
          detail.append(el("p", "facebook-loading-count", `本批已处理 ${processed} / ${total} 个候选`));
          const progress = el("progress", "job-progress");
          progress.max = Math.max(1, total); progress.value = processed;
          progress.setAttribute("aria-label", "本批采集进度");
          detail.append(progress);
        } else detail.append(el("p", "facebook-loading-count", "正在读取本批进度…"));
        detail.append(el("p", "field-help", "正在逐个检查企业主页，结果会自动保存。"));
        loading.append(spinner, detail); batch.append(loading);
      } else if (activeJob?.message) batch.append(el("p", "small muted", activeJob.message));
      if (activeJob && ["paused", "blocked"].includes(activeJob.status)) {
        const targetMatches = canUseTab && activeJob.target_id === state.targetId;
        batch.append(el("p", "field-help", targetMatches ? "继续本批会从未完成候选接着处理，仍使用当前所选的原标签页。" : "请连接本批原标签页后继续；切换标签页不会转移任务。也可停止本批，再用新标签页继续下一批。"));
      }
      if (activeJob) batch.append(jobActionButtons(activeJob));
      else batch.append(el("p", "muted small", "正在读取本批任务，请刷新会话查看控制按钮。"));
      summary.append(batch);
    } else if (number(session.remaining) <= 0) {
      summary.append(el("p", "inline-note", "这批候选已处理完。继续找同类客户：打开原搜索页，手动加载更多结果后重新导入。换产品或市场：修改上方条件、生成新入口，再选择新结果页并导入。"));
    } else {
      summary.append(el("p", "field-help", `下一批最多处理 ${Math.min(10, number(session.remaining))} 个候选，实际保存数量取决于筛选结果。继续时沿用本会话的产品、客户类型和市场。`));
    }
  }

  function facebookFeedback(message, success = false) {
    const feedback = $("facebook-feedback");
    feedback.textContent = message;
    feedback.classList.toggle("success", success);
    feedback.hidden = false;
    report(message, success);
    if (!document.querySelector("dialog[open]")) feedback.scrollIntoView({ block: "nearest", behavior: "instant" });
  }

  async function facebookAction(action) {
    if (state.facebookBusy) return;
    const button = $({ next: "facebook-next", retry: "facebook-retry", import: "facebook-import" }[action]);
    if (button.disabled) return;
    if (!selectedTab() || !state.browser?.connected || (needsFacebookSearch(action) && facebookTabReason(selectedTab()))) {
      facebookFeedback(needsFacebookSearch(action) ? "请先选择可导入的搜索结果页，单个商家主页不能用于批量导入。确认后会继续刚才的操作。" : "请选择已连接的标签页；确认后会继续处理保存的候选。");
      openBrowserDialog(action);
      return;
    }
    state.facebookBusy = action; renderFacebook();
    try {
      if (needsFacebookSearch(action)) {
        // Tab titles/addresses can change after selection; recheck metadata only.
        await refreshBrowser(action);
        if (!selectedTab() || facebookTabReason(selectedTab())) {
          facebookFeedback("所选页面已改变或无法识别，请重新选择搜索结果页。");
          openBrowserDialog(action, false);
          return;
        }
      }
      const target = requireTab();
      facebookFeedback(action === "import" ? "正在读取所选标签页的搜索结果，请稍候…" : "正在读取保存的候选并创建批次，请稍候…", true);
      let session = selectedFacebookSession();
      if (action === "next" || action === "retry") {
        const requestedSessionId = state.facebookSessionId;
        await loadFacebookSessions(true);
        session = selectedFacebookSession();
        if (state.facebookSessionId !== requestedSessionId) throw new Error("原搜索会话已不可用，请重新选择会话后再继续。");
      } else {
        if (!$("search-form").reportValidity()) { facebookFeedback("请完整填写上方产品关键词、客户类型和目标市场。"); return; }
        const data = await api("/api/facebook/import", { method: "POST", body: JSON.stringify({ target_id: target, ...searchContext(), buyer_type: $("buyer-type").value.trim() }) });
        session = data.session; rememberFacebookSession(session);
      }
      const available = number(action === "retry" ? session?.failed : session?.remaining);
      if (action !== "import" && session && !session.active_job_id && available > 0) {
        const data = await api(`/api/facebook/sessions/${encodeURIComponent(session.id)}/${action === "retry" ? "retry" : "next"}`, { method: "POST", body: JSON.stringify({ target_id: target }) });
        state.jobs = [data.job, ...state.jobs.filter((job) => String(job.id) !== String(data.job.id))];
        rememberFacebookSession(data.session);
        facebookFeedback(action === "retry" ? `失败项重试已开始，本批最多重试 ${number(data.job.total)} 个候选。` : `本批已开始，最多处理 ${number(data.job.total)} 个候选，符合规则和待复核的资料会保存。`, true);
      } else if (session?.active_job_id) {
        facebookFeedback("当前会话已有未完成批次，请使用“继续本批”、暂停或停止。");
      } else if (action === "retry") {
        facebookFeedback("当前会话已没有可重试的失败项。", true);
      } else {
        facebookFeedback(number(session?.remaining) > 0 ? "搜索结果已导入。请在第 2 步确认会话，再开始采集。" : "当前没有待处理候选。请返回原搜索页加载更多，再导入当前搜索结果。", true);
      }
      await loadJobs(true); await loadStatus();
    } catch (error) {
      facebookFeedback(errorMessage(error));
      await loadJobs(true).catch(() => {});
    } finally { state.facebookBusy = false; renderFacebook(); renderJobs(); }
  }

  function renderCandidates() {
    const list = $("candidate-list"); list.replaceChildren();
    $("candidate-section").hidden = false;
    $("candidate-count").textContent = `共 ${state.candidates.length} 个候选网址`;
    if (!state.candidates.length) empty(list, "当前页面未提取到候选网址", "请确认页面已加载搜索结果，或手动复制企业网址到采集任务。", "⌕");
    state.candidates.forEach((candidate, index) => {
      const row = el("div", "candidate-row");
      const checkbox = el("input"); checkbox.type = "checkbox"; checkbox.id = `candidate-${index}`; checkbox.dataset.index = String(index);
      checkbox.addEventListener("change", updateCandidateSelection);
      const content = el("div"); const label = el("label", "", candidate.title || siteName(candidate.url)); label.htmlFor = checkbox.id;
      content.append(label, link(candidate.url)); row.append(checkbox, content, chip(candidate.channel || "网页")); list.append(row);
    });
    updateCandidateSelection();
  }

  function checkedCandidates() { return [...$("candidate-list").querySelectorAll("input:checked")].map((input) => state.candidates[Number(input.dataset.index)]); }
  function updateCandidateSelection() {
    const count = checkedCandidates().length;
    $("candidate-selection-label").textContent = `已选择 ${count} 个`;
    $("candidate-select-all").checked = count > 0 && count === state.candidates.length;
    $("candidate-select-all").indeterminate = count > 0 && count < state.candidates.length;
    $("candidate-select-all").disabled = !state.candidates.length;
    $("prepare-candidates").disabled = count === 0;
  }

  async function capture(kind, button) {
    if (state.captureBusy || state.facebookBusy) return;
    await busy(button, "正在读取页面…", async () => {
      const target = requireTab(); state.captureBusy = true; renderFacebook();
      const other = kind === "lead" ? $("capture-candidates") : $("capture-lead"); other.disabled = true;
      try {
        const data = await api("/api/capture", { method: "POST", body: JSON.stringify({ target_id: target, kind, ...searchContext() }) });
        if (kind === "candidates") {
          const seen = new Set();
          state.candidates = array(data.candidates).filter((candidate) => { const url = safeUrl(candidate.url); if (!url || seen.has(url)) return false; seen.add(url); return true; });
          renderCandidates(); announce(`提取到 ${state.candidates.length} 个候选网址。`);
        } else {
          report(data.message || "当前企业已保存，可在客户线索中查看来源和联系方式。", true);
          await loadStatus();
        }
      } finally { state.captureBusy = false; other.disabled = false; renderFacebook(); }
    });
    renderFacebook();
  }

  function scheduleJobs() {
    clearTimeout(state.pollTimer);
    if (!document.hidden && (state.jobs.some((job) => ["queued", "running"].includes(job.status)) || state.facebookSessions.some((session) => ["queued", "running"].includes(session.active_status)))) {
      state.pollTimer = setTimeout(() => loadJobs().catch((error) => report(errorMessage(error))), 3000);
    }
  }

  async function loadJobs(fresh = false) {
    if (state.jobsRequest) {
      if (!fresh) return state.jobsRequest;
      await state.jobsRequest.catch((error) => report(errorMessage(error)));
    }
    state.jobsRequest = (async () => {
      try {
        const wasActive = state.jobs.some((job) => ["queued", "running"].includes(job.status));
        const data = await api("/api/jobs");
        state.jobs = array(data.jobs); state.jobsLoaded = true; renderJobs();
        await loadFacebookSessions(true);
        if (wasActive) {
          await loadStatus();
          if (state.view === "leads") await loadLeads();
        }
      } catch (error) {
        clearTimeout(state.pollTimer);
        if (!state.jobsLoaded) empty($("jobs-list"), "任务暂时无法加载", errorMessage(error), "!", { label: "重新加载", run: () => loadJobs().catch((problem) => report(errorMessage(problem))) });
        throw error;
      } finally { state.jobsRequest = null; scheduleJobs(); }
    })();
    return state.jobsRequest;
  }

  function renderJobs() {
    const list = $("jobs-list");
    const openIds = new Set([...list.querySelectorAll("details[open]")].map((detail) => detail.dataset.jobId));
    list.replaceChildren(); $("job-total").textContent = state.jobs.length ? String(state.jobs.length) : "";
    $("nav-job-count").textContent = state.jobs.filter((job) => ["queued", "running", "paused", "blocked"].includes(job.status)).length;
    if (!state.jobs.length) { empty(list, "还没有采集任务", "粘贴企业网址，或从搜索结果提取候选网址后开始采集。", "▤"); return; }
    state.jobs.forEach((job) => {
      const card = el("article", "job-card"); const header = el("div", "job-heading"); const info = el("div"); const title = el("div", "job-title");
      const facebook = job.mode === "facebook";
      title.append(el("h3", "", facebook ? "Facebook 企业筛选" : job.mode === "browser" ? "已登录浏览器采集" : "官网自动采集"), chip(jobStatuses[job.status] || job.status, statusColors[job.status]));
      info.append(title, el("p", "job-meta", `${dateLabel(job.created_at)} · ${number(job.total)} 个${facebook ? `候选 · 搜索会话 #${job.session_id}` : "网址"}`)); header.append(info);
      header.append(jobActionButtons(job)); card.append(header);
      if (facebook) {
        card.append(el("p", "job-meta", [job.product, job.buyer_type, job.country].filter(Boolean).join(" · ")));
        if (["paused", "blocked"].includes(job.status) && (!selectedTab() || !state.browser?.connected || job.target_id !== state.targetId)) card.append(el("p", "field-help", "继续本批需要重新连接本批原标签页；换标签页后，可先停止本批，再从搜索会话继续下一批。"));
      }
      if (job.message) card.append(el("p", "job-message", job.message));
      const processed = number(job.done) + number(job.failed) + (facebook ? number(job.review) + number(job.skipped) : 0);
      const progressRow = el("div", "job-progress-row"); const progress = el("progress", "job-progress"); progress.max = Math.max(1, number(job.total)); progress.value = Math.min(processed, progress.max); progress.setAttribute("aria-label", "已处理网址进度");
      progressRow.append(progress, el("span", "", facebook ? `已处理 ${processed} / ${number(job.total)} · 符合规则 ${number(job.done)} · 待复核 ${number(job.review)} · 跳过 ${number(job.skipped)} · 失败 ${number(job.failed)}` : `成功 ${number(job.done)} / ${number(job.total)} · 失败 ${number(job.failed)}`)); card.append(progressRow);
      if (array(job.items).length) {
        const details = el("details", "job-items"); details.dataset.jobId = job.id; details.open = openIds.has(String(job.id)); details.append(el("summary", "", "查看网址与处理结果"));
        const items = el("ul", "job-item-list"); array(job.items).forEach((item) => { const li = el("li"); li.append(link(item.url), el("span", "", facebook && item.status === "done" ? "符合规则" : jobStatuses[item.status] || item.status)); if (item.message) li.append(el("span", "item-message", item.message)); items.append(li); }); details.append(items); card.append(details);
      }
      list.append(card);
    });
  }

  function leadParams(page = state.page) { const params = new URLSearchParams({ q: state.filters.q, status: state.filters.status }); if (page) params.set("page", String(page)); return params; }

  async function loadLeads() {
    const request = ++state.leadRequest;
    $("leads-content").setAttribute("aria-busy", "true");
    try {
      const data = await api(`/api/leads?${leadParams()}`);
      if (request !== state.leadRequest) return;
      state.total = number(data.total); state.page = number(data.page) || 1; state.pageSize = number(data.page_size) || 25;
      if (state.page > 1 && !array(data.items).length && state.total > 0) { state.page = Math.ceil(state.total / state.pageSize); return await loadLeads(); }
      renderLeads(array(data.items));
    } catch (error) {
      if (request !== state.leadRequest) return;
      $("lead-total").textContent = "线索加载失败"; $("lead-pagination").hidden = true;
      empty($("leads-content"), "客户线索暂时无法加载", errorMessage(error), "!", { label: "重新加载", run: () => loadLeads().catch((problem) => report(errorMessage(problem))) });
      throw error;
    } finally { if (request === state.leadRequest) $("leads-content").removeAttribute("aria-busy"); }
  }

  function renderLeads(leads) {
    const container = $("leads-content"); container.replaceChildren();
    $("lead-total").textContent = `共 ${state.total.toLocaleString("zh-CN")} 条客户线索`;
    $("lead-pagination").hidden = state.total <= state.pageSize;
    $("page-label").textContent = `第 ${state.page} / ${Math.max(1, Math.ceil(state.total / state.pageSize))} 页`;
    $("previous-page").disabled = state.page <= 1; $("next-page").disabled = state.page * state.pageSize >= state.total;
    if (!leads.length) {
      const filtered = state.filters.q || state.filters.status;
      empty(container, filtered ? "没有符合条件的线索" : "第一条线索，从一次搜索开始", filtered ? "试试其他关键词，或清除跟进状态筛选。" : "采集企业官网或主页后，公开联系方式和来源会整理在这里。", "▦", { label: filtered ? "清除筛选" : "去搜索找客户", run: () => { if (filtered) { $("lead-search").value = ""; $("lead-status-filter").value = ""; applyFilters(); } else showView("search", true); } });
      return;
    }
    const table = el("table", "leads-table"); table.setAttribute("aria-label", "客户线索列表"); const head = el("thead"); const headRow = el("tr");
    ["企业 / 来源", "公开联系方式", "搜索目标", "规则匹配", "跟进状态"].forEach((label) => { const th = el("th", "", label); th.scope = "col"; headRow.append(th); }); head.append(headRow); table.append(head);
    const body = el("tbody");
    leads.forEach((lead) => {
      const row = el("tr"); const company = el("td"); const name = el("button", "lead-name", lead.name || siteName(lead.website || lead.source_url)); name.type = "button"; name.addEventListener("click", () => openLead(lead.id));
      company.append(name, link(lead.website || lead.source_url, siteName(lead.website || lead.source_url), "lead-url"));
      const contact = el("td"); const values = [...array(lead.emails).slice(0, 1), ...array(lead.whatsapp).map((item) => `WhatsApp: ${item}`).slice(0, 1)]; if (!values.length) values.push(...array(lead.phones).slice(0, 1)); if (!values.length) values.push("未提取到联系方式"); values.forEach((item) => contact.append(el("span", "lead-contact", item)));
      const country = el("td", "", lead.target_country || "未设定"); const score = el("td"); score.append(el("span", "score-number", number(lead.score)), el("span", "score-caption", "规则匹配"));
      if (screeningLabels[lead.screening]) score.append(el("div", "lead-screening", screeningLabels[lead.screening]));
      const status = el("td"); status.append(chip(leadStatuses[lead.status] || lead.status || "待跟进", statusColors[lead.status])); const detail = el("button", "lead-row-action", "查看详情 →"); detail.type = "button"; detail.setAttribute("aria-label", `查看 ${lead.name || "此客户"} 的详情`); detail.addEventListener("click", () => openLead(lead.id)); status.append(el("br"), detail);
      row.append(company, contact, country, score, status); body.append(row);
    }); table.append(body); container.append(table);
  }

  function detailField(label, value) { const field = el("div"); field.append(el("div", "detail-field-label", label), el("div", "detail-field-value", value || "未记录")); return field; }
  function detailSection(title) { const section = el("section", "detail-section"); section.append(el("h3", "", title)); return section; }

  function contactField(label, values, kind) {
    const field = el("div"); field.append(el("div", "detail-field-label", label));
    const list = el("ul", "detail-list");
    if (!values.length) list.append(el("li", "muted", "未提取到"));
    values.forEach((value) => {
      const li = el("li");
      if (kind === "url") li.append(link(value));
      else if (kind === "email" && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) { const anchor = el("a", "", value); anchor.href = `mailto:${encodeURIComponent(value)}`; li.append(anchor); }
      else li.textContent = String(value);
      list.append(li);
    }); field.append(list); return field;
  }

  async function openLead(id) {
    const request = ++state.detailRequest;
    $("lead-detail-title").textContent = "正在加载客户详情";
    empty($("lead-detail-content"), "正在读取线索", "公开联系方式、匹配理由与来源将显示在这里。", "▦");
    $("lead-dialog").querySelector(".dialog-feedback")?.remove();
    if (!$("lead-dialog").open) $("lead-dialog").showModal();
    try {
      const data = await api(`/api/leads/${encodeURIComponent(id)}`);
      if (request !== state.detailRequest || !$("lead-dialog").open) return;
      state.currentLead = data.lead; renderLeadDetail(data.lead);
    } catch (error) {
      if (request !== state.detailRequest || !$("lead-dialog").open) return;
      $("lead-detail-title").textContent = "客户详情加载失败";
      empty($("lead-detail-content"), "暂时无法读取这条线索", errorMessage(error), "!", { label: "重新加载", run: () => openLead(id) }); report(errorMessage(error));
    }
  }

  function renderLeadDetail(lead) {
    $("lead-detail-title").textContent = lead.name || siteName(lead.website || lead.source_url);
    const content = $("lead-detail-content"); content.replaceChildren();
    if (lead.merge_notice) content.append(el("p", "detail-footnote", lead.merge_notice));
    if (lead.name_source) content.append(el("p", "detail-footnote", `名称依据：${lead.name_source}`));
    const summary = el("div", "detail-summary"); summary.append(link(lead.website || lead.source_url, lead.website || lead.source_url)); const score = el("div", "detail-score"); score.append(el("strong", "", number(lead.score)), el("span", "", "规则匹配 · 不代表采购意向")); summary.append(score); content.append(summary);
    const info = detailSection("线索背景"); const infoGrid = el("div", "detail-grid"); infoGrid.append(detailField("来源渠道", lead.channel || "网页"), detailField("搜索目标市场（非已核实所在地）", lead.target_country), detailField("页面中的地区证据", lead.country_evidence)); if (lead.buyer_type) infoGrid.append(detailField("搜索客户类型", lead.buyer_type)); info.append(infoGrid); content.append(info);
    if (screeningLabels[lead.screening]) {
      const screening = detailSection("自动筛选结果");
      screening.append(chip(screeningLabels[lead.screening], lead.screening === "matched" ? "green" : "orange"), el("p", "detail-field-value", lead.screening_reason || "请核对原始页面后决定是否跟进。"), el("p", "detail-footnote", "这是本次采集的规则判断，跟进状态由你单独维护。")); content.append(screening);
    }
    const contacts = detailSection("公开商务联系方式"); const contactGrid = el("div", "detail-grid"); contactGrid.append(contactField("电子邮箱", array(lead.emails), "email"), contactField("电话", array(lead.phones)), contactField("WhatsApp", array(lead.whatsapp)), contactField("页面中的社交链接", array(lead.social_links), "url")); contacts.append(contactGrid, el("p", "detail-footnote", "以上信息来自已采集页面。联系前请核对来源，确认信息仍有效。")); content.append(contacts);
    const evidence = detailSection("匹配依据"); const tags = el("div", "detail-tags"); array(lead.keywords).forEach((keyword) => tags.append(chip(keyword, "green"))); if (tags.childNodes.length) evidence.append(tags);
    const reasons = el("ul", "detail-list"); reasons.style.marginTop = "12px"; array(lead.reasons).forEach((reason) => reasons.append(el("li", "", reason))); if (!reasons.childNodes.length) reasons.append(el("li", "muted", "暂无匹配理由，请打开来源页面核实。")); evidence.append(reasons); content.append(evidence);
    const sources = detailSection("信息来源"); const sourceList = el("ul", "detail-list"); const sourceItems = array(lead.sources); if (!sourceItems.length && lead.source_url) sourceItems.push({ url: lead.source_url, captured_at: lead.created_at });
    sourceItems.forEach((source) => { const item = el("li", "source-entry"); item.append(link(source.url), el("small", "", `采集于 ${dateLabel(source.captured_at)}`)); sourceList.append(item); }); if (!sourceItems.length) sourceList.append(el("li", "muted", "暂无来源记录")); sources.append(sourceList); content.append(sources);
    const form = el("form", "detail-form"); const statusLabel = el("label", "", "跟进状态"); statusLabel.htmlFor = "detail-status"; const status = el("select"); status.id = "detail-status"; Object.entries(leadStatuses).forEach(([value, label]) => status.append(new Option(label, value))); status.value = lead.status || "new";
    const notesLabel = el("label", "", "跟进备注"); notesLabel.htmlFor = "detail-notes"; notesLabel.style.marginTop = "18px"; const notes = el("textarea"); notes.id = "detail-notes"; notes.rows = 4; notes.maxLength = 5000; notes.placeholder = "记录已核实的信息、联系进度或下一步计划…"; notes.value = lead.notes || "";
    const actions = el("div", "dialog-actions"); const updated = el("span", "detail-updated", `更新于 ${dateLabel(lead.updated_at)}`); const save = el("button", "button primary", "保存跟进记录"); save.type = "submit"; actions.append(updated, save); form.append(statusLabel, status, notesLabel, notes, actions);
    form.addEventListener("submit", (event) => { event.preventDefault(); busy(save, "正在保存…", async () => { const data = await api(`/api/leads/${encodeURIComponent(lead.id)}`, { method: "PATCH", body: JSON.stringify({ status: status.value, notes: notes.value }) }); state.currentLead = data.lead; updated.textContent = `更新于 ${dateLabel(data.lead.updated_at)}`; report("跟进记录已保存。", true); await loadLeads(); }); }); content.append(form);
  }

  function applyFilters() {
    state.filters = { q: $("lead-search").value.trim(), status: $("lead-status-filter").value };
    state.page = 1; return loadLeads().catch((error) => report(errorMessage(error)));
  }

  async function exportLeads(format, button) {
    await busy(button, "导出中…", async () => {
      const params = leadParams(null); params.set("format", format);
      const data = await api(`/api/export?${params}`, { download: true });
      const url = URL.createObjectURL(data.blob); const anchor = el("a"); anchor.href = url; anchor.download = `yingsha-leads-${new Date().toISOString().slice(0, 10)}.${format}`; document.body.append(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); announce("已导出当前筛选结果。");
    });
  }

  document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => showView(button.dataset.view, true)));
  window.addEventListener("hashchange", () => showView(location.hash.slice(1), true));
  $("dismiss-alert").addEventListener("click", () => { $("global-alert").hidden = true; });
  $("open-browser").addEventListener("click", () => openBrowserDialog());
  $("browser-dialog").addEventListener("cancel", () => { state.pendingFacebookAction = ""; });
  $("refresh-browser").addEventListener("click", () => busy($("refresh-browser"), "检查中…", refreshBrowser));
  $("close-browser").addEventListener("click", () => closeDialog($("browser-dialog")));
  $("browser-target").addEventListener("change", updateBrowserConfirmation);
  $("confirm-browser").addEventListener("click", () => {
    updateBrowserConfirmation();
    if ($("confirm-browser").disabled) return;
    const action = state.pendingFacebookAction;
    state.targetId = $("browser-target").value; updateBrowserSelection();
    closeDialog($("browser-dialog")); announce("已选择目标标签页。");
    if (action) facebookAction(action);
  });
  $("close-lead").addEventListener("click", () => { state.detailRequest++; closeDialog($("lead-dialog")); });
  document.querySelectorAll("dialog").forEach((dialog) => { dialog.addEventListener("click", (event) => { if (event.target !== dialog) return; const rect = dialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeDialog(dialog); }); dialog.addEventListener("close", () => dialog.querySelector(".dialog-feedback")?.remove()); });
  ["product", "buyer-type", "country"].forEach((id) => $(id).addEventListener("input", () => {
    if (state.searchKey && state.searchKey !== JSON.stringify(searchCriteria())) {
      $("search-results-section").hidden = true;
      $("search-changed").hidden = false;
    }
  }));
  $("search-form").addEventListener("submit", (event) => { event.preventDefault(); busy($("search-submit"), "正在生成搜索入口…", async () => {
    const criteria = searchCriteria(); const key = JSON.stringify(criteria);
    const data = await api("/api/search", { method: "POST", body: key });
    if (key !== JSON.stringify(searchCriteria())) {
      $("search-results-section").hidden = true; $("search-changed").hidden = false; return;
    }
    state.searchKey = key; $("search-changed").hidden = true;
    renderSearchEntries(array(data.queries)); $("search-results-section").hidden = false;
    $("job-product").value = criteria.product; $("job-country").value = criteria.country;
    announce(`已生成 ${array(data.queries).length} 个搜索入口。请选择渠道，打开结果后连接标签页。`);
    $("search-results-section").scrollIntoView({ block: "start", behavior: "smooth" });
  }); });
  $("capture-candidates").addEventListener("click", () => capture("candidates", $("capture-candidates")));
  $("capture-lead").addEventListener("click", () => capture("lead", $("capture-lead")));
  $("facebook-next").addEventListener("click", () => facebookAction("next"));
  $("facebook-retry").addEventListener("click", () => facebookAction("retry"));
  $("facebook-import").addEventListener("click", () => facebookAction("import"));
  $("facebook-session").addEventListener("change", () => { selectFacebookSession($("facebook-session").value); renderFacebook(); });
  $("facebook-refresh").addEventListener("click", () => busy($("facebook-refresh"), "刷新中…", async () => { await loadJobs(true); renderFacebook(); }));
  $("candidate-select-all").addEventListener("change", () => { $("candidate-list").querySelectorAll("input").forEach((input) => { input.checked = $("candidate-select-all").checked; }); updateCandidateSelection(); });
  $("prepare-candidates").addEventListener("click", () => { const selected = checkedCandidates(); $("job-urls").value = selected.map((candidate) => candidate.url).join("\n"); $("job-product").value = $("product").value; $("job-country").value = $("country").value; showView("jobs", true); announce(`已带入 ${selected.length} 个网址，请确认采集方式后开始。`); });
  document.querySelectorAll("input[name=job-mode]").forEach((input) => input.addEventListener("change", () => { $("browser-mode-note").hidden = document.querySelector("input[name=job-mode]:checked").value !== "browser"; }));
  $("job-form").addEventListener("submit", (event) => { event.preventDefault(); busy($("start-job"), "正在创建任务…", async () => {
    const raw = $("job-urls").value.split(/\r?\n/).map((url) => url.trim()).filter(Boolean); const urls = [...new Set(raw)];
    if (!urls.length) throw new Error("请至少填写一个企业网址。");
    if (urls.length > 100) throw new Error("每个任务最多采集 100 个网址，请分批添加。");
    const invalid = urls.find((url) => !safeUrl(url)); if (invalid) throw new Error(`网址格式不正确，请使用完整的 HTTP 或 HTTPS 地址：${invalid}`);
    const socialUrl = urls.find(isSocialBatchUrl);
    if (socialUrl) throw new Error(`此处批量任务仅用于企业官网，无法添加社交主页：${socialUrl}。Facebook 请使用“搜索找客户”中的每批 10 个入口；Instagram 请打开并确认企业主页，再点击“保存当前企业”。`);
    const mode = document.querySelector("input[name=job-mode]:checked").value;
    await api("/api/jobs", { method: "POST", body: JSON.stringify({ urls, mode, target_id: mode === "browser" ? requireTab() : "", product: $("job-product").value.trim(), country: $("job-country").value.trim() }) });
    $("job-urls").value = ""; report("采集任务已创建，处理结果会逐页保存。", true); await loadJobs(true); await loadStatus();
  }); });
  $("refresh-jobs").addEventListener("click", () => busy($("refresh-jobs"), "刷新中…", loadJobs));
  $("lead-filter-form").addEventListener("submit", (event) => { event.preventDefault(); busy($("filter-leads"), "筛选中…", applyFilters); });
  $("lead-status-filter").addEventListener("change", applyFilters);
  $("previous-page").addEventListener("click", () => { if (state.page > 1) { state.page--; loadLeads().catch((error) => report(errorMessage(error))); } });
  $("next-page").addEventListener("click", () => { if (state.page * state.pageSize < state.total) { state.page++; loadLeads().catch((error) => report(errorMessage(error))); } });
  $("export-csv").addEventListener("click", () => exportLeads("csv", $("export-csv")));
  $("export-xlsx").addEventListener("click", () => exportLeads("xlsx", $("export-xlsx")));
  document.addEventListener("visibilitychange", () => { if (document.hidden) clearTimeout(state.pollTimer); else loadJobs(true).catch((error) => report(errorMessage(error))); });

  showView(location.hash.slice(1));
  Promise.allSettled([loadStatus(), loadJobs()]).then((results) => results.forEach((result) => { if (result.status === "rejected") report(errorMessage(result.reason)); }));
})();
