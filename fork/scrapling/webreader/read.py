"""The one reading tool: Scrapling's engines (Chrome-TLS HTTP, stealth browser) with web-reader's
reading options. Port of web-reader 0.9.4 readSmart / readMany.

Order per URL: HTTP with the shared browser's cookies and user agent (fast, no scripts) ->
the shared stealth browser when HTTP is blocked, too thin, not HTML, or the site is remembered
as blocked for HTTP -> other language editions / alternates when blocked -> needs_human
(the tab is kept so user can pass the check in the window; nothing is solved automatically).
"""

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

from scrapling import Selector
from scrapling.core.shell import Convertor
from scrapling.fetchers import FetcherSession
from scrapling.engines.toolbelt.navigation import _is_domain_blocked
from scrapling.webreader.browser import ROOT, SHARED, TabSession
from scrapling.webreader import notify, sites, textops
from scrapling.webreader.embedded import embedded_report, embedded_state

HERE = Path(__file__).resolve().parent
_EXTRACT_JS = (HERE / "extract.js").read_text(encoding="utf-8")
_READABILITY_JS = (HERE / "vendor" / "Readability.js").read_text(encoding="utf-8")
_CALL = "(arg) => {\n" + _EXTRACT_JS + "\nreturn wr[arg.fn](arg.a || {});\n}"
_CALL_MAIN = (
    "(arg) => {\n" + _EXTRACT_JS
    + "\nif (typeof globalThis.__wrReadability !== 'function') { globalThis.__wrReadability = (function(){ var module; "
    + _READABILITY_JS + "\n; return Readability; })(); }\nreturn wr.main();\n}"
)

CHALLENGE = re.compile(r"just a moment|잠시만 기다|attention required|checking your browser|verify you are human|access denied", re.I)
CAPTCHA_TEXT = re.compile(r"unusual traffic|비정상적인 트래픽|i'm not a robot|로봇이 아닙니다|verify you are human|사람인지 확인|captcha", re.I)
CHALLENGE_HTML = re.compile(r"challenge-platform|__cf_chl|cf-turnstile|g-recaptcha|h-captcha|captcha-delivery|px-captcha|_Incapsula_Resource", re.I)
BLOCKED = re.compile(r"^(bot_check|captcha|challenge_page|http_403|http_429)")
# Site recipes (language editions, section synonyms, notes) live in sites.json via sites.py.
HTTP_BLOCKED_FILE = ROOT / "data" / "sites-http-blocked.json"
HTTP_BLOCK_DAY = 24 * 3600
# Read results are reused for a short while only (prices, hours, availability change); cache:false skips it.
CACHE_S = 600
LISTY = re.compile(r"/(rstLst|grouplst|list|lists|search|srch|tag|tags|category|categories|ranking|rank)(/|\?|$)|[?&](page|p|pg|q|query|keyword)=", re.I)
STATE_SCRIPT = re.compile(r"^\s*(\(?\s*self\.__next_f\s*=|self\.__next_f\.push|window\.__[A-Z0-9_]+__\s*=|window\[[\"']__[A-Z0-9_]+__[\"']\]\s*=|__NUXT__\s*=|window\.__NUXT__)")
_prefer_edition: Dict[str, str] = {}


def host_of(u: str) -> str:
    try:
        return (urlsplit(u).hostname or "").removeprefix("www.")
    except ValueError:
        return ""


def lang_url(u: str, lang: str) -> Optional[str]:
    """Put a language segment first in the path: /osaka/x -> /en/osaka/x, /ja/x -> /en/x."""
    try:
        x = urlsplit(u)
    except ValueError:
        return None
    segs = [s for s in x.path.split("/") if s]
    if segs and re.match(r"^[a-z]{2}(-[a-z]{2})?$", segs[0], re.I):
        segs.pop(0)
    path = "/" + "/".join([lang, *segs]) + ("/" if u.endswith("/") else "")
    return urlunsplit((x.scheme, x.netloc, path, x.query, x.fragment))


def _block_key(u: str) -> str:
    x = urlsplit(u)
    first = next((s for s in x.path.split("/") if s), "")
    return f"{host_of(u)}/{first}"


