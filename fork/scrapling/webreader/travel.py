"""web-reader 0.9.4's search, place, travel, image and post tools, moved into Scrapling so web-reader
is no longer needed. Same inputs, outputs and page-side scripts as search.mjs / server.mjs; they now
run on the one shared browser (user's profile and logins) and Scrapling's HTTP client.
"""

import asyncio
import base64
import hashlib
import json
import math
import re
import time
import unicodedata
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit

from scrapling.webreader.browser import HEADLESS, ROOT, SHARED
from scrapling.webreader.read import READER, _decode, js

HERE = Path(__file__).resolve().parent
DATA = ROOT / "data"
_VENDOR = HERE / "vendor"


def classify_error(e: Any) -> Dict[str, str]:
    m = str(e)
    kinds = [
        (r"ERR_NAME_NOT_RESOLVED|ENOTFOUND|EAI_AGAIN|Could not resolve host", "dns"),
        (r"ERR_CONNECTION_REFUSED|ECONNREFUSED", "connection_refused"),
        (r"ERR_CONNECTION_(RESET|CLOSED)|ECONNRESET|socket hang up", "connection_reset"),
        (r"(?i)ERR_CERT|SSL|certificate", "ssl"),
        (r"(?i)ERR_TOO_MANY_REDIRECTS|maximum redirects", "redirect_loop"),
        (r"(?i)Timeout|timed out|ERR_TIMED_OUT", "timeout"),
        (r"ERR_INTERNET_DISCONNECTED|ERR_PROXY|ERR_TUNNEL", "network_blocked"),
        (r"ERR_BLOCKED_BY_CLIENT|ERR_BLOCKED_BY_RESPONSE", "blocked_by_browser"),
        (r"ERR_ABORTED", "aborted_or_download"),
    ]
    first = m.splitlines()[0][:300] if m else type(e).__name__
    for rx, k in kinds:
        if re.search(rx, m):
            return {"error_kind": k, "error": first}
    return {"error_kind": "other", "error": first}


async def page_problem(page: Any, status: int) -> Optional[str]:
    """Why a loaded page is not the content (status, bot check, captcha, empty)."""
    try:
        title = await page.title()
    except Exception:
        title = ""
    url = page.url
    try:
        n = await page.evaluate("() => (document.body && document.body.innerText || '').trim().length")
    except Exception:
        n = 0
    if re.search(r"(?i)/sorry/|captcha|recaptcha|hcaptcha", url) or re.search(r"(?i)captcha|로봇이 아닙니다|unusual traffic", title):
        return "captcha"
    if re.search(r"(?i)__cf_chl|challenge-platform|js_challenge", url) or re.search(r"(?i)just a moment|잠시만 기다|attention required|checking your browser|verify you are human", title):
        return "bot_check"
    if status == 429:
        return "http_429_rate_limited"
    if status == 403:
        return "http_403_forbidden"
    if status == 406:
        return "http_406_not_acceptable"
    if status in (404, 410):
        return f"http_{status}_not_found"
    if status >= 500:
        return f"http_{status}_server_error"
    if n == 0:
        return "empty_page"
    return None


@asynccontextmanager
async def tab(url: Optional[str] = None, wait_ms: int = 0) -> AsyncIterator[Any]:
    """A tab of the shared browser (its own off-screen window), closed afterwards. page._status holds
    the navigation status."""
    page = await SHARED.new_page()
    try:
        page.set_default_timeout(30000)
        page._status = 0  # type: ignore[attr-defined]
        if url:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page._status = resp.status if resp else 0  # type: ignore[attr-defined]
            try:
                await page.wait_for_load_state("load", timeout=10000)
            except Exception:
                pass
            if wait_ms:
                await page.wait_for_timeout(wait_ms)
        yield page
    finally:
        try:
            await page.close()
        except Exception:
            pass


def _safe_name(s: str) -> str:
    return re.sub(r"[^\w.\-가-힣ぁ-んァ-ン一-龥]+", "_", s)[:80]


# ---------- web_search ----------
LISTY = re.compile(r"/(rstLst|grouplst|dtlrvwlst|dtlphotolst|dtlmenu|rvwlst|list|lists|search|srch|tag|tags|category|categories|area|areas|ranking|rank|map|maps|keyword|top|index)(/|\?|$)|[?&](page|p|pg|sk|sw|q|query|keyword)=|/s/|/[A-Z]\d{4}/?$", re.I)


def detail_score(u: str) -> int:
    try:
        x = urlsplit(u)
    except ValueError:
        return 0
    segs = [g for i, g in enumerate(s for s in x.path.split("/") if s) if i or not re.match(r"^[a-z]{2}([-_][a-z]{2,4})?$", g, re.I)]
    s = min(len(segs), 5)
    if LISTY.search(x.path + ("?" + x.query if x.query else "")):
        s -= 4
    if re.search(r"\d{4,}/?$", x.path) or re.search(r"(?i)/(shop|store|restaurant|rst|place|spot|detail|hotel|item|article|post|view|p)/", x.path):
        s += 2
    if not segs:
        s -= 3
    return s


def _market(q: str) -> Dict[str, str]:
    if re.search(r"[぀-ヿ]", q):
        return {"lang": "ja", "mkt": "ja-JP", "cc": "JP"}
    if re.search(r"[가-힯]", q):
        return {"lang": "ko", "mkt": "ko-KR", "cc": "KR"}
    if re.search(r"[一-鿿]", q):
        return {"lang": "ja", "mkt": "ja-JP", "cc": "JP"}
    return {"lang": "en", "mkt": "en-US", "cc": "US"}


def _engine_url(eng: str, q: str, n: int) -> str:
    m = _market(q)
    if eng == "google":
        return f"https://www.google.com/search?q={quote(q)}&num={min(n + 5, 30)}&hl={m['lang']}"
    if eng == "bing":
        return f"https://www.bing.com/search?q={quote(q)}&count={min(n + 5, 30)}&setlang={m['lang']}&cc={m['cc']}&mkt={m['mkt']}"
    return f"https://html.duckduckgo.com/html/?q={quote(q)}"


PARSE_RESULTS = r"""(engine) => {
  const T = (el) => (el?.innerText || '').replace(/\s+/g, ' ').trim();
  const res = [];
  if (engine === 'google') {
    let links = [...document.querySelectorAll('#search a, #rso a')].filter((a) => a.querySelector('h3') && a.href.startsWith('http'));
    if (!links.length) links = [...document.querySelectorAll('a')].filter((a) => a.querySelector('h3') && a.href.startsWith('http'));
    for (const a of links) {
      const h3 = a.querySelector('h3');
      const box = a.closest('[data-hveid][data-ved], div.g, div.MjjYud') || a.parentElement;
      const sn = box?.querySelector('[data-sncf], .VwiC3b, [style*="line-clamp"]');
      let snippet = T(sn);
      if (!snippet) snippet = T(box).replace(T(h3), '').slice(0, 400);
      const cite = T(box?.querySelector('cite'));
      res.push({ title: T(h3), url: a.href, snippet, ...(cite ? { display_url: cite } : {}) });
    }
  } else if (engine === 'bing') {
    for (const li of document.querySelectorAll('#b_results > li.b_algo')) {
      const a = li.querySelector('h2 a'); if (!a) continue;
      res.push({ title: T(a), url: a.href, snippet: T(li.querySelector('.b_caption p, .b_lineclamp2, .b_lineclamp3, .b_lineclamp4, .b_algoSlug, .b_snippet')) || T(li).slice(0, 400) });
    }
  } else {
    for (const r of document.querySelectorAll('.result, .web-result')) {
      const a = r.querySelector('a.result__a'); if (!a) continue;
      res.push({ title: T(a), url: a.href, snippet: T(r.querySelector('.result__snippet')) });
    }
  }
  return res;
}"""


def _relevance(query: str, results: List[Dict[str, Any]]) -> float:
    words = [w for w in re.sub(r'-?site:\S+|\bOR\b|[()"]', " ", query).lower().split() if w]
    terms: List[str] = []
    for w in words:
        if re.search(r"[぀-ヿ一-鿿가-힯]", w) and len(w) > 2:
            terms += [w[i : i + 2] for i in range(len(w) - 1)]
        else:
            terms.append(w)
    if not terms or not results:
        return 1.0
    hit = [x for x in results if any(t in f"{x['title']} {x['snippet']} {x['url']}".lower() for t in terms)]
    return len(hit) / len(results)


