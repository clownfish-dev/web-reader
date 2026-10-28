// Page-side readers, ported from web-reader 0.9.4 (search.mjs / server.mjs) so the results
// keep the same shape. Evaluated in Playwright's isolated world: they see the page's DOM and
// layout but not its scripts, and the page's scripts do not see them.
// Called as wr[name](arg) by read.py.
const wr = {
  // Visible text of the page or one element; text hidden from people (CSS tricks used for
  // prompt injection) is left out and counted.
  text({ sel, includeHidden }) {
    const root = (sel && document.querySelector(sel)) || document.body;
    if (!root) return { text: '', hidden_chars: 0 };
    const docW = document.documentElement.scrollWidth, docH = document.documentElement.scrollHeight;
    const isHidden = (el) => {
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden' || cs.visibility === 'collapse') return true;
      if (parseFloat(cs.opacity) < 0.1) return true;
      if (parseFloat(cs.fontSize) < 4) return true;
      if (cs.color === 'transparent' || /rgba\([^)]*,\s*0\)$/.test(cs.color)) return true;
      const r = el.getBoundingClientRect();
      if (r.width <= 1 && r.height <= 1 && cs.overflow === 'hidden') return true;
      if (/^rect\(0/.test(cs.clip) || cs.clipPath === 'inset(50%)' || cs.clipPath === 'inset(100%)') return true;
      const ax = r.left + scrollX, ay = r.top + scrollY;
      if ((cs.position === 'absolute' || cs.position === 'fixed') && (ax + r.width < -50 || ay + r.height < -50 || ax > docW + 50 || ay > docH + 50)) return true;
      if (cs.textIndent && parseFloat(cs.textIndent) < -500) return true;
      return false;
    };
    const hidden = new Set();
    for (const el of root.querySelectorAll('*')) {
      if (/^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE|SVG|IFRAME|LINK|META)$/i.test(el.tagName) || el.closest('svg')) continue;
      let p = el.parentElement, inside = false;
      while (p) { if (hidden.has(p)) { inside = true; break; } p = p.parentElement; }
      if (!inside && isHidden(el)) hidden.add(el);
    }
    // Clone, then drop the clones of hidden elements (same document order in both trees).
    const all = [...root.querySelectorAll('*')];
    const clone = root.cloneNode(true);
    const cAll = [...clone.querySelectorAll('*')];
    const drop = [];
    all.forEach((el, i) => { if (hidden.has(el)) drop.push(cAll[i]); });
    const hiddenText = drop.map((n) => (n.textContent || '').trim()).filter(Boolean).join('\n');
    if (!includeHidden) drop.forEach((n) => n.remove());
    clone.querySelectorAll('script,style,noscript,svg,iframe,template').forEach((n) => n.remove());
    const holder = document.createElement('div');
    holder.style.cssText = 'position:absolute;left:-99999px;top:0;width:1200px';
    holder.appendChild(clone);
    document.body.appendChild(holder);
    const t = holder.innerText;
    holder.remove();
    const out = { text: t.replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim(), hidden_chars: hiddenText.length };
    if (includeHidden) out.hidden_text = hiddenText.slice(0, 20000);
    return out;
  },

  // Main article text with Readability (loaded by read.py as __wrReadability).
  main() {
    try {
      const doc = document.cloneNode(true);
      doc.querySelectorAll('[hidden],[aria-hidden="true"],script,style,noscript,template,iframe').forEach((n) => n.remove());
      const a = new __wrReadability(doc, { charThreshold: 200 }).parse();
      if (!a) return null;
      const holder = document.createElement('div');
      holder.style.cssText = 'position:absolute;left:-99999px;top:0;width:1000px';
      holder.innerHTML = a.content;
      holder.querySelectorAll('script,style,iframe').forEach((n) => n.remove());
      document.body.appendChild(holder);
      const text = holder.innerText.replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
      holder.remove();
      return { title: a.title, byline: a.byline || undefined, published: a.publishedTime || undefined, site: a.siteName || undefined, text, html: a.content };
    } catch (e) { return null; }
  },

  // Only the parts about the keywords: a heading's section, dt->dd, th->td, a labelled
  // block; else text windows around the keyword.
  sections({ kws, maxEach }) {
    const vis = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return cs.display !== 'none' && cs.visibility !== 'hidden' && (r.width > 0 || r.height > 0); };
    // Line starts keep their spaces (code indentation); runs of blank lines and trailing spaces go.
    const clean = (s) => (s || '').replace(/(\S)[ \t]{2,}/g, '$1 ').replace(/[ \t]+\n/g, '\n').replace(/\n\s*\n(\s*\n)+/g, '\n\n').replace(/^\s*\n|\s+$/g, '');
    const norm = (s) => s.replace(/\s+/g, ' ').trim();
    const out = []; const seen = [];
    const add = (kw, how, label, text) => {
      text = clean(text); const n = norm(text); if (!n) return;
      // The same part found again (as a block, with its heading line, in a menu copy) is left out.
      if (seen.some((x) => x.includes(n) || (n.includes(x) && n.length < x.length * 1.5))) return;
      seen.push(n);
      const item = { keyword: kw, how, label: clean(label).slice(0, 120), text: text.slice(0, maxEach) };
      if (text.length > maxEach) item.truncated = true;
      out.push(item);
    };
    // A heading's level, also for wrappers that hold only a heading and its anchor (GitHub READMEs).
    const level = (n) => {
      if (/^H[1-6]$/.test(n.tagName)) return +n.tagName[1];
      const h = n.children.length <= 3 && n.querySelector(':scope > h1,:scope > h2,:scope > h3,:scope > h4,:scope > h5,:scope > h6');
      return h && norm(n.innerText || '') === norm(h.innerText || '') ? +h.tagName[1] : 0;
    };
    for (const kw of kws) {
      const k = kw.toLowerCase();
      const cands = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6,dt,th,caption,summary,legend,label,strong,b,[class*="title" i],[class*="label" i],[class*="heading" i]')]
        .filter((el) => { const t = (el.innerText || '').trim(); return t && t.length <= 80 && t.toLowerCase().includes(k) && vis(el) && !el.closest('nav,[role=navigation]'); })
        .sort((a, b) => /^H[1-6]$/.test(b.tagName) - /^H[1-6]$/.test(a.tagName));
      // A heading's section answers it; labelled blocks are read only when no heading matched.
      let byHeading = false;
      for (const el of cands.slice(0, 12)) {
        const tag = el.tagName;
        if (/^H[1-6]$/.test(tag)) {
          const lvl = +tag[1]; let start = el;
          while (start.parentElement && level(start.parentElement) === lvl) start = start.parentElement;
          let t = ''; let n = start.nextElementSibling;
          while (n && !(level(n) && level(n) <= lvl) && t.length <= maxEach) { t += '\n' + (n.innerText || ''); n = n.nextElementSibling; }
          if (!norm(t)) {
            const p = el.parentElement?.innerText || '';
            if (norm(p).length <= norm(el.innerText).length + 8) continue;
            t = p;
          }
          add(kw, 'heading', el.innerText, t);
          byHeading = true;
        } else if (tag === 'DT') {
          let t = ''; let n = el.nextElementSibling;
          while (n && n.tagName === 'DD') { t += '\n' + n.innerText; n = n.nextElementSibling; }
          add(kw, 'dl', el.innerText, t);
        } else if (tag === 'TH') {
          const row = el.closest('tr');
          const tds = row ? [...row.querySelectorAll('td')].map((d) => d.innerText).join('\n') : '';
          if (tds.trim()) add(kw, 'table_row', el.innerText, tds);
          else {
            const idx = [...el.parentElement.children].indexOf(el); const tbl = el.closest('table');
            const col = tbl ? [...tbl.querySelectorAll('tr')].map((r) => r.children[idx]?.innerText || '').filter(Boolean).slice(1).join('\n') : '';
            add(kw, 'table_col', el.innerText, col);
          }
        } else if (!byHeading) {
          let p = el.parentElement; const own = (el.innerText || '').trim().length;
          while (p && p !== document.body && (p.innerText || '').trim().length < own + 8) p = p.parentElement;
          if (p && p !== document.body && (p.innerText || '').length < maxEach * 2) add(kw, 'block', el.innerText, p.innerText);
        }
      }
    }
    if (!out.length) {
      const body = clean(document.body.innerText);
      for (const kw of kws) {
        let i = -1, n = 0; const low = body.toLowerCase(); const k = kw.toLowerCase();
        while ((i = low.indexOf(k, i + 1)) >= 0 && n < 4) { add(kw, 'window', kw, body.slice(Math.max(0, i - 200), i + 600)); n++; i += 600; }
      }
    }
    return out;
  },

  // Headings of a long page (h1-h4 outside menus), indented by level, up to max characters.
  outline({ max }) {
    const lines = []; const seen = new Set(); let used = 0;
    for (const h of document.querySelectorAll('h1,h2,h3,h4')) {
      if (h.closest('nav,header,footer,aside,[role=navigation]')) continue;
      const cs = getComputedStyle(h);
      if (cs.display === 'none' || cs.visibility === 'hidden') continue;
      const t = (h.innerText || '').replace(/\s+/g, ' ').replace(/\s*\[(편집|edit|編集)\]\s*$/i, '').replace(/\s*[¶§#]\s*$/, '').trim();
      if (!t || t.length > 80 || seen.has(t)) continue;
      seen.add(t);
      const line = '  '.repeat(Math.max(0, +h.tagName[1] - 2)) + t;
      if (used + line.length > max) break;
      lines.push(line); used += line.length + 1;
    }
    return lines.join('\n');
  },

  // JSON-LD, microdata, meta, <time> and canonical.
  structured() {
    const ld = [];
    const flat = (x) => { if (!x) return; if (Array.isArray(x)) return x.forEach(flat); if (typeof x === 'object') { if (x['@graph']) flat(x['@graph']); if (x['@type']) ld.push(x); } };
    for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
      try { flat(JSON.parse(s.textContent.trim().replace(/^﻿/, ''))); } catch {}
    }
    const meta = {};
    for (const m of document.querySelectorAll('meta[property],meta[name],meta[itemprop]')) {
      const k = m.getAttribute('property') || m.getAttribute('name') || m.getAttribute('itemprop');
      if (/^(og:|article:|twitter:|description$|date|last-modified|author$|geo\.|place:|business:|restaurant:|DC\.|dcterms\.)/i.test(k) && m.content) meta[k] = m.content.slice(0, 500);
    }
    const times = [...document.querySelectorAll('time[datetime]')].slice(0, 5).map((t) => ({ datetime: t.getAttribute('datetime'), text: t.innerText.trim().slice(0, 60) }));
    const micro = [...document.querySelectorAll('[itemscope][itemtype]')].filter((el) => !el.parentElement?.closest('[itemscope]')).slice(0, 10).map((el) => {
      const o = { '@type': (el.getAttribute('itemtype') || '').split('/').pop() };
      for (const p of el.querySelectorAll('[itemprop]')) {
        const k = p.getAttribute('itemprop'); if (o[k]) continue;
        o[k] = (p.getAttribute('content') || p.getAttribute('datetime') || p.getAttribute('href') || p.getAttribute('src') || p.innerText || '').trim().slice(0, 300);
      }
      return o;
    });
    return { json_ld: ld, microdata: micro, meta, times, canonical: document.querySelector('link[rel=canonical]')?.href };
  },

  links({ base }) {
    const seen = new Set(); const out = [];
    for (const a of document.querySelectorAll('a[href]')) {
      let href; try { href = new URL(a.getAttribute('href'), base || location.href).href; } catch { continue; }
      if (!/^https?:/.test(href) || seen.has(href)) continue;
      seen.add(href);
      out.push({ text: (a.innerText || a.getAttribute('aria-label') || a.title || '').trim().replace(/\s+/g, ' ').slice(0, 200), href });
    }
    return out;
  },

  images({ minWidth, base }) {
    const abs = (u) => { if (!u) return null; try { return new URL(u, base || location.href).href; } catch { return null; } };
    const out = new Map();
    const put = (url, info) => {
      url = abs(url); if (!url || !/^https?:/.test(url)) return;
      const prev = out.get(url); out.set(url, { url, ...Object.fromEntries(Object.entries(info).filter(([, v]) => v)), ...(prev || {}) });
    };
    for (const k of ['og:image', 'og:image:secure_url', 'twitter:image', 'twitter:image:src']) {
      const m = document.querySelector(`meta[property="${k}"],meta[name="${k}"]`); if (m?.content) put(m.content, { source: k });
    }
    for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const walk = (x) => { if (!x || typeof x !== 'object') return; if (Array.isArray(x)) return x.forEach(walk); for (const im of [].concat(x.image || [])) put(typeof im === 'string' ? im : im.url || im.contentUrl, { source: 'json-ld', caption: im.caption || x.name }); Object.values(x).forEach((v) => typeof v === 'object' && walk(v)); };
        walk(JSON.parse(s.textContent));
      } catch {}
    }
    const best = (set) => {
      let top = null, topW = -1;
      for (const part of (set || '').split(/,\s+(?=\S)/)) {
        const [u, d] = part.trim().split(/\s+/); if (!u) continue;
        const w = d ? (d.endsWith('w') ? parseFloat(d) : parseFloat(d) * 1000) : 1;
        if (w > topW) { topW = w; top = u; }
      }
      return top ? { url: top, w: topW >= 1000 && !/w$/.test(set) ? null : topW } : null;
    };
    for (const img of document.querySelectorAll('img, picture source')) {
      const isImg = img.tagName === 'IMG';
      const lazy = img.getAttribute('data-src') || img.getAttribute('data-original') || img.getAttribute('data-lazy-src') || img.getAttribute('data-lazy');
      const set = best(img.getAttribute('srcset') || img.getAttribute('data-srcset'));
      const url = [set?.url, lazy, isImg ? img.getAttribute('src') : null].map(abs).find((u) => u && /^https?:/.test(u));
      if (!url) continue;
      const pic = isImg ? img : img.closest('picture')?.querySelector('img');
      const w = (set?.w > 1 ? set.w : 0) || pic?.naturalWidth || +pic?.getAttribute('width') || 0;
      if (minWidth && w && w < minWidth) continue;
      const fig = img.closest('figure');
      put(url, { source: set ? 'srcset' : lazy ? 'lazy' : 'img', alt: pic?.alt?.trim().slice(0, 200), caption: fig?.querySelector('figcaption')?.innerText.trim().slice(0, 200) || pic?.title, width: w || undefined, link: abs(img.closest('a')?.getAttribute('href')) });
    }
    for (const a of document.querySelectorAll('a[href]')) { const h = abs(a.getAttribute('href')); if (h && /\.(jpe?g|png|webp|gif|avif)(\?|$)/i.test(h)) put(h, { source: 'link', alt: a.innerText.trim().slice(0, 120) || a.querySelector('img')?.alt }); }
    return [...out.values()].filter((x) => !/sprite|spacer|blank\.gif|pixel|1x1|logo|icon|favicon|badge|avatar/i.test(x.url) || x.source?.includes('og'));
  },

  // Repeated items (search results, cards, rows) as a table. With sel: that element and its look-alikes
  // (same tag and classes); without: the largest group of same-looking siblings with real text.
  table({ sel, base, max }) {
    const sig = (el) => el.tagName + '.' + [...el.classList].filter((c) => !/\d{3,}|active|selected|odd|even|first|last/i.test(c)).sort().join('.');
    const txt = (el) => (el.innerText || '').trim().replace(/[ \t]+/g, ' ').replace(/\n{2,}/g, '\n');
    let items = [];
    let used = sel || null;
    if (sel) {
      const first = document.querySelector(sel);
      if (first) { const s = sig(first); items = [...document.querySelectorAll(first.tagName)].filter((e) => sig(e) === s); }
    } else {
      let best = null, bestScore = 0;
      for (const p of document.querySelectorAll('body *')) {
        if (p.children.length < 4) continue;
        const groups = {};
        for (const c of p.children) { const s = sig(c); (groups[s] ||= []).push(c); }
        for (const [s, g] of Object.entries(groups)) {
          if (g.length < 4) continue;
          const avg = g.reduce((a, e) => a + Math.min(txt(e).length, 400), 0) / g.length;
          if (avg < 20) continue;
          const score = g.length * avg;
          if (score > bestScore) { bestScore = score; best = { s, g }; }
        }
      }
      if (best) { items = best.g; used = best.s; }
    }
    const abs = (u) => { try { return new URL(u, base || location.href).href; } catch { return null; } };
    const rows = items.slice(0, max || 100).map((el) => {
      const a = el.querySelector('a[href]') || el.closest('a[href]');
      const img = el.querySelector('img');
      const t = txt(el);
      const price = t.match(/(?:JPY|¥|￥|₩|KRW|\$|€)\s?[\d,]+(?:\s?[~～-]\s?(?:JPY|¥|￥|₩)?\s?[\d,]+)?|[\d,]+\s?(?:円|원)/);
      const rating = t.match(/(?:^|\s|★|☆)([0-5][.,]\d{1,2})(?=\s|$|\/)/m);
      const title = (el.querySelector('h1,h2,h3,h4,[class*="name" i],[class*="title" i]') || a || el).innerText?.trim().split('\n')[0].slice(0, 120);
      return Object.fromEntries(Object.entries({
        title, link: a ? abs(a.getAttribute('href')) : undefined,
        image: img ? abs(['data-src', 'data-original', 'data-lazy', 'data-lazy-src', 'src'].map((k) => img.getAttribute(k)).find((v) => v && !v.startsWith('data:')) || (img.getAttribute('srcset') || '').split(/\s+/)[0] || null) : undefined,
        price: price?.[0], rating: rating ? +rating[1].replace(',', '.') : undefined,
        text: t.slice(0, 300),
      }).filter(([, v]) => v !== undefined && v !== null && v !== ''));
    });
    return { selector: used, count: items.length, rows };
  },

  // What the page shows, for the block check: title, text length and head, challenge frames.
  probe() {
    return {
      title: document.title,
      len: (document.body?.innerText || '').trim().length,
      head: (document.body?.innerText || '').slice(0, 3000),
      frame: !!document.querySelector('iframe[src*="recaptcha"],iframe[src*="hcaptcha"],iframe[src*="challenges.cloudflare.com"],#challenge-form,#cf-challenge-running'),
    };
  },

  // One step of auto_scroll: a screen down; returns the height and where the view ends.
  scrollStep() { window.scrollBy(0, Math.max(400, innerHeight * 0.9)); return { h: document.documentElement.scrollHeight, y: scrollY + innerHeight }; },
  height() { return document.documentElement.scrollHeight; },
  top() { const h = document.documentElement.scrollHeight; window.scrollTo(0, 0); return h; },

  // For browser_actions' change report: url, title, visible lines, checked/selected states,
  // open dialogs and alert/status messages.
  ui() {
    const lines = (document.body?.innerText || '').split('\n').map((s) => s.trim()).filter(Boolean).slice(0, 4000);
    const states = [...document.querySelectorAll('input[type=checkbox],input[type=radio],[aria-checked],[aria-selected],[aria-expanded],select')].slice(0, 400).map((el) => {
      const name = (el.getAttribute('aria-label') || el.labels?.[0]?.innerText || el.name || el.id || el.innerText || '').trim().slice(0, 60);
      const v = el.tagName === 'SELECT' ? el.selectedOptions?.[0]?.text : el.checked ?? el.getAttribute('aria-checked') ?? el.getAttribute('aria-selected') ?? el.getAttribute('aria-expanded');
      return `${name}=${v}`;
    });
    const dialogs = [...document.querySelectorAll('dialog[open],[role=dialog],[role=alertdialog],[aria-modal=true]')].filter((d) => d.offsetParent !== null || getComputedStyle(d).position === 'fixed').map((d) => (d.innerText || '').trim().slice(0, 200));
    const messages = [...document.querySelectorAll('[role=alert],[role=status],[aria-live=assertive],[aria-live=polite],.toast,.snackbar')].map((d) => (d.innerText || '').trim()).filter(Boolean).slice(0, 10).map((s) => s.slice(0, 200));
    return { url: location.href, title: document.title, lines, states, dialogs, messages };
  },
};