def _load_http_blocked() -> Dict[str, Any]:
    try:
        return json.loads(HTTP_BLOCKED_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _remember_http_block(key: str, reason: str) -> None:
    d = _load_http_blocked()
    d[key] = {"reason": reason, "at": int(time.time())}
    HTTP_BLOCKED_FILE.parent.mkdir(parents=True, exist_ok=True)
    HTTP_BLOCKED_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def _http_blocked(key: str) -> Optional[str]:
    v = _load_http_blocked().get(key)
    if v and time.time() - v.get("at", 0) < HTTP_BLOCK_DAY:
        return v.get("reason")
    return None


def _decode(resp: Any) -> str:
    body = resp.body or b""
    for enc in (getattr(resp, "encoding", None), "utf-8"):
        if enc:
            try:
                return body.decode(enc)
            except (LookupError, UnicodeDecodeError):
                pass
    return body.decode("utf-8", "replace")


def _document_text(body: bytes, kind: str) -> Dict[str, Any]:
    """Text of a PDF (per page) or DOCX (paragraphs and table cells). Scanned PDFs have no text layer."""
    import io

    if kind == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(body))
        pages = [(p.extract_text() or "").strip() for p in reader.pages[:300]]
        text = "\n\n".join(f"[p.{i + 1}]\n{t}" for i, t in enumerate(pages) if t)
        out: Dict[str, Any] = {"text": text, "pages": len(reader.pages)}
        if not text:
            out["note"] = "글자 층이 없는 PDF(스캔 이미지)예요. 글을 읽으려면 OCR이 필요해요."
        return out
    from docx import Document

    d = Document(io.BytesIO(body))
    parts = [p.text for p in d.paragraphs if p.text.strip()]
    for t in d.tables:
        for row in t.rows:
            parts.append(" | ".join(c.text.strip() for c in row.cells))
    return {"text": "\n".join(parts), "paragraphs": len(d.paragraphs), "tables": len(d.tables)}


def _html_problem(html: str, status: int) -> Optional[str]:
    head = html[:200000]
    title = re.search(r"<title[^>]*>([\s\S]*?)</title>", head, re.I)
    if title and CHALLENGE.search(title.group(1)):
        return "challenge_page"
    if CHALLENGE_HTML.search(head):
        return "bot_check"
    if status in (401, 403, 429, 503):
        return f"http_{status}"
    return None


def _probe_problem(p: Dict[str, Any], url: str, status: int) -> Optional[str]:
    if CHALLENGE.search(p.get("title") or ""):
        return "challenge_page"
    if re.search(r"/sorry/|captcha|js_challenge|__cf_chl|challenge-platform", url, re.I):
        return "captcha"
    if p["len"] < 1500 and (p["frame"] or CAPTCHA_TEXT.search(p["head"])):
        return "captcha"
    if status in (401, 403, 429, 503) and p["len"] < 500:
        return f"http_{status}"
    if p["len"] == 0:
        return "empty_page"
    return None


def _static_html(html: str) -> str:
    """Keep only scripts that hold data (JSON-LD, __NEXT_DATA__, state pushes); drop the rest and refreshes."""

    def keep(m: re.Match) -> str:
        attrs, code = m.group(1), m.group(2)
        if re.search(r"\btype\s*=\s*[\"']?application/(ld\+)?json", attrs, re.I) or re.search(r"\bid\s*=\s*[\"']?__(NEXT|NUXT)_DATA__", attrs, re.I):
            return m.group(0)
        return ""

    html = re.sub(r"<script\b([^>]*)>([\s\S]*?)</script\s*>", keep, html, flags=re.I)
    return re.sub(r"<meta[^>]+http-equiv\s*=\s*[\"']?refresh[^>]*>", "", html, flags=re.I)


async def js(page: Any, fn: str, arg: Optional[dict] = None) -> Any:
    if fn == "main":
        return await page.evaluate(_CALL_MAIN, {})
    return await page.evaluate(_CALL, {"fn": fn, "a": arg or {}})