def unwrap_url(u: str) -> str:
    try:
        x = urlsplit(u)
        q = parse_qs(x.query)
        if (x.hostname or "").endswith("bing.com") and x.path.startswith("/ck/"):
            v = (q.get("u") or [""])[0]
            if v.startswith("a1"):
                b = v[2:].replace("-", "+").replace("_", "/")
                return base64.b64decode(b + "=" * (-len(b) % 4)).decode("utf-8", "replace")
        if (x.hostname or "").endswith("duckduckgo.com") and q.get("uddg"):
            return q["uddg"][0]
        if re.search(r"google\.[a-z.]+$", x.hostname or "") and x.path == "/url":
            return (q.get("q") or q.get("url") or [u])[0]
    except Exception:
        pass
    return u


async def _resolve_google(results: List[Dict[str, Any]]) -> None:
    client = await READER._http_client()
    for x in results:
        try:
            u = urlsplit(x["url"])
        except ValueError:
            continue
        if not re.search(r"(^|\.)google\.[a-z.]+$", u.hostname or "") or u.path not in ("/goto", "/url"):
            continue
        q = parse_qs(u.query)
        v = (q.get("q") or q.get("url") or [""])[0]
        if v.startswith("http"):
            x["url"] = v
            continue
        try:
            r = await client.get(x["url"], follow_redirects=False, timeout=10)
            loc = (r.headers or {}).get("location")
            if loc and "google." not in (urlsplit(urljoin(x["url"], loc)).hostname or ""):
                x["url"] = urljoin(x["url"], loc)
            else:
                m = next((v for v in re.findall(r"https?://[^\"'\s<>]+", _decode(r)) if "google." not in v), None)
                if m:
                    x["url"] = m.replace("&amp;", "&")
        except Exception:
            pass
        await asyncio.sleep(0.15)


def _tidy(x: Dict[str, Any]) -> Dict[str, Any]:
    """A result without what repeats elsewhere: the snippet's "Read more" and a display_url that only
    restates the URL (kept when it carries something else, like likes and age)."""
    x = {**x, "snippet": re.sub(r"\s*(Read more|더보기|もっと見る)\s*$", "", x.get("snippet") or "")}
    d = x.get("display_url")
    if d and (re.match(r"https?://", d) or "›" in d or "." in d.split(" ")[0]):
        x.pop("display_url")
    return x


async def web_search(query: str, engine: str = "auto", site: Any = None, exclude_sites: Optional[List[str]] = None, prefer_detail: bool = False,
                     limit: int = 10, fetch_top: int = 0, read: str = "main", section: Any = None, max_chars_each: int = 3000, structured_data: bool = False) -> Dict[str, Any]:
    exclude_sites = exclude_sites or []
    sites = [site] if isinstance(site, str) else list(site or [])
    parts = [query]
    if len(sites) > 1:
        parts.append("(" + " OR ".join(f"site:{s}" for s in sites) + ")")
    elif sites:
        parts.append(f"site:{sites[0]}")
    parts += [f"-site:{s}" for s in exclude_sites]
    q = " ".join(parts)
    order = ["google", "duckduckgo", "bing"] if engine == "auto" else [engine]
    attempts: List[Dict[str, Any]] = []
    results: List[Dict[str, Any]] = []
    used = None
    fallback = None
    for eng in order:
        try:
            async with tab(_engine_url(eng, q, limit)) as page:
                problem = await page_problem(page, page._status)
                got = [] if problem else await page.evaluate(PARSE_RESULTS, eng)
        except Exception as e:
            attempts.append({"engine": eng, **classify_error(e)})
            continue
        if not got:
            attempts.append({"engine": eng, "problem": problem or "no_results_parsed", "status": page._status})
            continue
        if eng == "google":
            await _resolve_google(got)
        rel = _relevance(query, got)
        if rel < 0.3 and len(got) >= 3:
            attempts.append({"engine": eng, "problem": "irrelevant_results", "relevance": round(rel, 2), "sample": [x["title"] for x in got[:2]]})
            fallback = fallback or (got, eng)
            continue
        results, used = got, eng
        break
    if not used and fallback:
        results, used = fallback
    seen = set()
    out = []
    for x in results:
        x = {**x, "url": unwrap_url(x["url"])}
        k = x["url"].split("#")[0]
        if k in seen or re.search(r"google\.[a-z.]+/(search|maps/search)|bing\.com/(search|images)|webcache|translate\.google", k):
            continue
        seen.add(k)
        if any((urlsplit(x["url"]).hostname or "").endswith(s) for s in exclude_sites):
            continue
        out.append(x)
    site_note = None
    if sites:
        def host(x: Dict[str, Any]) -> str:
            h = urlsplit(x["url"]).hostname or ""
            if re.search(r"(^|\.)google\.", h) and x.get("display_url"):
                return re.split(r"[\s/›]", re.sub(r"^https?://", "", x["display_url"]))[0]
            return h
        on = [x for x in out if any(host(x).endswith(s.split("/")[0]) for s in sites)]
        if not on and out:
            site_note = f"{', '.join(sites)} 안의 결과가 없어요(검색엔진이 사이트 밖 결과만 줌). 검색어를 바꾸거나 site 없이 찾아 보세요."
        out = on
    out = [{"rank": i + 1, **_tidy(x)} for i, x in enumerate(out)]
    if prefer_detail:
        out.sort(key=lambda x: (-detail_score(x["url"]), x["rank"]))
    out = out[:limit]
    for x in out[:fetch_top]:
        t0 = time.monotonic()
        opts: Dict[str, Any] = {"max_chars": max_chars_each, "structured_data": structured_data, "mode": "main" if read != "all" else "all"}
        if section:
            opts["section"] = section
        try:
            r = await READER.read(x["url"], opts, annotate=False)
            for k in ("problem", "opened_instead", "note", "sections", "place", "dates", "published", "needs_human"):
                if r.get(k):
                    x[k] = r[k]
            if read != "none" and not section:
                x["text"] = r.get("text", "")
        except Exception as e:
            x.update(classify_error(e))
        x["fetch_ms"] = int((time.monotonic() - t0) * 1000)
    res: Dict[str, Any] = {"query": q, "engine": used}
    if attempts:
        res["engine_failures"] = attempts
    if site_note:
        res["site_note"] = site_note
    res.update(count=len(out), results=out)
    return res


# ---------- place_lookup (Google Maps tbm=map) ----------
def _dist_m(a: Dict[str, float], b: Dict[str, float]) -> int:
    r = math.radians
    x = math.sin(r(b["lat"] - a["lat"]) / 2) ** 2 + math.cos(r(a["lat"])) * math.cos(r(b["lat"])) * math.sin(r(b["lng"] - a["lng"]) / 2) ** 2
    return round(12742000 * math.asin(math.sqrt(x)))


def _g(a: Any, *path: Any) -> Any:
    for k in path:
        try:
            a = a[k]
        except (IndexError, KeyError, TypeError):
            return None
    return a


def _maps_place(p: list) -> Optional[Dict[str, Any]]:
    name, lat, lng = _g(p, 11), _g(p, 9, 2), _g(p, 9, 3)
    if not isinstance(name, str) or not isinstance(lat, (int, float)):
        return None
    today = _g(p, 203, 0, 0)
    c88 = _g(p, 88, 0)
    closed_text = c88 if isinstance(c88, str) and re.search(r"(?i)폐쇄|폐업|영구|closed|閉業|閉店", c88) else None
    names = [x for x in [name, _g(p, 101), _g(p, 18), *(p[2] if isinstance(_g(p, 2), list) else [])] if isinstance(x, str)]
    out: Dict[str, Any] = {"name": name}
    if isinstance(_g(p, 101), str) and p[101] != name:
        out["local_name"] = p[101]
    out["_names"] = " ".join(names)
    rating, reviews = _g(p, 4, 7), _g(p, 4, 8)
    out.update({
        "rating": rating if isinstance(rating, (int, float)) else None, "reviews": reviews if isinstance(reviews, (int, float)) else None,
        "lat": lat, "lng": lng, "address": _g(p, 39) or _g(p, 18) or None,
        "category": ", ".join(p[13]) if isinstance(_g(p, 13), list) else None,
        "closed": _g(p, 23) == 1 or bool(closed_text),
    })
    if closed_text:
        out["closed_text"] = closed_text
    if today:
        out["hours_today"] = f"{today[0]} {', '.join(h[0] for h in (_g(today, 3) or []))}".strip()
    out.update({"phone": _g(p, 178, 0, 0) or None, "website": _g(p, 7, 0) or None, "place_id": _g(p, 78) or None})
    if out["place_id"]:
        out["maps_url"] = f"https://www.google.com/maps/place/?q=place_id:{out['place_id']}"
    return {k: v for k, v in out.items() if v is not None}


