"""Smaller fork tools around reading: remembered site APIs (call again without the browser),
sitemap URL lists, and page-change watches. The server registers thin tool wrappers."""

import gzip
import hashlib
import json
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from scrapling.webreader import sites
from scrapling.webreader.browser import ROOT, SHARED
from scrapling.webreader.read import READER, _decode

APIS_FILE = ROOT / "data" / "site-apis.json"
WATCH_FILE = ROOT / "data" / "watches.json"
# Request headers worth replaying; cookies come from the browser at call time, never stored.
KEEP_HEADERS = re.compile(r"^(accept|accept-language|content-type|x-requested-with|x-(?!.*(auth|token|csrf|xsrf|api-key)).*)$", re.I)


def _load(f) -> Dict[str, Any]:
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(f, d) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)


# ---------- 1. remembered site APIs (Unbrowse's idea, kept on this PC) ----------
def remember_api(record: Any, name: str, note: Optional[str] = None) -> Dict[str, Any]:
    """Save one recorded request (from browser_network_requests) as a named call for its site."""
    parts = urlsplit(record.url)
    body = record.meta.get("request_body")
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8", "replace")
    entry = {
        "method": record.method or "GET",
        "url": urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")),
        "params": dict(parse_qsl(parts.query, keep_blank_values=True)),
        "headers": {k: v for k, v in (record.request_headers or {}).items() if KEEP_HEADERS.match(k)},
        "body": body if body and len(body) < 20000 else None,
        "saved": time.strftime("%Y-%m-%d %H:%M"),
        **({"note": note} if note else {}),
    }
    d = _load(APIS_FILE)
    d.setdefault(sites.site_of(record.url), {})[name] = entry
    _save(APIS_FILE, d)
    return {"site": sites.site_of(record.url), "name": name, **{k: entry[k] for k in ("method", "url", "params")}}


def list_apis(site: Optional[str] = None) -> Dict[str, Any]:
    d = _load(APIS_FILE)
    if site:
        d = {sites.site_of(site): d.get(sites.site_of(site), {})}
    return {s: {n: {k: e.get(k) for k in ("method", "url", "params", "note", "saved")} for n, e in calls.items()} for s, calls in d.items()}


async def call_api(site: str, name: str, params: Optional[Dict[str, Any]] = None, body: Optional[Any] = None, max_chars: int = 50000) -> Dict[str, Any]:
    """Replay a remembered call over HTTP (Chrome TLS) with the browser's current cookies and user agent."""
    e = _load(APIS_FILE).get(sites.site_of(site), {}).get(name)
    if not e:
        raise ValueError(f"{site}에 '{name}' API가 없어요. api_list로 확인하세요.")
    q = {**e.get("params", {}), **{k: str(v) for k, v in (params or {}).items()}}
    url = e["url"] + ("?" + urlencode(q) if q else "")
    cookies, ua = await READER._cookie_header_parts(url)
    origin = "{0.scheme}://{0.netloc}/".format(urlsplit(e["url"]))
    headers = {**e.get("headers", {}), "User-Agent": ua, "Referer": origin}
    client = await READER._http_client()
    kwargs: Dict[str, Any] = {"cookies": cookies or None, "headers": headers}
    payload = body if body is not None else e.get("body")
    if e["method"] != "GET" and payload is not None:
        if isinstance(payload, (dict, list)):
            kwargs["json"] = payload
        else:
            kwargs["data"] = payload
    t0 = time.monotonic()
    resp = await getattr(client, e["method"].lower())(url, **kwargs)
    text = _decode(resp)
    out: Dict[str, Any] = {"status": resp.status, "url": url, "ms": int((time.monotonic() - t0) * 1000), "chars": len(text)}
    try:
        out["json"] = json.loads(text)
        if len(text) > max_chars:
            out["json_note"] = f"{len(text)}자예요. 필요한 부분만 쓰세요."
    except ValueError:
        out["body"] = text[:max_chars]
    if resp.status in (401, 403):
        out["next"] = "로그인이 풀렸을 수 있어요: site_status로 확인, login(url)"
    return out