def place_info(s: Dict[str, Any]) -> List[Dict[str, Any]]:
    types = re.compile(r"Restaurant|FoodEstablishment|LocalBusiness|Store|Cafe|Bar|BarOrPub|Bakery|Hotel|LodgingBusiness|TouristAttraction|Place|Museum|LandmarksOrHistoricalBuildings|PlaceOfWorship|Park|Event|Organization|Product")
    one = lambda v: v[0] if isinstance(v, list) and v else v  # noqa: E731

    def txt(v: Any) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, dict):
            return v.get("name") or v.get("@id") or json.dumps(v, ensure_ascii=False)
        return str(v)

    def num(v: Any) -> Optional[float]:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    out = []
    for x in [*s.get("json_ld", []), *s.get("microdata", [])]:
        t = x.get("@type")
        tstr = ",".join(t) if isinstance(t, list) else str(t)
        if not types.search(tstr):
            continue
        a = one(x.get("address"))
        addr = " ".join(str(a[k]) for k in ("postalCode", "addressRegion", "addressLocality", "streetAddress") if a.get(k)) if isinstance(a, dict) else txt(a)
        g = one(x.get("geo")) or {}
        r = one(x.get("aggregateRating")) or {}
        if not isinstance(g, dict):
            g = {}
        if not isinstance(r, dict):
            r = {}
        oh = None
        if x.get("openingHoursSpecification"):
            specs = x["openingHoursSpecification"] if isinstance(x["openingHoursSpecification"], list) else [x["openingHoursSpecification"]]
            oh = []
            for o in specs:
                if not isinstance(o, dict):
                    continue
                days = o.get("dayOfWeek") or []
                days = days if isinstance(days, list) else [days]
                oh.append(f"{','.join(str(d).split('/')[-1] for d in days)} {o.get('opens', '')}-{o.get('closes', '')}".strip())
        elif x.get("openingHours"):
            oh = x["openingHours"] if isinstance(x["openingHours"], list) else [x["openingHours"]]
        img = one(x.get("image"))
        item = {
            "type": tstr, "name": txt(x.get("name")), "address": addr or None,
            "lat": num(g.get("latitude")), "lon": num(g.get("longitude")),
            "opening_hours": oh, "telephone": txt(x.get("telephone")), "price_range": txt(x.get("priceRange")),
            "cuisine": ", ".join(x["servesCuisine"]) if isinstance(x.get("servesCuisine"), list) else x.get("servesCuisine"),
            "rating": num(r.get("ratingValue")), "rating_count": r.get("ratingCount") or r.get("reviewCount"),
            "url": txt(x.get("url")), "image": txt(img.get("url") if isinstance(img, dict) else img),
            "date_published": x.get("datePublished"), "date_modified": x.get("dateModified"),
        }
        out.append({k: v for k, v in item.items() if v not in (None, "", [])})
        if len(out) >= 5:
            break
    return out


def dates_from(s: Dict[str, Any], last_modified: Optional[str]) -> Optional[Dict[str, str]]:
    d: Dict[str, Any] = {}
    for x in s.get("json_ld", []):
        d.setdefault("published", x.get("datePublished"))
        d.setdefault("modified", x.get("dateModified"))
    m = s.get("meta", {})
    times = s.get("times") or [{}]
    d["published"] = d.get("published") or m.get("article:published_time") or m.get("og:published_time") or m.get("date") or m.get("DC.date") or m.get("dcterms.created") or times[0].get("datetime")
    d["modified"] = d.get("modified") or m.get("article:modified_time") or m.get("og:updated_time") or m.get("dcterms.modified") or m.get("last-modified")
    if last_modified:
        d["http_last_modified"] = last_modified
    d = {k: v for k, v in d.items() if v}
    return d or None


async def auto_scroll(page: Any, max_steps: int = 25, max_ms: int = 15000, pause_ms: int = 500) -> Dict[str, Any]:
    t0 = time.monotonic()
    steps = still = 0
    last = -1
    while steps < max_steps and (time.monotonic() - t0) * 1000 < max_ms:
        try:
            st = await js(page, "scrollStep")
        except Exception:
            break
        steps += 1
        await page.wait_for_timeout(pause_ms)
        h = await js(page, "height")
        if st["y"] >= h - 5 and h == last:
            still += 1
            if still >= 2:
                break
        else:
            still = 0
        last = h
    try:
        await page.wait_for_load_state("networkidle", timeout=3000)
    except Exception:
        pass
    height = await js(page, "top")
    out: Dict[str, Any] = {"steps": steps, "ms": int((time.monotonic() - t0) * 1000), "page_height": height}
    if still < 2 and (steps >= max_steps or (time.monotonic() - t0) * 1000 >= max_ms):
        out.update(reached_limit=True, note="한도까지 내렸지만 더 불러올 내용이 남았을 수 있어요(무한 스크롤). 더 필요하면 browser_open 후 browser_actions(wheel)·browser_extract.")
    return out


async def _settle(page: Any, max_ms: int = 10000) -> None:
    """After load: return once the network is quiet or the text stops changing for ~1 s (max 10 s)."""
    t0 = time.monotonic()
    idle = asyncio.ensure_future(page.wait_for_load_state("networkidle", timeout=max_ms))
    last, same = -1, 0
    try:
        while (time.monotonic() - t0) * 1000 < max_ms and not idle.done():
            await asyncio.sleep(0.25)
            try:
                n = await page.evaluate("() => (document.body && document.body.innerText || '').length")
            except Exception:
                break
            same = same + 1 if n == last else 0
            last = n
            if same >= 4 and n > 0:
                break
    finally:
        if not idle.done():
            idle.cancel()
        try:
            await idle
        except BaseException:
            pass


LEAN_TYPES = frozenset({"image", "font", "media"})