async def place_lookup(query: str, near: Optional[Dict[str, float]] = None, radius_m: int = 1000, lang: str = "ko", max_candidates: int = 5) -> Dict[str, Any]:
    t0 = time.monotonic()
    client = await READER._http_client()
    ua = await SHARED.user_agent()

    async def ask(span: int) -> Dict[str, Any]:
        pb = ""
        if near:
            pb = "&pb=" + quote(f"!4m12!1m3!1d{span}!2d{near['lng']}!3d{near['lat']}!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1!7i20!10b1!12m6!2m3!5m1!6e2!20e3!10b1!16b1")
        u = f"https://www.google.com/search?tbm=map&hl={quote(lang)}&gl=jp&q={quote(query)}{pb}"
        resp = await client.get(u, headers={"User-Agent": ua}, timeout=15)
        t = _decode(resp)
        if resp.status != 200 or not t.startswith(")]}'"):
            return {"error_kind": "captcha" if resp.status == 429 or re.search(r"(?i)sorry|captcha", resp.url or "") else f"http_{resp.status}", "places": []}
        try:
            j = json.loads(t[t.index("\n") + 1 :])
        except ValueError:
            return {"error_kind": "parse_failed", "places": []}
        places = [_maps_place(e[14]) for e in (_g(j, 0, 1) or []) if isinstance(_g(e, 14), list)]
        return {"places": [p for p in places if p]}

    r = await ask(max(1000, radius_m * 6))
    if not r["places"] and near and not r.get("error_kind"):
        r = await ask(30000)

    def norm(x: str) -> str:
        x = unicodedata.normalize("NFKC", x).lower()
        x = re.sub(r"[\s・·'’\"&\-_.()（）「」]", "", x)
        return re.sub(r"[ァ-ヶ]", lambda m: chr(ord(m.group(0)) - 0x60), x)

    word = norm(sorted(query.split(), key=len, reverse=True)[0] if query.split() else query)
    lst = []
    for p in r["places"]:
        names = p.pop("_names")
        lst.append({**p, "name_match": word in norm(names)})
    if near:
        lst = sorted(({**p, "distance_m": _dist_m(near, p)} for p in lst), key=lambda p: p["distance_m"])
    in_range = [p for p in lst if p["distance_m"] <= radius_m] if near else lst
    best = next((p for p in in_range if p["name_match"]), None)
    out: Dict[str, Any] = {"query": query, "best": best, "candidates": [p for p in lst[:max_candidates] if p is not best]}
    if not best and lst:
        out["note"] = f"반경 {radius_m}m 안에 이름이 맞는 곳 없음, 후보만" if near else "이름이 맞는 곳 없음, 후보만"
    if r.get("error_kind"):
        out["error_kind"] = r["error_kind"]
    out.update(source="tbm_map", ms=int((time.monotonic() - t0) * 1000))
    return out


# ---------- find_place_photos ----------
BING_IMAGES = r"""() => [...document.querySelectorAll('a.iusc[m]')].map((a) => {
  let m = {}; try { m = JSON.parse(a.getAttribute('m')); } catch {}
  const box = a.closest('li, .iuscp, .imgpt') || a.parentElement;
  const info = (box?.querySelector('.img_info, .imgpt .b_dataList')?.innerText || '').replace(/\s+/g, ' ');
  const dm = info.match(/(\d{2,5})\s*[x×]\s*(\d{2,5})/);
  return { image_url: m.murl, thumb_url: m.turl, source_page: m.purl, title: (m.t || '').replace(/[-]/g, ''), caption: m.desc, width: dm ? +dm[1] : undefined, height: dm ? +dm[2] : undefined };
}).filter((x) => x.image_url)"""


def _pnorm(s: Optional[str]) -> str:
    return re.sub(r"[\s\"'「」『』()（）・·\-_.]", "", (s or "").lower())


async def _photo_search(q: str, name: str, limit: int, min_width: int, require_name: bool) -> Dict[str, Any]:
    async with tab(f"https://www.bing.com/images/search?q={quote(q)}&form=HDRSC2&first=1", 1500) as page:
        problem = await page_problem(page, page._status)
        if problem:
            return {"query": q, "problem": problem, "results": []}
        for _ in range(2):
            await page.mouse.wheel(0, 3000)
            await page.wait_for_timeout(700)
        items = await page.evaluate(BING_IMAGES)
    key = _pnorm(name)
    out = []
    for x in items:
        x["source_site"] = urlsplit(x.get("source_page") or "").hostname
        x["name_match"] = key in _pnorm(f"{x.get('title')} {x.get('caption')} {x.get('source_page')}") if key else None
        if not min_width or not x.get("width") or x["width"] >= min_width:
            out.append({k: v for k, v in x.items() if v is not None})
    if require_name:
        out = [x for x in out if x.get("name_match")]
    out.sort(key=lambda x: x.get("name_match") is not True)
    return {"query": q, "engine": "bing_images", "count": min(len(out), limit),
            "note": "name_match=false 인 사진은 다른 대상(같은 이름의 상품·인물 등)일 수 있어요. source_page를 열어 확인하거나 contact_sheet로 눈으로 확인하세요.",
            "results": out[:limit]}


async def find_place_photos(name: str, area: Optional[str] = None, site: Any = None, limit: int = 20, min_width: int = 400, require_name: bool = False) -> Dict[str, Any]:
    sites = [f"site:{s}" for s in ([site] if isinstance(site, str) else list(site or []))]
    plain = name.replace('"', "")
    first = f'"{name}"' if " " in name and not name.startswith('"') else name
    tries = list(dict.fromkeys(" ".join(x for x in a if x) for a in ([first, area, *sites], [plain, area, *sites], [plain, area], [plain])))
    attempts = []
    for q in tries:
        got = await _photo_search(q, name, limit, min_width, require_name)
        if got.get("count") or got.get("problem"):
            return {**got, "tried": attempts} if attempts else got
        attempts.append(q)
        await asyncio.sleep(0.8)
    # Image search found nothing: photos from the place's own pages found by web search.
    try:
        ws = await web_search(" ".join(x for x in [plain, area] if x), site=site, limit=5)
    except Exception:
        ws = {"results": []}
    key = _pnorm(name)
    out = []
    for x in [y for y in ws["results"] if not re.search(r"youtube\.|instagram\.|x\.com|twitter\.", y["url"])][:3]:
        try:
            async with tab(x["url"], 1500) as page:
                for im in await js(page, "images", {"minWidth": min_width, "base": page.url}):
                    out.append({k: v for k, v in {
                        "image_url": im["url"], "source_page": page.url, "source_site": urlsplit(page.url).hostname, "title": im.get("alt") or x["title"],
                        "caption": im.get("caption"), "width": im.get("width"), "name_match": (key in _pnorm(f"{x['title']} {im.get('alt') or ''} {im.get('caption') or ''}")) if key else None,
                        "from": im.get("source"),
                    }.items() if v is not None})
        except Exception:
            pass
        await asyncio.sleep(0.5)
    res = [y for y in out if y.get("name_match")] if require_name else out
    return {"query": tries[0], "engine": "place_pages", "search_engine": ws.get("engine"), "count": len(res[:limit]), "tried": attempts,
            "note": "이미지 검색이 0건이라 검색 상위 페이지(공식·타베로그 등)의 사진을 모았어요. 페이지 제목에 이름이 있으면 name_match=true예요.", "results": res[:limit]}


# ---------- fetch_images ----------
WIKI_WIDTHS = [250, 330, 500, 960, 1280, 1920]


def wikimedia_thumb(u: str, w: Optional[int]) -> Optional[str]:
    try:
        x = urlsplit(u)
    except ValueError:
        return None
    if not re.search(r"(^|\.)wikimedia\.org$", x.hostname or ""):
        return None
    bw = next((v for v in WIKI_WIDTHS if v >= (w or 960)), WIKI_WIDTHS[-1])
    origin = f"{x.scheme}://{x.netloc}"
    th = re.match(r"^(.*/thumb/[0-9a-f]/[0-9a-f]{2}/[^/]+/)(\d+)px-(.+)$", x.path)
    if th:
        return None if int(th.group(2)) == bw and not x.query else f"{origin}{th.group(1)}{bw}px-{th.group(3)}"
    m = re.match(r"^/wikipedia/([^/]+)/([0-9a-f])/([0-9a-f]{2})/([^/]+)$", x.path)
    if not m or not w:
        return f"{origin}{x.path}" if x.query and "utm_" in x.query else None
    f = m.group(4)
    suffix = f + ".png" if re.search(r"(?i)\.(svg|tiff?|pdf)$", f) else f
    return f"https://upload.wikimedia.org/wikipedia/{m.group(1)}/thumb/{m.group(2)}/{m.group(3)}/{f}/{bw}px-{suffix}"


