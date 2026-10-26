"""What is known per site: recipes (sites.json), user's notes, and a status board built from what
the reader saw (last block, how it was read) and the shared browser's cookies (login alive,
when it expires). read() attaches a site's note and a login-expiry warning to its results."""

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from scrapling.webreader.browser import ROOT

HERE = Path(__file__).resolve().parent
RECIPES: Dict[str, Any] = json.loads((HERE / "sites.json").read_text(encoding="utf-8"))
DATA = ROOT / "data"
NOTES_FILE = DATA / "site-notes.json"
STATUS_FILE = DATA / "site-status.json"
# Cookie names that usually carry a login (session, auth tokens); the rest are visits and ads.
LOGIN_COOKIE = re.compile(r"sess|auth|token|login|^sid$|_sid$|^uid$|remember|NID_AUT|NID_SES|SAPISID|PSID|_kawlt|_karmt|access|refresh|jwt", re.I)
EXPIRY_WARN_DAYS = 3


def site_of(host_or_url: str) -> str:
    """Registrable domain, roughly: last two labels, three for co.jp-style suffixes."""
    host = urlsplit(host_or_url).hostname if "://" in host_or_url else host_or_url
    p = (host or "").lstrip(".").lower().removeprefix("www.").split(".")
    two = len(p) > 2 and p[-2] in ("co", "or", "ne", "ac", "go", "com", "net", "org") and len(p[-1]) == 2
    return ".".join(p[-3:] if two else p[-2:])


def recipe(host_or_url: str) -> Dict[str, Any]:
    return RECIPES["sites"].get(site_of(host_or_url), {})


def editions(host_or_url: str) -> List[str]:
    return recipe(host_or_url).get("lang_editions", [])


def synonyms() -> List[List[str]]:
    return RECIPES["section_synonyms"]


def _load(f: Path) -> Dict[str, Any]:
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(f: Path, d: Dict[str, Any]) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)


def note(host_or_url: str) -> Optional[str]:
    site = site_of(host_or_url)
    parts = [x for x in (recipe(site).get("note"), _load(NOTES_FILE).get(site)) if x]
    return " / ".join(parts) or None


def set_note(site: str, text: Optional[str]) -> Dict[str, Any]:
    d = _load(NOTES_FILE)
    key = site_of(site)
    if text:
        d[key] = text
    else:
        d.pop(key, None)
    _save(NOTES_FILE, d)
    return {"site": key, "note": note(key)}


def record(url: str, ok: bool, via: Optional[str] = None, problem: Optional[str] = None) -> None:
    """Called by the reader for every page: keeps the last success and the last block per site."""
    d = _load(STATUS_FILE)
    e = d.setdefault(site_of(url), {})
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    if ok:
        e["last_ok"] = {"at": now, "via": via, "url": url[:200]}
    if problem:
        e["last_block"] = {"at": now, "reason": problem, "url": url[:200]}
        e["blocks"] = e.get("blocks", 0) + 1
    _save(STATUS_FILE, d)


def login_state(cookies: List[Dict[str, Any]]) -> Dict[str, Any]:
    """From one site's cookies: is there a login cookie, and when does the first one expire."""
    login = [c for c in cookies if LOGIN_COOKIE.search(c.get("name", ""))]
    out: Dict[str, Any] = {"cookies": len(cookies), "login_cookies": len(login)}
    exp = [c["expires"] for c in login if c.get("expires", -1) and c.get("expires", -1) > 0]
    if login and len(exp) < len(login):
        out["session_cookie"] = True  # ends when the browser closes; the profile keeps it while the window stays
    if exp:
        first = min(exp)
        days = (first - time.time()) / 86400
        out["earliest_expiry"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(first))
        out["expires_in_days"] = round(days, 1)
        if days < EXPIRY_WARN_DAYS:
            out["warn"] = "로그인이 곧 풀려요" if days > 0 else "로그인 쿠키가 만료됐어요"
    return out


def mark_login(url: str) -> None:
    """user logged in to this site (login tool): its login cookies are then watched for expiry."""
    d = _load(STATUS_FILE)
    d.setdefault(site_of(url), {})["logged_in"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _save(STATUS_FILE, d)


def login_warning(cookies: List[Dict[str, Any]], url: str) -> Optional[Dict[str, Any]]:
    """Only for sites user logged in to: visitor cookies elsewhere look like logins and would warn falsely."""
    if not _load(STATUS_FILE).get(site_of(url), {}).get("logged_in"):
        return None
    s = login_state(cookies)
    return s if s.get("warn") else None


def board(all_cookies: List[Dict[str, Any]], only: Optional[str] = None, http_blocked: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    status = _load(STATUS_FILE)
    by_site: Dict[str, List[Dict[str, Any]]] = {}
    for c in all_cookies:
        by_site.setdefault(site_of(c.get("domain", "")), []).append(c)
    sites = set(status) | {s for s, cs in by_site.items() if any(LOGIN_COOKIE.search(c.get("name", "")) for c in cs)}
    if only:
        sites = {site_of(only)}
    rows = []
    for s in sorted(sites):
        row: Dict[str, Any] = {"site": s, "login": login_state(by_site.get(s, [])), **status.get(s, {})}
        if not row.get("logged_in"):
            row["login"].pop("warn", None)  # visitor cookies, not a login user made
        blocked = {k: v for k, v in (http_blocked or {}).items() if k.split("/")[0].endswith(s)}
        if blocked:
            row["http_blocked"] = blocked  # these go straight to the browser for a day
        n = note(s)
        if n:
            row["note"] = n
        rows.append(row)
    return rows