def _route_handler(ad_domains: frozenset, lean: bool) -> Any:
    """One route for both: Scrapling's ad/tracker domains, and (lean) pictures, fonts and video,
    which reading does not need. Stylesheets stay: hidden-text detection and line breaks use them."""

    async def handle(route: Any) -> None:
        req = route.request
        if (lean and req.resource_type in LEAN_TYPES) or (ad_domains and _is_domain_blocked(urlsplit(req.url).hostname or "", ad_domains)):
            await route.abort()
        else:
            await route.continue_()

    return handle


def next_steps(r: Dict[str, Any], url: str) -> List[str]:
    """One-line hints for what to call next, from what the result shows."""
    out: List[str] = []
    if r.get("needs_human"):
        out.append("사용자에게 사이트를 알리고 wait_for_human(session_id)")
    elif r.get("problem"):
        out.append("막혔으면 engine=browser, 그래도 막히면 login(url) 또는 사용자 손")
    if r.get("next_offset"):
        out.append(f"나머지 글: offset={r['next_offset']}")
    if LISTY.search(r.get("url") or url) and not r.get("table"):
        out.append("목록 페이지면 table=true로 항목을 표로")
    if (r.get("total_chars") or 0) > 15000 and not r.get("query_filter") and not r.get("sections"):
        out.append("긴 페이지: section(칸 이름) 또는 urls와 query로 필요한 문단만")
    return out[:3]


class _StaticTab:
    """Session-like holder for a pooled offline tab: close() returns it to the pool (up to 10)."""

    def __init__(self, page: Any, pool: List[Any]) -> None:
        self.page, self._pool = page, pool

    async def close(self) -> None:
        if self.page.is_closed():
            return
        if len(self._pool) < 10:
            self._pool.append(self.page)
        else:
            await self.page.close()


class Opened:
    """A page ready to read: a live browser tab, or a static tab holding HTTP-fetched HTML."""

    def __init__(self, session: TabSession, page: Any, status: int, url: str, html: str, via: str, last_modified: Optional[str] = None):
        self.session, self.page, self.status, self.url, self.html, self.via, self.last_modified = session, page, status, url, html, via, last_modified