_host_last: Dict[str, float] = {}


async def _polite_gap(host: str, gap_s: float) -> None:
    now = time.monotonic()
    wait = _host_last.get(host, 0) + gap_s - now
    _host_last[host] = max(now, _host_last.get(host, 0) + gap_s)
    if wait > 0:
        await asyncio.sleep(wait)


async def _get_with_backoff(page: Any, url: str, headers: Dict[str, str], max_tries: int, deadline: float) -> Dict[str, Any]:
    host = urlsplit(url).hostname or ""
    attempt, last = 0, None
    while attempt < max_tries and time.monotonic() < deadline:
        attempt += 1
        await _polite_gap(host, 1.2 if re.search(r"wikimedia|wikipedia", host) else 0.4)
        try:
            await page.set_extra_http_headers(headers)
            resp = await page.goto(url, wait_until="commit", timeout=max(5000, min(30000, (deadline - time.monotonic()) * 1000)))
            if not resp:
                raise RuntimeError("no response")
            if resp.status in (429, 503):
                ra = float(resp.headers.get("retry-after") or 0)
                wait = min((ra if ra > 0 else 2 * 2 ** (attempt - 1)) + 0.5 * (attempt % 2), 30, max(0, deadline - time.monotonic()))
                last = {"status": resp.status, "waited_ms": int(wait * 1000)}
                _host_last[host] = time.monotonic() + wait
                await asyncio.sleep(wait)
                continue
            return {"status": resp.status, "body": await resp.body(), "ctype": resp.headers.get("content-type", ""), "final_url": resp.url, "attempts": attempt}
        except Exception as e:
            last = classify_error(e)
            if last["error_kind"] == "dns":
                break
            await asyncio.sleep(1.5 * attempt)
    if not last and time.monotonic() >= deadline:
        last = {"error_kind": "time_budget_exceeded"}
    return {"fail": last, "attempts": attempt}


RESIZE = r"""async ({ b64, maxW, type }) => {
  const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const bmp = await createImageBitmap(new Blob([bin]));
  const scale = maxW && bmp.width > maxW ? maxW / bmp.width : 1;
  const c = document.createElement('canvas'); c.width = Math.round(bmp.width * scale); c.height = Math.round(bmp.height * scale);
  c.getContext('2d').drawImage(bmp, 0, 0, c.width, c.height);
  return { b64: c.toDataURL(type, 0.85).split(',')[1], width: c.width, height: c.height, orig_width: bmp.width, orig_height: bmp.height };
}"""


async def _resize(buf: bytes, max_w: int) -> Optional[Dict[str, Any]]:
    try:
        async with tab() as page:
            return await page.evaluate(RESIZE, {"b64": base64.b64encode(buf).decode(), "maxW": max_w, "type": "image/jpeg"})
    except Exception:
        return None


async def fetch_images(urls: List[str], subdir: str = "images", max_width: Optional[int] = None, referer: Optional[str] = None, user_agent: Optional[str] = None,
                       headers: Optional[Dict[str, str]] = None, return_images: str = "none", thumb_width: int = 360, use_cache: bool = True,
                       max_tries: int = 6, time_budget_ms: int = 150000) -> Dict[str, Any]:
    cache_dir = DATA / "cache"
    out_dir = DATA / "downloads" / _safe_name(subdir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    ua = user_agent or await SHARED.user_agent()
    results, images = [], []
    t0 = time.monotonic()
    deadline = t0 + time_budget_ms / 1000
    async with tab() as page:
        for i, u0 in enumerate(urls, 1):
            item: Dict[str, Any] = {"url": u0}
            if time.monotonic() >= deadline:
                results.append({**item, "ok": False, "error_kind": "not_attempted_time_budget"})
                continue
            try:
                thumb = wikimedia_thumb(u0, max_width)
                buf = ctype = final = None
                for u in ([thumb, u0] if thumb else [u0]):
                    key = hashlib.sha1(u.encode()).hexdigest()
                    meta = cache_dir / f"{key}.json"
                    if use_cache and meta.exists():
                        m = json.loads(meta.read_text(encoding="utf-8"))
                        buf, ctype, final = (cache_dir / f"{key}.bin").read_bytes(), m["content_type"], m["final_url"]
                        item["from_cache"] = True
                        break
                    h = {**(headers or {}), "User-Agent": ua, **({"Referer": referer} if referer else {})}
                    g = await _get_with_backoff(page, u, h, max_tries, deadline)
                    item["attempts"] = item.get("attempts", 0) + g["attempts"]
                    if g.get("fail"):
                        item["last_failure"] = g["fail"]
                        continue
                    if g["status"] >= 400:
                        item["last_failure"] = {"status": g["status"], "error_kind": "http_403_forbidden (Referer 필요할 수 있음)" if g["status"] == 403 else f"http_{g['status']}"}
                        continue
                    if not g["ctype"].startswith("image/"):
                        item["last_failure"] = {"status": g["status"], "error_kind": "not_an_image", "content_type": g["ctype"]}
                        continue
                    buf, ctype, final = g["body"], g["ctype"], g["final_url"]
                    (cache_dir / f"{key}.bin").write_bytes(buf)
                    meta.write_text(json.dumps({"url": u, "final_url": final, "content_type": ctype, "saved": time.strftime("%Y-%m-%dT%H:%M:%S")}), encoding="utf-8")
                    break
                if not buf:
                    results.append({**item, "ok": False})
                    continue
                if final != u0:
                    item["final_url"] = final
                ext = (re.search(r"image/(\w+)", ctype or "") or [None, "jpg"])[1].replace("jpeg", "jpg")
                out_buf, dims = buf, {}
                if max_width and "svg" not in (ctype or ""):
                    rz = await _resize(buf, max_width)
                    if rz:
                        out_buf, ext = base64.b64decode(rz["b64"]), "jpg"
                        dims = {k: rz[k] for k in ("width", "height", "orig_width", "orig_height")}
                base = _safe_name(re.sub(r"\.\w+$", "", urlsplit(final or u0).path.rsplit("/", 1)[-1] or "image"))
                f = out_dir / f"{i:02d}-{base}.{ext}"
                f.write_bytes(out_buf)
                item.update(ok=True, file=str(f), bytes=len(out_buf), content_type=ctype, **dims)
                if return_images != "none" and "svg" not in (ctype or ""):
                    if return_images == "full":
                        images.append((base64.b64encode(out_buf).decode(), ctype if ext != "jpg" else "image/jpeg"))
                    else:
                        t = await _resize(buf, thumb_width)
                        if t:
                            images.append((t["b64"], "image/jpeg"))
            except Exception as e:
                item.update(ok=False, **classify_error(e))
            results.append(item)
    return {"obj": {"out_dir": str(out_dir), "ok": sum(1 for r in results if r.get("ok")), "failed": sum(1 for r in results if not r.get("ok")),
                    "elapsed_ms": int((time.monotonic() - t0) * 1000), "results": results}, "images": images}


# ---------- contact_sheet / static_map (pages Claude owns: main world is fine) ----------
SHEET = r"""async ({ cells, columns, thumb, title }) => {
  const el = (tag, css, text) => { const e = document.createElement(tag); if (css) e.style.cssText = css; if (text != null) e.textContent = text; return e; };
  document.body.replaceChildren(); document.body.style.cssText = 'margin:8px;font:12px sans-serif;background:#fff';
  if (title) document.body.append(el('div', 'font-size:15px;font-weight:bold;margin:4px', title));
  const grid = el('div', `display:grid;grid-template-columns:repeat(${columns},${thumb}px);gap:8px`);
  const loads = [];
  cells.forEach((c, i) => {
    const box = el('div', `width:${thumb}px;height:${thumb}px;background:#eee;display:flex;align-items:center;justify-content:center;overflow:hidden`);
    if (c.err) box.append(el('span', 'color:#c00', c.err));
    else {
      const im = el('img', 'max-width:100%;max-height:100%');
      loads.push(new Promise((r) => { im.onload = im.onerror = r; setTimeout(r, 20000); }));
      im.src = c.b64 ? URL.createObjectURL(new Blob([Uint8Array.from(atob(c.b64), (ch) => ch.charCodeAt(0))], { type: c.mime })) : c.url;
      box.append(im);
    }
    const cap = el('div', `width:${thumb}px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis`);
    cap.append(el('b', '', `${i + 1} `), document.createTextNode(c.caption || ''));
    const cell = el('div'); cell.append(box, cap); grid.append(cell);
  });
  document.body.append(grid);
  await Promise.all(loads);
  return [...document.images].map((im) => (im.naturalWidth ? `${im.naturalWidth}x${im.naturalHeight}` : 'failed'));
}"""


async def contact_sheet(items: List[Dict[str, str]], columns: int = 4, thumb: int = 240, title: Optional[str] = None) -> Dict[str, Any]:
    root = DATA.resolve()
    cells = []
    for it in items:
        c: Dict[str, Any] = {"caption": it.get("caption") or (Path(it["file"]).name if it.get("file") else it.get("url"))}
        if it.get("file"):
            f = Path(it["file"]).resolve()
            if root not in f.parents:
                c["err"] = "data 폴더 밖 파일은 못 읽어요"
            elif not f.exists():
                c["err"] = "파일 없음"
            else:
                ext = f.suffix[1:].lower().replace("jpg", "jpeg")
                c.update(b64=base64.b64encode(f.read_bytes()).decode(), mime="image/svg+xml" if ext == "svg" else f"image/{ext}")
        elif it.get("url"):
            c["url"] = it["url"]
        cells.append(c)
    async with tab("about:blank") as page:
        await page.set_viewport_size({"width": columns * (thumb + 8) + 16, "height": 100})
        sizes = await page.evaluate(SHEET, {"cells": cells, "columns": columns, "thumb": thumb, "title": title}, isolated_context=False)
        buf = await page.screenshot(type="jpeg", quality=80, full_page=True)
    return {"buf": buf, "info": {"count": len(cells), "image_sizes": sizes}}


TILES = {
    "esri": ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}", "Tiles © Esri"),
    "esri_satellite": ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", "Tiles © Esri"),
    "osm": ("https://tile.openstreetmap.org/{z}/{x}/{y}.png", "© OpenStreetMap contributors"),
    "carto": ("https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png", "© OpenStreetMap © CARTO"),
    "gsi": ("https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png", "地理院タイル"),
}
MAP = r"""({ pts, tileUrl, attr, route, zoom, title }) => {
  document.body.replaceChildren();
  if (title) { const t = document.createElement('div'); t.className = 'ttl'; t.textContent = title; document.body.append(t); }
  const m = document.createElement('div'); m.id = 'm'; document.body.append(m);
  const map = L.map('m', { zoomControl: false, attributionControl: true, zoomSnap: 0.25 });
  const layer = L.tileLayer(tileUrl, { attribution: attr, maxZoom: 19, crossOrigin: true });
  window.__tilesDone = false; window.__tileErr = 0;
  layer.on('tileerror', () => { window.__tileErr++; });
  layer.on('load', () => { window.__tilesDone = true; });
  layer.addTo(map);
  const ll = pts.map((p) => [p.lat, p.lon]);
  if (route && ll.length > 1) L.polyline(ll, { color: '#1c7ed6', weight: 4, opacity: 0.8 }).addTo(map);
  for (const p of pts) {
    const pin = document.createElement('div'); pin.className = 'pin'; pin.style.setProperty('--c', p.color); pin.textContent = p.n;
    const mk = L.marker([p.lat, p.lon], { icon: L.divIcon({ className: '', html: pin, iconSize: [30, 30], iconAnchor: [15, 15] }) }).addTo(map);
    if (p.label) mk.bindTooltip(document.createTextNode(p.label), { permanent: true, direction: 'right', offset: [14, 0], className: 'lab' });
  }
  if (zoom) map.setView(ll[0], zoom); else if (ll.length === 1) map.setView(ll[0], 16); else map.fitBounds(ll, { padding: [50, 50], maxZoom: 17 });
}"""