# ---------- 11. sitemap URL list ----------
async def site_map(url: str, search: Optional[str] = None, limit: int = 200) -> Dict[str, Any]:
    """URLs a site lists in its sitemaps (robots.txt Sitemap lines, else /sitemap.xml); index files followed (up to 20)."""
    client = await READER._http_client()
    base = "{0.scheme}://{0.netloc}".format(urlsplit(url))
    t0 = time.monotonic()
    queue: List[str] = []
    try:
        r = await client.get(base + "/robots.txt")
        if r.status == 200:
            queue = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", _decode(r))
    except Exception:
        pass
    if not queue:
        queue = [base + "/sitemap.xml", base + "/sitemap_index.xml"]
    seen, found, fetched, failed = set(), [], 0, []
    while queue and fetched < 20 and len(found) < 50000:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        try:
            r = await client.get(sm)
        except Exception as e:
            failed.append({"sitemap": sm, "error": str(e).splitlines()[0][:100]})
            continue
        fetched += 1
        if r.status != 200:
            failed.append({"sitemap": sm, "status": r.status})
            continue
        raw = r.body or b""
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        xml = raw.decode("utf-8", "replace")
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
        if "<sitemapindex" in xml:
            queue.extend(locs)
        else:
            found.extend(locs)
    if search:
        s = search.lower()
        found = [u for u in found if s in u.lower()]
    return {"site": base, "sitemaps_read": fetched, "count": len(found), "urls": found[:limit], **({"failed": failed} if failed else {}),
            "ms": int((time.monotonic() - t0) * 1000), **({"more": len(found) - limit} if len(found) > limit else {})}


# ---------- 12. page-change watch (tool only; scheduling is left to whoever calls it) ----------
async def watch_add(url: str, name: Optional[str], section: Optional[str], selector: Optional[str]) -> Dict[str, Any]:
    r = await READER.read(url, {"section": section, "selector": selector, "cache": False, "max_chars": 20000} if (section or selector) else {"cache": False, "max_chars": 20000}, annotate=False)
    text = _watch_text(r)
    d = _load(WATCH_FILE)
    key = name or url
    d[key] = {"url": url, "section": section, "selector": selector, "text": text, "hash": hashlib.sha1(text.encode()).hexdigest(), "since": time.strftime("%Y-%m-%d %H:%M"), "checked": time.strftime("%Y-%m-%d %H:%M")}
    _save(WATCH_FILE, d)
    return {"watch": key, "chars": len(text)}


def _watch_text(r: Dict[str, Any]) -> str:
    if r.get("sections"):
        return "\n".join(s["text"] for s in r["sections"])
    return r.get("text") or ""


async def watch_check(name: Optional[str] = None) -> List[Dict[str, Any]]:
    d = _load(WATCH_FILE)
    out = []
    for key, w in d.items():
        if name and key != name:
            continue
        opts: Dict[str, Any] = {"cache": False, "max_chars": 20000}
        if w.get("section"):
            opts["section"] = w["section"]
        if w.get("selector"):
            opts["selector"] = w["selector"]
        try:
            r = await READER.read(w["url"], opts, annotate=False)
        except Exception as e:
            out.append({"watch": key, "error": str(e).splitlines()[0][:200]})
            continue
        text = _watch_text(r)
        h = hashlib.sha1(text.encode()).hexdigest()
        row: Dict[str, Any] = {"watch": key, "url": w["url"], "changed": h != w["hash"], "since": w["since"]}
        if h != w["hash"]:
            old, new = w["text"].split("\n"), text.split("\n")
            row["added"] = [x for x in new if x.strip() and x not in old][:20]
            row["removed"] = [x for x in old if x.strip() and x not in new][:20]
            w.update(text=text, hash=h, since=time.strftime("%Y-%m-%d %H:%M"))
        w["checked"] = time.strftime("%Y-%m-%d %H:%M")
        out.append(row)
    _save(WATCH_FILE, d)
    return out


def watch_remove(name: str) -> bool:
    d = _load(WATCH_FILE)
    ok = d.pop(name, None) is not None
    _save(WATCH_FILE, d)
    return ok