class Reader:
    def __init__(self) -> None:
        self._client: Any = None
        self._client_cm: Any = None
        self._client_lock = asyncio.Lock()
        self._tab_gates: Dict[str, asyncio.Semaphore] = {}
        self._cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._static_pool: List[Any] = []

    async def _http_client(self) -> Any:
        """One HTTP session kept for the life of the server: connections (TLS, HTTP/2) are reused,
        which saves the handshakes that took most of each read (0.8-1.1 s per URL, measured)."""
        async with self._client_lock:
            if self._client is None:
                self._client_cm = FetcherSession(impersonate="chrome", stealthy_headers=True, follow_redirects="safe", retries=1, timeout=20)
                self._client = await self._client_cm.__aenter__()
            return self._client

    async def _drop_client(self) -> None:
        async with self._client_lock:
            cm, self._client, self._client_cm = self._client_cm, None, None
        if cm:
            try:
                await cm.__aexit__(None, None, None)
            except Exception:
                pass

    async def _cookie_header_parts(self, url: str) -> Tuple[Dict[str, str], str]:
        owner = await SHARED.get()
        cookies = await owner.context.cookies([url])
        ua = await SHARED.user_agent()
        return {c["name"]: c["value"] for c in cookies}, ua

    async def http_open(self, url: str, headers: Optional[Dict[str, str]]) -> Dict[str, Any]:
        """One HTTP request with Chrome's TLS, the browser's cookies and user agent. Returns
        {opened} or {skipped: reason}."""
        key = _block_key(url)
        remembered = _http_blocked(key)
        if remembered:
            return {"skipped": remembered, "remembered": True, "block_key": key}
        cookies, ua = await self._cookie_header_parts(url)
        t0 = time.monotonic()
        try:
            client = await self._http_client()
            resp = await client.get(url, cookies=cookies or None, headers={"User-Agent": ua, "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7", **(headers or {})})
        except Exception as e:
            await self._drop_client()
            return {"skipped": "http_error", "error": str(e).splitlines()[0][:200] if str(e) else type(e).__name__}
        http_ms = int((time.monotonic() - t0) * 1000)
        ctype = str((resp.headers or {}).get("content-type", ""))
        kind = "pdf" if ("pdf" in ctype.lower() or (resp.body or b"")[:5] == b"%PDF-") else "docx" if "wordprocessingml" in ctype.lower() else None
        if kind and resp.status < 400:
            try:
                return {"document": _document_text(resp.body, kind) | {"kind": kind, "status": resp.status, "url": resp.url, "http_ms": http_ms}}
            except Exception as e:
                return {"skipped": "document_unreadable", "error": str(e).splitlines()[0][:200] if str(e) else type(e).__name__}
        if ctype and "html" not in ctype.lower():
            return {"skipped": "not_html", "content_type": ctype[:80], "http_ms": http_ms}
        html = _decode(resp)
        problem = _html_problem(html, resp.status)
        if problem:
            if BLOCKED.match(problem):
                _remember_http_block(key, problem)
            return {"skipped": problem, "status": resp.status, "block_key": key, "http_ms": http_ms}
        if resp.status >= 400:
            return {"skipped": f"http_{resp.status}", "status": resp.status, "http_ms": http_ms}
        session = await self._static_tab()
        page = session.page
        await page.set_content(_static_html(html), wait_until="domcontentloaded")
        n = await page.evaluate("() => (document.body && document.body.innerText || '').trim().length")
        # Short and built by scripts: render it. Short without scripts (example.com) is simply short:
        # rendering it again cost ~0.8 s for nothing (measured).
        if n < 300 and re.search(r"<script\b[^>]*\bsrc=|<script\b[^>]*>\s*\S{200}", html, re.I):
            await session.close()
            return {"skipped": "thin_page_needs_rendering", "chars": n, "http_ms": http_ms}
        return {"opened": Opened(session, page, resp.status, resp.url, html, "http", (resp.headers or {}).get("last-modified")), "http_ms": http_ms}

    async def _static_tab(self) -> "_StaticTab":
        """A tab for HTTP-fetched HTML, offline (every request aborted). Reused after each read instead of
        opening a new window each time."""
        while self._static_pool:
            page = self._static_pool.pop()
            if not page.is_closed():
                return _StaticTab(page, self._static_pool)
        page = await SHARED.new_page()

        async def cut(route: Any) -> None:
            await route.abort()

        await page.route("**/*", cut)
        return _StaticTab(page, self._static_pool)

    async def browser_open(self, url: str, headers: Optional[Dict[str, str]], wait_ms: int, lean: bool = True) -> Opened:
        session = TabSession()
        await session.start()
        # Browser tabs per site stay at web-reader's 5 (decided 2026-10-22); HTTP reads go wider.
        gate = self._tab_gates.setdefault(host_of(url), asyncio.Semaphore(5))
        try:
            page = await session.context.new_page()
            session.page_pool.add_page(page).mark_ready()
            page.set_default_timeout(30000)
            if headers:
                await page.set_extra_http_headers(headers)
            await page.route("**/*", _route_handler(frozenset(session._config.blocked_domains or ()), lean))
            async with gate:
                # Read from DOMContentLoaded + settle instead of waiting for "load" (every script,
                # stylesheet and ad): 1.9-3.0 s of 3.0-3.6 s per tabelog page went to that wait.
                resp = await page.goto(url, wait_until="domcontentloaded")
                await _settle(page)
                if wait_ms:
                    await page.wait_for_timeout(wait_ms)
        except Exception:
            await session.close()
            raise
        status = resp.status if resp else 0
        return Opened(session, page, status, page.url, "", "browser", (resp.headers.get("last-modified") if resp else None))

    async def open(self, url: str, opts: Dict[str, Any]) -> Dict[str, Any]:
        """HTTP first (unless mode browser/auto_scroll), then the browser; on a block, other
        editions and alternates. Returns {opened, problem, tried, opened_instead, http}."""
        site = host_of(url)
        pref = _prefer_edition.get(site)
        tries = [u for u in dict.fromkeys([*([lang_url(url, pref)] if pref else []), url, *opts.get("alternates", []), *(lang_url(url, l) for l in opts.get("lang_fallback", []))]) if u]
        http_info: Optional[Dict[str, Any]] = None
        failures: List[Dict[str, Any]] = []
        blocked_seen = False
        use_http = opts.get("engine", "auto") in ("auto", "http") and not opts.get("auto_scroll")
        k = 0
        last: Optional[Tuple[Opened, Optional[str]]] = None
        while k < len(tries):
            u = tries[k]
            k += 1
            if use_http:
                h = await self.http_open(u, opts.get("headers"))
                if u == url:
                    http_info = {kk: v for kk, v in h.items() if kk != "opened"}
                if "document" in h:
                    return {"document": h["document"]}
                if "opened" in h:
                    o = h["opened"]
                    if blocked_seen and u != url:
                        ed = next((l for l in sites.editions(site) if lang_url(url, l) == u), None)
                        if ed:
                            _prefer_edition[site] = ed
                    return {"opened": o, "problem": None, "tried": failures or None, "opened_instead": u if u != url else None, "blocked_original": blocked_seen, "http": http_info}
                if BLOCKED.match(h.get("skipped", "")):
                    blocked_seen = True
                    for n, ed in enumerate(e for e in (lang_url(u, x) for x in sites.editions(u)) if e and e not in tries):
                        tries.insert(k + n, ed)
                if opts.get("engine") == "http":
                    failures.append({"url": u, "via": "http", "problem": h.get("skipped"), **({"error": h["error"]} if h.get("error") else {})})
                    continue
            try:
                o = await self.browser_open(u, opts.get("headers"), opts.get("wait_ms", 0), lean=not opts.get("images"))
            except Exception as e:
                failures.append({"url": u, "via": "browser", "error": str(e).splitlines()[0][:200] if str(e) else type(e).__name__})
                continue
            p = await js(o.page, "probe")
            problem = _probe_problem(p, o.url, o.status)
            if problem and BLOCKED.match(problem):
                blocked_seen = True
                failures.append({"url": u, "via": "browser", "status": o.status, "problem": problem})
                for n, ed in enumerate(e for e in (lang_url(u, x) for x in sites.editions(u)) if e and e not in tries):
                    tries.insert(k + n, ed)
                if last:
                    await last[0].session.close()
                last = (o, problem)
                continue
            if last:
                await last[0].session.close()
            if not problem and blocked_seen and u != url:
                ed = next((l for l in sites.editions(site) if lang_url(url, l) == u), None)
                if ed:
                    _prefer_edition[site] = ed
            return {"opened": o, "problem": problem, "tried": failures or None, "opened_instead": u if u != url and not problem else None, "blocked_original": blocked_seen, "http": http_info}
        if last:  # every try blocked: keep the last tab for user
            return {"opened": last[0], "problem": last[1], "tried": failures, "needs_human": True, "http": http_info}
        raise RuntimeError("모든 주소 실패: " + " | ".join(f"{f['url']} → {f.get('problem') or f.get('error')}" for f in failures))

    async def _read(self, url: str, opts: Dict[str, Any], keep_blocked: Any = None) -> Dict[str, Any]:
        """keep_blocked(session, url) registers a blocked tab for wait_for_human and returns its id."""
        t0 = time.monotonic()
        o = await self.open(url, opts)
        if o.get("document"):
            d = o["document"]
            text = d.pop("text")
            offset, max_chars = opts.get("offset", 0), opts.get("max_chars", 20000)
            out = {"via": "http", **d}
            if opts.get("query"):
                text, out["query_filter"] = textops.relevant(text, opts["query"], max_chars)
            out.update(total_chars=len(text), offset=offset, text=text[offset : offset + max_chars], ms=int((time.monotonic() - t0) * 1000))
            if offset + max_chars < len(text):
                out["next_offset"] = offset + max_chars
            return out
        op: Opened = o["opened"]
        if o.get("needs_human"):
            sid = keep_blocked(op.session, op.url) if keep_blocked else None
            if not sid:
                await op.session.close()
            return {
                "url": url, "status": op.status, "problem": o["problem"], "failed_before": o.get("tried"),
                "needs_human": {"session_id": sid, "site": host_of(op.url), "next": "사용자에게 어느 사이트인지 알리고 wait_for_human(session_id)를 부르세요. 창이 사용자 화면에 떠요."},
                "ms": int((time.monotonic() - t0) * 1000),
            }
        page = op.page
        try:
            scrolled = await auto_scroll(page) if opts.get("auto_scroll") and op.via == "browser" else None
            out: Dict[str, Any] = {"url": page.url if op.via == "browser" else op.url, "status": op.status, "via": op.via}
            try:
                out["title"] = await page.title()
            except Exception:
                pass
            if o.get("problem"):
                out["problem"] = o["problem"]
            if o.get("opened_instead") and o.get("blocked_original"):
                out["opened_instead"] = o["opened_instead"]
                out["note"] = "원래 주소가 403/확인 화면으로 막혀 같은 페이지의 다른 언어판을 읽었어요."
            if o.get("tried"):
                out["failed_before"] = o["tried"]
            if o.get("http") and op.via == "browser":
                out["http"] = o["http"]
            section = opts.get("section")
            if section:
                asked = section if isinstance(section, list) else [section]
                kws = list(dict.fromkeys(w for k in asked if k for w in next((g for g in sites.synonyms() if any(x.lower() == str(k).lower() for x in g)), [str(k)])))
                out["sections"] = await js(page, "sections", {"kws": kws, "maxEach": 2500})
            else:
                text, mres = "", None
                fmt = opts.get("format", "text")
                if opts.get("mode", "main") == "main" and not opts.get("selector") and not opts.get("include_hidden"):
                    mres = await js(page, "main")
                if mres and len(mres.get("text") or "") > 200:
                    text = Convertor._convert_to_markdown(mres["html"]) if fmt == "markdown" else mres["text"]
                    out["extracted"] = "main"
                    for k in ("byline", "published"):
                        if mres.get(k):
                            out[k] = mres[k]
                else:
                    t = await js(page, "text", {"sel": opts.get("selector"), "includeHidden": bool(opts.get("include_hidden"))})
                    text = t["text"]
                    if fmt == "markdown":
                        html = op.html or await page.content()
                        sel = Selector(content=html, url=out["url"])
                        text = "".join(Convertor._extract_content(sel, extraction_type="markdown", css_selector=opts.get("selector"), main_content_only=True)).strip() or text
                    out["extracted"] = "selector" if opts.get("selector") else "all_visible"
                    out["hidden_chars_removed"] = t["hidden_chars"]
                    if opts.get("include_hidden"):
                        out["hidden_text"] = t.get("hidden_text", "")
                if len(text) < 300 and op.via == "browser":
                    frames = []
                    for f in page.frames[1:8]:
                        try:
                            ft = (await f.evaluate("() => document.body ? document.body.innerText : ''")).strip()
                        except Exception:
                            continue
                        if len(ft) > 50:
                            frames.append(ft)
                    if frames:
                        text += "\n\n[iframe]\n" + "\n\n[iframe]\n".join(frames)
                        out["included_iframes"] = len(frames)
                offset, max_chars = opts.get("offset", 0), opts.get("max_chars", 20000)
                if fmt == "markdown" and opts.get("links") == "footnotes":
                    text = textops.footnotes(text)
                if opts.get("query"):
                    text, out["query_filter"] = textops.relevant(text, opts["query"], max_chars)
                out.update(total_chars=len(text), offset=offset, text=text[offset : offset + max_chars])
                if offset + max_chars < len(text):
                    out["next_offset"] = offset + max_chars
            if opts.get("structured_data"):
                s = await js(page, "structured")
                place = place_info(s)
                if place:
                    out["place"] = place
                d = dates_from(s, op.last_modified)
                if d:
                    out["dates"] = d
                if opts.get("structured_data") == "full" or not place:
                    out["structured"] = {"json_ld": s["json_ld"][:8], "microdata": s["microdata"] or None, "meta": s["meta"], "canonical": s.get("canonical")}
            if opts.get("table"):
                out["table"] = await js(page, "table", {"sel": opts.get("table_selector"), "base": out["url"], "max": 100})
            if opts.get("images"):
                out["images"] = (await js(page, "images", {"minWidth": opts.get("min_image_width", 200), "base": out["url"]}))[:80]
            if opts.get("include_links"):
                links = await js(page, "links", {"base": out["url"]})
                f = (opts.get("link_filter") or "").lower()
                if f:
                    links = [x for x in links if f in x["href"].lower() or f in x["text"].lower()]
                out["links"] = links[:300]
            if opts.get("embedded_json"):
                html = op.html or await page.content()
                out["embedded"] = embedded_report(embedded_state(html), opts.get("json_paths"), opts.get("json_find"), opts.get("json_max_chars", 20000))
                if op.via == "http":
                    out["embedded"]["http_note"] = "HTTP로 받은 HTML에서 읽었어요. 스크립트가 나중에 만드는 상태가 필요하면 engine=browser로."
            if scrolled:
                out["auto_scroll"] = scrolled
            out["ms"] = int((time.monotonic() - t0) * 1000)
            return out
        finally:
            await op.session.close()

    async def read(self, url: str, opts: Dict[str, Any], keep_blocked: Any = None, annotate: bool = True) -> Dict[str, Any]:
        """_read plus what is known around it: a short-lived cache, the site status board, a Telegram
        note when a person is needed, the site's note, a login-expiry warning and next steps."""
        key = None
        if opts.get("cache", True):
            key = json.dumps([url, {k: v for k, v in opts.items() if k not in ("cache", "cache_s")}], sort_keys=True, default=str)
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < opts.get("cache_s", CACHE_S):
                return {**hit[1], "cached_s": int(time.monotonic() - hit[0]), "ms": 0}
        r = await self._read(url, opts, keep_blocked)
        problem = r.get("problem") if r.get("problem") and BLOCKED.match(r["problem"]) else None
        sites.record(url, ok=not (problem or r.get("needs_human")), via=r.get("via"), problem=problem)
        if r.get("needs_human"):
            r["needs_human"]["telegram"] = await notify.needs_human(sites.site_of(url), r.get("problem") or "check", url)
        if annotate:
            n = sites.note(url)
            if n:
                r["site_note"] = n
            try:
                warn = sites.login_warning(await (await SHARED.get()).context.cookies([url]), url)
            except Exception:
                warn = None
            if warn:
                r["login"] = warn
        nxt = next_steps(r, url)
        if nxt:
            r["next"] = nxt
        if key and r.get("status", 0) < 400 and not (problem or r.get("needs_human")):
            self._cache[key] = (time.monotonic(), r)
            if len(self._cache) > 500:
                self._cache.pop(next(iter(self._cache)))
        return r

    async def read_many(self, urls: List[str], opts: Dict[str, Any], concurrency: int = 10, keep_blocked: Any = None) -> Dict[str, Any]:
        """Tabs per site adapt as in web-reader: start at 5, +1 after each clean round up to
        concurrency, back to 2 on a block; blocked pages are read once more at the end."""
        opts.setdefault("max_chars", 3000)
        t0 = time.monotonic()
        results: List[Any] = [None] * len(urls)
        site_tabs: Dict[str, Dict[str, int]] = {}
        steps: List[Dict[str, Any]] = []
        queue = [{"u": u, "i": i, "retry": False} for i, u in enumerate(urls)]
        busy: Dict[str, int] = {}
        running: List[asyncio.Task] = []
        done = asyncio.Event()

        def state(h: str) -> Dict[str, int]:
            # HTTP reads start at full width; browser tabs are held to 5 per site in browser_open.
            st = site_tabs.setdefault(h, {"limit": min(5, concurrency) if opts.get("engine") == "browser" else concurrency, "streak": 0})
            st["limit"] = min(st["limit"], concurrency)
            return st

        def is_block(r: Dict[str, Any]) -> bool:
            return bool(r.get("needs_human") or BLOCKED.match(r.get("problem") or "") or BLOCKED.match(r.get("error_kind") or "")
                        or any(BLOCKED.match(f.get("problem") or "") for f in r.get("failed_before") or []))

        async def run(job: Dict[str, Any], h: str, st: Dict[str, int]) -> None:
            t1 = time.monotonic()
            try:
                x = await self.read(job["u"], opts, keep_blocked, annotate=False)
                r = {"url": job["u"], "ok": False, "error_kind": "needs_human", **x} if x.get("needs_human") else {"ok": not x.get("problem"), **x}
            except Exception as e:
                msg = str(e).splitlines()[0][:300] if str(e) else type(e).__name__
                kind = next((m.group(0) for m in [BLOCKED.search(msg)] if m), None) if "→" in msg else None
                r = {"url": job["u"], "ok": False, "error": msg, **({"error_kind": kind} if kind else {}), "ms": int((time.monotonic() - t1) * 1000)}
            busy[h] -= 1
            if is_block(r):
                if st["limit"] != 2:
                    steps.append({"site": h, "tabs": 2, "why": r.get("problem") or r.get("error_kind")})
                st["limit"], st["streak"] = 2, 0
                if not r["ok"] and not job["retry"] and not r.get("needs_human"):
                    queue.append({**job, "retry": True})
                    r = None
            elif r["ok"]:
                st["streak"] += 1
                if st["streak"] >= st["limit"] and st["limit"] < concurrency:
                    st["limit"] += 1
                    st["streak"] = 0
                    steps.append({"site": h, "tabs": st["limit"]})
            if r is not None:
                results[job["i"]] = {**r, "read_again": True} if job["retry"] else r
            pump()

        def pump() -> None:
            k = 0
            while k < len(queue) and sum(busy.values()) < max(20, concurrency):
                job = queue[k]
                h = host_of(job["u"])
                st = state(h)
                if busy.get(h, 0) >= st["limit"]:
                    k += 1
                    continue
                queue.pop(k)
                busy[h] = busy.get(h, 0) + 1
                running.append(asyncio.ensure_future(run(job, h, st)))
            if not queue and sum(busy.values()) == 0:
                done.set()

        pump()
        await done.wait()
        failed = [x["url"] for x in results if not x.get("ok")]
        out: Dict[str, Any] = {"count": len(urls), "ok_count": len(urls) - len(failed)}
        if failed:
            out["failed"] = failed
        out.update(total_ms=int((time.monotonic() - t0) * 1000), tabs={h: site_tabs.get(h, {}).get("limit") for h in dict.fromkeys(map(host_of, urls))})
        if steps:
            out["tab_changes"] = steps
        notes = {s: n for s in dict.fromkeys(sites.site_of(u) for u in urls) if (n := sites.note(s))}
        if notes:
            out["site_notes"] = notes
        try:
            cookies = await (await SHARED.get()).context.cookies(list(dict.fromkeys(urls)))
            warns = {s: w for s in dict.fromkeys(sites.site_of(u) for u in urls)
                     if (w := sites.login_warning([c for c in cookies if sites.site_of(c.get("domain", "")) == s], s))}
            if warns:
                out["login"] = warns
        except Exception:
            pass
        out["results"] = results
        return out


READER = Reader()