async def static_map(points: List[Dict[str, Any]], route: bool = True, width: int = 900, height: int = 650, tiles: str = "esri", zoom: Optional[float] = None,
                     subdir: str = "maps", name: str = "map", title: Optional[str] = None) -> Dict[str, Any]:
    tile_url, attr = TILES.get(tiles, TILES["esri"])
    pts = [{"lat": float(p["lat"]), "lon": float(p["lon"]), "label": p.get("label") or "", "n": p.get("number", i + 1), "color": p.get("color") or "#d6336c"} for i, p in enumerate(points)]
    css = (_VENDOR / "leaflet.css").read_text(encoding="utf-8") + (
        " html,body,#m{margin:0;width:100%;height:100%} .pin{box-sizing:border-box;background:var(--c);color:#fff;border:2px solid #fff;border-radius:50%;"
        "width:28px;height:28px;line-height:24px;text-align:center;font:bold 13px sans-serif;box-shadow:0 1px 4px #0008} .lab{background:#fffe;border:0;"
        "box-shadow:0 1px 3px #0006;font:12px sans-serif;padding:1px 4px} .ttl{position:absolute;top:8px;left:50%;transform:translateX(-50%);z-index:999;"
        "background:#fffe;padding:4px 10px;font:bold 15px sans-serif;border-radius:4px}")
    async with tab("about:blank") as page:
        await page.set_viewport_size({"width": width, "height": height})
        await page.add_style_tag(content=css)
        await page.add_script_tag(content=(_VENDOR / "leaflet.js").read_text(encoding="utf-8"))
        await page.evaluate(MAP, {"pts": pts, "tileUrl": tile_url, "attr": attr, "route": route, "zoom": zoom, "title": title}, isolated_context=False)
        try:
            await page.wait_for_function("() => window.__tilesDone", timeout=25000, isolated_context=False)
        except Exception:
            pass
        await page.wait_for_timeout(400)
        tile_errors = await page.evaluate("() => window.__tileErr", isolated_context=False)
        buf = await page.screenshot(type="png")
    out_dir = DATA / "downloads" / _safe_name(subdir)
    out_dir.mkdir(parents=True, exist_ok=True)
    f = out_dir / f"{_safe_name(name)}.png"
    f.write_bytes(buf)
    return {"buf": buf, "info": {"file": str(f), "width": width, "height": height, "tiles": tiles, "points": len(pts), "tile_errors": tile_errors}}


# ---------- check_domains ----------
async def check_domains(urls: List[str], use_browser: bool = False) -> Dict[str, Any]:
    client = await READER._http_client()
    ua = await SHARED.user_agent()
    out = []
    for u0 in urls:
        u = u0 if re.match(r"^https?:", u0) else f"https://{u0}"
        t0 = time.monotonic()
        if use_browser:
            try:
                async with tab(u) as page:
                    out.append({k: v for k, v in {"url": u, "status": page._status, "final_url": page.url, "problem": await page_problem(page, page._status),
                                "browser": "scrapling/" + ("headless" if HEADLESS else "window"), "ms": int((time.monotonic() - t0) * 1000)}.items() if v is not None})
            except Exception as e:
                out.append({"url": u, **classify_error(e), "ms": int((time.monotonic() - t0) * 1000)})
            continue
        try:
            resp = await client.get(u, headers={"User-Agent": ua}, timeout=15)
            body = _decode(resp)[:20000]
            m = re.search(r"<title[^>]*>([^<]*)", body, re.I)
            title = m.group(1).strip()[:100] if m else None
            bot = "bot_check_page" if (re.search(r"(?i)just a moment|attention required|checking your browser|verify you are human|captcha", title or "") or re.search(r"challenge-platform|__cf_chl", body)) else None
            out.append({k: v for k, v in {"url": u, "status": resp.status, "ok": 200 <= resp.status < 400 and not bot, "final_url": resp.url if resp.url != u else None,
                        "title": title, "problem": bot, "ms": int((time.monotonic() - t0) * 1000)}.items() if v is not None})
        except Exception as e:
            out.append({"url": u, "ok": False, **classify_error(e), "ms": int((time.monotonic() - t0) * 1000)})
    return {"checked": len(out), "open": sum(1 for x in out if x.get("ok")), "results": out}


