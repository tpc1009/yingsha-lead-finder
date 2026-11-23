() => {
  const url = location.href;
  const path = location.pathname.toLowerCase();
  const visible = el => {
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.visibility === 'collapse' || style.display === 'none') return false;
    if (el.getClientRects().length) return true;
    // Facebook wraps business links in display:contents. The wrapper has no
    // rectangle of its own even though its children/text are on screen.
    if (style.display !== 'contents') return false;
    const range = document.createRange(); range.selectNodeContents(el);
    return !!range.getClientRects().length;
  };
  const text = (document.body?.innerText || '').slice(0, 25000);
  if (/(?:do not|don.t) have permission to (?:view|access)|you (?:can.t|cannot) access this (?:page|content)|您无权|你无权|没有权限(?:查看|访问)/i.test(text)) return {error: 'SECURITY_GATE'};
  if (/\/(login|accounts\/login|checkpoint|challenge|signin)(\/|$)/i.test(path) ||
      [...document.querySelectorAll('input[type="password"]')].some(visible) ||
      /just a moment|access denied|security check|unusual traffic/i.test(document.title) ||
      /verify you are human|confirm you are human|our systems have detected unusual traffic|temporarily blocked|we restrict certain activity|too many requests|请完成安全验证|请验证您是真人|检测到异常流量|请求过于频繁|操作过于频繁/i.test(text)) {
    return {error: 'SECURITY_GATE'};
  }
  if (/\/(direct|messages|messenger|inbox|settings|account|accounts|admin|oauth|followers|following|groups|members)(\/|$)/i.test(path)) return {error: 'PRIVATE_PAGE'};
  const social = /(^|\.)(facebook\.com|instagram\.com)$/.test(location.hostname);
  const businessSearch = /(^|\.)facebook\.com$/.test(location.hostname) && /^\/search\/pages\/?$/.test(path);
  if (social && /^\/(?:home\.php|friends(?:\/.*)?)?$/.test(path)) return {error: 'NOT_COMPANY_PAGE'};
  if (social && [...document.querySelectorAll('[role="dialog"],dialog[open]')].some(visible)) return {error: 'SOCIAL_DIALOG'};
  if (document.readyState === 'loading' || text.trim().length < 30) return {error: 'NOT_READY'};
  const source = social ? document.querySelector('main,[role="main"]') || document.body : document.body;
  if (!source) return {error: 'NOT_READY'};
  const copy = source.cloneNode(true);
  const originals = [...source.querySelectorAll('*')];
  const clones = [...copy.querySelectorAll('*')];
  originals.forEach((el, i) => {
    const clone = clones[i];
    if (!clone) return;
    if (!visible(el) || /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE|IFRAME|INPUT|TEXTAREA|SELECT|BUTTON)$/.test(el.tagName) ||
        el.isContentEditable || el.closest('[contenteditable="true"]') ||
        (social && ((!businessSearch && el.matches('article,[role="article"]')) || /comment|评论/i.test(el.getAttribute('aria-label') || '')))) clone.remove();
    else [...clone.attributes].forEach(a => {
      if (!['href','title','lang','role','rel'].includes(a.name)) clone.removeAttribute(a.name);
    });
  });
  const head = document.createElement('head');
  const title = document.createElement('title'); title.textContent = document.title; head.append(title);
  document.querySelectorAll('meta[name="description"],meta[property="og:title"],meta[property="og:site_name"],meta[property="og:description"]').forEach(m => head.append(m.cloneNode(true)));
  const html = '<html>' + head.outerHTML + '<body>' + copy.innerHTML + '</body></html>';
  if (html.length > 1500000) return {error: 'PAGE_TOO_LARGE'};
  return {url, html};
}