# ---------- collect_post ----------
DC_COMMENTS = r"""() => {
  const out = [];
  for (const li of document.querySelectorAll('.cmt_list li.ub-content, .comment_wrap li.ub-content')) {
    const id = (li.id || '').replace(/^comment_li_/, '');
    const w = li.querySelector('.gall_writer');
    const author = (w?.getAttribute('data-nick') || w?.querySelector('.nickname')?.innerText || w?.innerText || '').replace(/\s+/g, ' ').trim() || null;
    const uid = w?.getAttribute('data-uid') || null;
    const ip = w?.getAttribute('data-ip') || null;
    let text = (li.querySelector('.usertxt')?.innerText || '').trim();
    if (!text && li.querySelector('.written_dccon, .cmt_img img')) text = '[디시콘]';
    const date = (li.querySelector('span.date_time')?.innerText || '').trim() || null;
    const reply = !!li.closest('.reply_list, .reply');
    if (!author && !text) continue;
    out.push({ id: id || undefined, author, uid: uid || undefined, ip: ip || undefined, date, reply: reply || undefined, text });
  }
  return out;
}"""
DC_HEAD = r"""() => {
  const T = (s) => (document.querySelector(s)?.innerText || '').replace(/\s+/g, ' ').trim();
  const w = document.querySelector('.gallview_head .gall_writer, .view_content_wrap .gall_writer');
  return {
    title: T('.title_subject, .tit_view, h3.title') || document.title,
    author: (w?.getAttribute('data-nick') || w?.innerText || '').replace(/\s+/g, ' ').trim() || null,
    date: (document.querySelector('.gall_date')?.getAttribute('title') || T('.gall_date')) || null,
    views: T('.gall_count') || null,
    comment_count_shown: T('.comment_count, .gall_comment') || null,
    body: (document.querySelector('.gallview_contents, .write_div, .writing_view_box')?.innerText || '').trim(),
  };
}"""
DC_NEXT = r"""(done) => { for (const a of document.querySelectorAll('.cmt_paging a')) { const n = (a.innerText || '').trim(); if (/^\d+$/.test(n) && !done.includes(n)) return n; } return null; }"""
DC_CLICK = r"""(n) => { const a = [...document.querySelectorAll('.cmt_paging a')].find((x) => (x.innerText || '').trim() === n); if (a) { a.click(); return true; } return false; }"""
GENERIC_COMMENTS = r"""() => {
  const nodes = new Set();
  for (const s of ['[class*="comment" i]', '[class*="reply" i]', '[class*="cmt" i]', '[id*="comment" i]']) for (const el of document.querySelectorAll(s)) nodes.add(el);
  const UI = /^(reply|replies|답글|답글\s?쓰기|댓글|댓글\s?쓰기|comments?|\d+\s*(comments?|replies|댓글|개의 댓글)|\[[-+–]\]|more|더보기)$/i;
  const SKIP = /^(SCRIPT|STYLE|NOSCRIPT|BUTTON|TEXTAREA|SELECT|OPTION|FORM|INPUT|TEMPLATE)$/;
  const buckets = new Map(); const order = []; const pos = new Map(); let idx = 0;
  const tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = tw.nextNode(); n; n = tw.nextNode(), idx++) {
    const t = n.nodeValue.replace(/\s+/g, ' ').trim();
    if (!t || UI.test(t)) continue;
    let el = n.parentElement, owner = null, skip = false;
    for (; el && el !== document.body; el = el.parentElement) {
      if (SKIP.test(el.tagName)) { skip = true; break; }
      if (nodes.has(el)) { owner = el; break; }
    }
    if (skip || !owner || (n.parentElement.checkVisibility && !n.parentElement.checkVisibility())) continue;
    if (!buckets.has(owner)) { buckets.set(owner, []); pos.set(owner, []); order.push(owner); }
    buckets.get(owner).push(t); pos.get(owner).push(idx);
  }
  const kids = new Map(); const firstKid = new Map();
  for (const el of order) for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) if (buckets.has(a)) {
    kids.set(a, (kids.get(a) || 0) + 1);
    if (!firstKid.has(a)) firstKid.set(a, pos.get(el)[0]);
    break;
  }
  const wrapper = (el) => (kids.get(el) || 0) >= 3 && pos.get(el).filter((i) => i > firstKid.get(el)).length >= kids.get(el);
  const seen = new Set(); const out = [];
  for (const el of order.slice(0, 1500)) {
    if (wrapper(el)) continue;
    const text = buckets.get(el).join(' ').replace(/\s+/g, ' ').trim();
    if (text.length < 2 || text.length > 4000 || UI.test(text) || seen.has(text)) continue;
    seen.add(text);
    out.push({ text: text.slice(0, 1500) });
  }
  return out.slice(0, 300);
}"""


async def collect_post(url: str, max_comment_pages: int = 30, include_body: bool = True, wait_ms: int = 1800) -> Dict[str, Any]:
    async with tab(url, wait_ms) as page:
        problem = await page_problem(page, page._status)
        if problem in ("captcha", "bot_check"):
            return {"blocked": True, "reason": problem, "url": page.url, "status": page._status,
                    "note": "봇 체크/로그인이 필요해요. read로 열어 needs_human 흐름(사용자 1회 인증)을 쓰세요."}
        if re.search(r"(^|\.)dcinside\.com$", urlsplit(page.url).hostname or ""):
            head = await page.evaluate(DC_HEAD)
            found: Dict[str, Any] = {}

            def add(cs: List[Dict[str, Any]]) -> None:
                for c in cs:
                    found[c.get("id") or f"{c.get('author')}|{c.get('date')}|{c.get('text')}"[:160]] = c

            add(await page.evaluate(DC_COMMENTS))
            seen: List[str] = []
            for _ in range(max_comment_pages):
                nxt = await page.evaluate(DC_NEXT, seen)
                if not nxt:
                    break
                seen.append(nxt)
                if not await page.evaluate(DC_CLICK, nxt):
                    break
                await page.wait_for_timeout(1200)
                add(await page.evaluate(DC_COMMENTS))
            comments = list(found.values())
            out = {"site": "dcinside", "url": page.url, "title": head["title"], "author": head["author"], "date": head["date"], "views": head["views"],
                   "comment_count_shown": head["comment_count_shown"], "comment_pages_read": len(seen) + 1, "comments_collected": len(comments)}
            if include_body:
                out["body"] = head["body"]
            out["comments"] = comments
            return out
        m = await js(page, "main") if include_body else None
        title = ((m or {}).get("title") or await page.title()).strip()
        comments = await page.evaluate(GENERIC_COMMENTS)
        out = {"site": "generic", "best_effort": True, "url": page.url, "title": title,
               "note": "전용 지원이 없는 사이트예요. 본문은 Readability로, 댓글은 class/id가 comment·reply·cmt인 블록을 휴리스틱으로 모았어요(정확도 사이트마다 다름)."}
        if include_body:
            out["body"] = (m or {}).get("text", "")
        out.update(comments_collected=len(comments), comments=comments)
        return out


# ---------- hotel_search (Google Hotels) ----------
HOTELS = r"""() => {
  const PRICE = /[₩$€£¥]\s?[\d,]{3,}/;
  const RATING = /(\d\.\d)\s*\/\s*5|\(\s*[\d.,KkMm]+\s*\)/;
  const all = [...document.querySelectorAll('div,li,a')];
  const cards = all.filter((el) => { const t = el.innerText || ''; return PRICE.test(t) && RATING.test(t) && t.length > 20 && t.length < 500; });
  const minimal = cards.filter((el) => !cards.some((o) => o !== el && el.contains(o)));
  const out = []; const seen = new Set();
  for (const el of minimal) {
    const text = (el.innerText || '').replace(/\s+/g, ' ').trim();
    const pm = text.match(/[₩$€£¥]\s?[\d,]{3,}/);
    if (!pm) continue;
    const name = text.slice(0, pm.index).replace(/^Sponsored\s*·?\s*/i, '').trim();
    if (!name || name.length > 90 || seen.has(name)) continue;
    seen.add(name);
    const rating = (text.match(/(\d\.\d)\s*\/\s*5/) || [])[1];
    const reviews = (text.match(/\(\s*([\d.,KkMm]+)\s*\)/) || [])[1];
    const star = (text.match(/(\d)[\- ]star/i) || [])[1];
    const tail = text.slice(pm.index + pm[0].length);
    const amenities = (tail.match(/·\s*([^·]{2,28}?)(?=\s*·|$)/g) || []).map((s) => s.replace(/·\s*/, '').trim()).filter((s) => !/star hotel/i.test(s)).slice(0, 4);
    out.push({ name, price: pm[0].replace(/\s/g, ''), rating: rating ? +rating : undefined, reviews, star: star ? +star : undefined, amenities: amenities.length ? amenities : undefined });
  }
  return out;
}"""


async def hotel_search(destination: Optional[str] = None, query: Optional[str] = None, checkin: Optional[str] = None, checkout: Optional[str] = None,
                       currency: str = "KRW", lang: str = "en", max_results: int = 25, sort: Optional[str] = None) -> Dict[str, Any]:
    dest = destination or query
    if not dest:
        raise ValueError("destination(또는 query)가 필요해요")
    params = {"q": f"{dest} hotels", "hl": lang, "curr": currency, **({"checkin": checkin} if checkin else {}), **({"checkout": checkout} if checkout else {})}
    async with tab(f"https://www.google.com/travel/search?{urlencode(params)}", 4500) as page:
        for _ in range(3):
            try:
                await page.mouse.wheel(0, 3000)
            except Exception:
                pass
            await page.wait_for_timeout(600)
        hotels = await page.evaluate(HOTELS)
        url = page.url
    res = hotels[:max_results]
    num = lambda p: int(re.sub(r"[^\d]", "", str(p or "")) or 0) or float("inf")  # noqa: E731
    if sort == "price":
        res = sorted(res, key=lambda h: num(h.get("price")))
    elif sort == "rating":
        res = sorted(res, key=lambda h: -(h.get("rating") or 0))
    return {"source": "google_hotels", "url": url, "destination": dest, "checkin": checkin, "checkout": checkout, "currency": currency,
            "note": "구글 호텔 카드에서 뽑았어요(가격=해당 날짜 1박). 면적은 호텔 검색엔 안 나와요(렌탈만). 인원은 기본 2인 기준. best_effort — 못 뽑은 카드가 있으면 read로 원본을 보세요.",
            "count": len(res), "hotels": res}


# ---------- transit_route (Google Maps) ----------
TRANSIT_TEXT = r"""() => ((document.querySelector('[role="main"]') || document.body).innerText || '').replace(/\r/g, '').replace(/\n+/g, ' ').replace(/\s{2,}/g, ' ')"""
TRANSIT_CARDS = r"""() => {
  const main = document.querySelector('[role="main"]') || document.body;
  const DUR = /^\s*(\d+\s*(?:hr|h|시간)\s*)?(\d+\s*(?:min|분))?/;
  const TIME = /(\d{1,2}:\d{2}\s*(?:AM|PM)?|(?:오전|오후)\s*\d{1,2}:\d{2})/gi;
  const all = [...main.querySelectorAll('div')].filter((el) => { const t = el.innerText || ''; return t.length < 600 && /^\s*\d+\s*(hr|h|min|시간|분)/.test(t) && /\d{1,2}:\d{2}/.test(t); });
  const cards = all.filter((el) => !all.some((o) => o !== el && o.contains(el)));
  const out = [];
  for (const card of cards.slice(0, 8)) {
    const text = card.innerText.replace(/\r/g, '');
    const lines = text.split('\n').map((s) => s.trim()).filter(Boolean);
    const dm = lines[0].match(DUR);
    const times = (lines.find((l) => /—|–|-/.test(l) && /\d{1,2}:\d{2}/.test(l)) || '').match(TIME) || [];
    const legs = []; let cur = null; let started = false; let done = false;
    const tw = document.createTreeWalker(card, NodeFilter.SHOW_ELEMENT);
    for (let n = tw.currentNode; n && !done; n = tw.nextNode()) {
      const lab = n.getAttribute && n.getAttribute('aria-label');
      if (n.tagName === 'IMG' && n.alt) { started = true; if (!cur) { cur = { mode: n.alt, lines: [] }; legs.push(cur); } else if (!cur.mode) cur.mode = n.alt; continue; }
      if (lab && /,\s*then|다음/i.test(lab)) { cur = null; continue; }
      if (n.tagName === 'SPAN' && !n.children.length) {
        const t = (n.innerText || '').trim();
        if (!t) continue;
        if (/\d{1,2}:\d{2}/.test(t)) { if (started) done = true; continue; }
        if (!started || /^(Details|every|from)\b/i.test(t)) continue;
        if (!cur) { cur = { mode: '', lines: [] }; legs.push(cur); }
        if (!cur.lines.includes(t)) cur.lines.push(t);
      }
    }
    const fare = (text.match(/[¥₩$€£]\s?[\d,.]+|[\d,]{3,}\s?원/) || [])[0];
    const from = (text.match(/\bfrom\s+([^\n]+)/) || [])[1];
    const every = (text.match(/every\s+\d+\s*min|\d+\s*분마다/i) || [])[0];
    const via = legs.map((l) => l.lines.length ? l.lines.join('/') : l.mode).filter(Boolean).join(' → ');
    out.push({
      duration: [dm?.[1], dm?.[2]].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim() || lines[0],
      depart: times[0], arrive: times[1], via: via || undefined,
      legs: legs.length ? legs.map((l) => ({ mode: l.mode || undefined, lines: l.lines.length ? l.lines : undefined })) : undefined,
      board: from ? from.trim() : undefined, frequency: every, fare: fare ? fare.replace(/\s/g, '') : undefined,
    });
  }
  return out;
}"""


async def transit_route(origin: str, destination: str, mode: str = "transit", lang: str = "en") -> Dict[str, Any]:
    if not origin or not destination:
        raise ValueError("origin과 destination이 필요해요")
    url = f"https://www.google.com/maps/dir/?api=1&origin={quote(origin)}&destination={quote(destination)}&travelmode={mode}&hl={lang}"
    async with tab(url, 4500) as page:
        await page.wait_for_timeout(1500)
        text = await page.evaluate(TRANSIT_TEXT)
        routes = []
        if mode == "transit":
            try:
                routes = await page.evaluate(TRANSIT_CARDS)
            except Exception:
                routes = []
        final = page.url
    if not routes:
        rx = re.compile(r"(\d+\s*(?:hr|시간)?\s*\d*\s*(?:min|분))\s+(\d{1,2}:\d{2}\s*(?:AM|PM)?)\s*[–—-]\s*(\d{1,2}:\d{2}\s*(?:AM|PM)?)\s+([^\d¥₩$]{2,40}?)(?:\s+.*?([¥₩$][\d,]+))?(?=\s+\d|\s*$)", re.I)
        for m in rx.finditer(text):
            if len(routes) >= 8:
                break
            win = text[m.start() : m.end() + 140]
            fare = m.group(5) or (re.search(r"[¥₩$][\d,]{2,}|\b[\d,]{3,}\s?원", win) or [None])[0]
            routes.append({k: v for k, v in {"duration": re.sub(r"\s+", " ", m.group(1)).strip(), "depart": m.group(2).strip(), "arrive": m.group(3).strip(),
                                             "via": re.sub(r"\s+", " ", m.group(4).strip()), "fare": fare}.items() if v})
    if not routes:
        for m in re.finditer(r"(\d+\s*(?:hr|시간)?\s*\d*\s*(?:min|분))\s+([\d.]+\s*(?:km|m|mi))\s+via\s+([^\d]{2,40}?)(?=\s+\d|\s*$)", text, re.I):
            if len(routes) >= 8:
                break
            routes.append({"duration": re.sub(r"\s+", " ", m.group(1)).strip(), "distance": m.group(2).strip(), "via": m.group(3).strip()})
    out: Dict[str, Any] = {"source": "google_maps", "mode": mode, "url": final, "origin": origin, "destination": destination, "count": len(routes), "routes": routes}
    if not routes:
        out.update(note="경로를 구조화하지 못했어요 — raw_excerpt로 직접 확인하세요(구글지도 UI가 바뀌었을 수 있음).", raw_excerpt=text[:1000])
    return out


# ---------- save_places (Google Maps lists; needs a Google login in the shared browser) ----------
MAPS_SAVE_BTN = re.compile(r"^(저장|저장됨|Save|Saved|保存|保存済み)$")
SIGNED = r"""() => {
  const acct = document.querySelector('a[href*="SignOutOptions"], a[aria-label*="Google 계정"], a[aria-label*="Google Account"], a[aria-label*="Google アカウント"]');
  const signin = document.querySelector('a[href*="accounts.google.com/ServiceLogin"], a[href*="accounts.google.com/v3/signin"], a[href*="accounts.google.com/signin"]')
    || [...document.querySelectorAll('a, button')].find((e) => /^(로그인|Sign in|ログイン)$/.test((e.getAttribute('aria-label') || e.innerText || '').trim()) && e.getClientRects().length);
  return acct ? 'in' : signin ? 'out' : null;
}"""
PANEL_READY = r"""() => {
  const h = [...document.querySelectorAll('h1')].find((x) => x.innerText.trim() && x.closest('[role=main]')?.getAttribute('aria-label')?.trim() === x.innerText.trim());
  return !!h || !!document.querySelector('a[href*="ServiceLogin"]') || document.querySelectorAll('[role=feed] a[href*="/maps/place/"]').length > 1;
}"""
PANEL = r"""() => {
  const h = [...document.querySelectorAll('h1')].find((x) => x.innerText.trim() && x.closest('[role=main]')?.getAttribute('aria-label')?.trim() === x.innerText.trim());
  if (!h) {
    if (document.querySelector('a[href*="ServiceLogin"]') && !document.querySelector('[role=feed]')) return { error: 'not_logged_in' };
    return { error: 'not_a_single_place', candidates: [...document.querySelectorAll('[role=feed] a[href*="/maps/place/"]')].slice(0, 5).map((a) => a.getAttribute('aria-label')) };
  }
  return { name: h.innerText.trim() };
}"""
MENU_ITEMS = "[role=menu] [role=menuitemradio], [role=menu] [role=menuitemcheckbox]"
_list_locks: Dict[str, asyncio.Lock] = {}


def _clean(s: str) -> List[str]:
    return [l.strip() for l in re.sub(r"[-]", "", str(s or "")).split("\n") if l.strip()]


async def _signed_out(page: Any) -> bool:
    try:
        h = await page.wait_for_function(SIGNED, timeout=6000)
        return (await h.json_value()) == "out"
    except Exception:
        return False


async def _panel(page: Any) -> Dict[str, Any]:
    try:
        await page.wait_for_function(PANEL_READY, timeout=20000)
    except Exception:
        return {"error": "place_not_loaded"}
    r = await page.evaluate(PANEL)
    if r.get("error"):
        return r
    return {**r, "signed_out": await _signed_out(page)}


async def _click(h: Any) -> None:
    """A real click; then forced; then the element's own click()."""
    for kw in ({"timeout": 4000}, {"force": True, "timeout": 3000}):
        try:
            await h.click(**kw)
            return
        except Exception:
            pass
    await h.evaluate("(e) => e.click()")


async def _save_button(page: Any) -> Any:
    for h in await page.query_selector_all("[role=main] button"):
        try:
            v = await h.evaluate("(b) => (b.offsetParent || b.getClientRects().length) ? (b.getAttribute('data-value') || b.getAttribute('aria-label') || '').trim() : ''")
        except Exception:
            v = ""
        if MAPS_SAVE_BTN.match(v):
            return h
    return None


async def _menu(page: Any) -> Dict[str, Any]:
    btn = await _save_button(page)
    if not btn:
        return {"error": "save_button_not_found"}
    await _click(btn)
    try:
        await page.wait_for_selector(MENU_ITEMS, timeout=10000)
    except Exception:
        return {"error": "not_logged_in" if ("accounts.google.com" in page.url or await _signed_out(page)) else "save_menu_not_opened"}
    await asyncio.sleep(0.3)
    items = []
    for h in await page.query_selector_all(MENU_ITEMS):
        try:
            v = await h.evaluate("(e) => ({ text: e.innerText, checked: e.getAttribute('aria-checked') === 'true' })")
        except Exception:
            continue
        lines = _clean(v["text"])
        items.append({"h": h, "name": lines[0] if lines else "", "detail": " ".join(lines[1:]), "checked": v["checked"]})
    return {"items": items}


async def _close_menu(page: Any) -> None:
    for _ in range(3):
        if not await page.query_selector(MENU_ITEMS):
            break
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        await asyncio.sleep(0.4)


async def _save_place(page: Any, url: str, lst: str, action: str, wait_ms: int) -> Dict[str, Any]:
    out: Dict[str, Any] = {"url": url}
    try:
        p: Dict[str, Any] = {}
        for i in range(2):  # one retry covers a load that hung
            try:
                await page.goto(url, wait_until="commit", timeout=30000)
            except Exception:
                if i:
                    raise
                continue
            p = await _panel(page)
            if p.get("error") != "place_not_loaded":
                break
        if p.get("error"):
            return {**out, "status": "failed", "reason": p["error"], **({"candidates": p["candidates"]} if p.get("candidates") else {})}
        if p.get("signed_out"):
            return {**out, "name": p["name"], "status": "failed", "reason": "not_logged_in"}
        out["name"] = p["name"]
        if wait_ms:
            await asyncio.sleep(wait_ms / 1000)
        async with _list_locks.setdefault(lst, asyncio.Lock()):  # nobody else writes this list meanwhile
            m = await _menu(page)
            if m.get("error"):
                return {**out, "status": "failed", "reason": m["error"]}
            find = lambda items: next((i for i in items if i["name"] == lst), None) or next((i for i in items if i["name"].lower() == lst.lower()), None)  # noqa: E731
            it = find(m["items"])
            if not it:
                await _close_menu(page)
                return {**out, "status": "failed", "reason": "list_not_found", "lists": [i["name"] for i in m["items"]]}
            was = it["checked"]
            if action == "check" or (action == "save" and was) or (action == "remove" and not was):
                await _close_menu(page)
                st = ("in_list" if was else "not_in_list") if action == "check" else ("already_saved" if was else "not_in_list")
                return {**out, "status": st, "verified": True}
            await _click(it["h"])
            for _ in range(20):
                try:
                    flipped = await it["h"].evaluate("(e, want) => !e.isConnected || !e.closest('[role=menu]')?.getClientRects().length || e.getAttribute('aria-checked') === want", str(not was).lower())
                except Exception:
                    flipped = True
                if flipped:
                    break
                await asyncio.sleep(0.25)
            await asyncio.sleep(1.2)
            await _close_menu(page)
            m = await _menu(page)
            it = find(m.get("items") or [])
            await _close_menu(page)
            if not it:
                return {**out, "status": "failed", "reason": m.get("error") or "verify_failed"}
            ok = it["checked"] == (not was)
            return {**out, "status": ("saved" if action == "save" else "removed") if ok else "failed", **({} if ok else {"reason": "state_did_not_change"}), "verified": ok}
    except Exception as e:
        return {**out, "status": "failed", "reason": str(e).splitlines()[0][:200] if str(e) else type(e).__name__}


async def save_places(urls: List[str], list: str, action: str = "save", tabs: int = 3, wait_ms: int = 1500) -> Dict[str, Any]:  # noqa: A002 - web-reader's argument name
    first: Dict[str, int] = {}
    results: List[Any] = [None] * len(urls)
    queue = []
    for i, u in enumerate(urls):
        if u in first:
            results[i] = {"url": u, "status": "duplicate", "same_as": first[u] + 1}
        else:
            first[u] = i
            queue.append((u, i))
    stop: Dict[str, Optional[str]] = {"why": None}
    n = max(1, min(tabs, 4, len(queue) or 1))

    async def worker(w: int) -> None:
        async with tab() as page:
            try:  # a tab that is not in front still acts focused (menus)
                cdp = await page.context.new_cdp_session(page)
                await cdp.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
            except Exception:
                pass
            while queue:
                u, i = queue.pop(0)
                if stop["why"]:
                    results[i] = {"url": u, "status": "skipped", "reason": stop["why"]}
                    continue
                t0 = time.monotonic()
                r = await _save_place(page, u, list, action, wait_ms)
                results[i] = {**r, "tab": w + 1, "ms": int((time.monotonic() - t0) * 1000)}
                if r.get("reason") in ("not_logged_in", "list_not_found"):
                    stop["why"] = r["reason"]

    await asyncio.gather(*(worker(w) for w in range(n)))
    count: Dict[str, int] = {}
    for r in results:
        count[r["status"]] = count.get(r["status"], 0) + 1
    lists = next((r["lists"] for r in results if r.get("lists")), None)
    out: Dict[str, Any] = {"list": list, "action": action, "tabs": n, "browser": "scrapling/" + ("headless" if HEADLESS else "window"), "count": count,
                           "results": [{k: v for k, v in r.items() if k != "lists"} for r in results]}
    if stop["why"] == "not_logged_in":
        out["note"] = "공용 브라우저가 구글에 로그인돼 있지 않아요. login(\"https://accounts.google.com\")으로 사용자가 한 번 로그인한 뒤 다시 하세요(창 있는 PC에서)."
    if lists:
        out["lists_found"] = lists
    return out
