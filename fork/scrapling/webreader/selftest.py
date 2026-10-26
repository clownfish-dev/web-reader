"""Checks before calling a build done (run against the running shared server):
    python -I -m scrapling.webreader.selftest            tool list vs README + bot traces; exit 1 on a problem
    python -I -m scrapling.webreader.selftest --write    rewrite README's tool list from the server
- tools: every tool has a description and appears in README's generated list, and the list names
  no tool that is gone.
- bot traces: what a page sees in Claude's tabs (webdriver, headless names, automation globals,
  plugin/language/permission inconsistencies), the same kinds of checks as web-reader --botcheck.
  The page is served from this PC (no outside site), so the request header can be compared with the
  page's and a worker's user agent; a mismatch is what sites look for (measured 2026-10-21).
- code: no call that plants something lasting in pages (init scripts, exposed functions); they
  leave globals bot checks look for. Run after development, before deploying.
WARN lines are reported but do not fail the run.
"""

import asyncio
import http.server
import json
import re
import sys
import threading
from pathlib import Path

from scrapling.webreader.browser import ROOT
from scrapling.webreader.proxy import HeldSession
from scrapling.webreader.shim import _connect

README = ROOT / "README.md"
START, END = "<!-- tools:start -->", "<!-- tools:end -->"

PROBE = """async () => {
  const keys = Object.getOwnPropertyNames(window).filter((k) => /playwright|puppeteer|patchright|selenium|webdriver|cdc_|__pw|__nightmare|domAutomation/i.test(k));
  let worker = 'none';
  try {
    const w = new Worker(URL.createObjectURL(new Blob(['postMessage(navigator.userAgent)'])));
    worker = await new Promise((r) => { w.onmessage = (e) => r(e.data); setTimeout(() => r('timeout'), 3000); });
    w.terminate();
  } catch (e) { worker = 'error: ' + e.message; }
  const h = document.getElementById('h');
  return {
    webdriver: navigator.webdriver,
    ua: navigator.userAgent,
    worker_ua: worker,
    header_ua: h ? JSON.parse(h.textContent) : null,
    brands: (navigator.userAgentData && navigator.userAgentData.brands || []).map((b) => b.brand),
    plugins: navigator.plugins.length,
    languages: navigator.languages,
    chrome: typeof window.chrome,
    automation_globals: keys,
    notification: typeof Notification === 'undefined' ? 'none' : Notification.permission,
    outer: [outerWidth, outerHeight],
    inner: [innerWidth, innerHeight],
  };
}"""


def _body(res):
    t = res.content[0].text if res.content else ""
    if "<untrusted_web_content" in t:
        t = t.split("\n", 2)[2].rsplit("\n", 1)[0]
    return json.loads(t)


async def tools_check(remote: HeldSession, write: bool) -> list:
    tools = (await (await remote.get()).list_tools()).tools
    problems = [f"{t.name}: no description" for t in tools if not (t.description or "").strip()]
    lines = [f"- `{t.name}`: {(t.description or '').strip().splitlines()[0]}" for t in sorted(tools, key=lambda t: t.name)]
    block = f"{START}\n" + "\n".join(lines) + f"\n{END}"
    text = README.read_text(encoding="utf-8")
    if write:
        text = re.sub(re.escape(START) + r"[\s\S]*?" + re.escape(END), block, text) if START in text else text + "\n## 도구 목록 (자동 생성)\n" + block + "\n"
        README.write_text(text, encoding="utf-8")
    current = re.search(re.escape(START) + r"([\s\S]*?)" + re.escape(END), text)
    listed = set(re.findall(r"^- `([a-z_]+)`", current.group(1), re.M)) if current else set()
    names = {t.name for t in tools}
    problems += [f"README lacks tool {n}" for n in sorted(names - listed)]
    problems += [f"README lists missing tool {n}" for n in sorted(listed - names)]
    print(f"tools: {len(tools)}, README list {'ok' if not problems else 'MISMATCH'}")
    return problems


class _EchoUA(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = f'<!doctype html><title>selftest</title><pre id="h">{json.dumps(self.headers.get("User-Agent", ""))}</pre>'.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


PLANT = re.compile(r"\b(add_init_script|addInitScript|expose_function|exposeFunction|expose_binding|exposeBinding)\s*\(")


def code_check() -> list:
    pkg = Path(__file__).resolve().parent
    problems = []
    for f in sorted([*pkg.glob("*.py"), *pkg.glob("*.js")]):
        if f.name.startswith("selftest"):  # this file names the calls it looks for
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if PLANT.search(line) and not line.lstrip().startswith(("#", "//")):
                problems.append(f"code {f.name}:{i} plants into pages: {line.strip()[:100]}")
    print(f"code: {'ok' if not problems else 'PLANTING CALLS'}")
    return problems


async def bot_check(remote: HeldSession) -> tuple:
    """Returns (problems, warns)."""
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _EchoUA)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        o = _body(await remote.call("browser_open", {"url": f"http://127.0.0.1:{srv.server_address[1]}/"}))
        sid = o["session_id"]
        try:
            res = await remote.call("browser_evaluate", {"session_id": sid, "expression": PROBE, "isolated_context": False})
            p = json.loads(res.content[0].text)
        finally:
            await remote.call("close_session", {"session_id": sid})
    finally:
        srv.shutdown()
    problems, warns = [], []
    if p["webdriver"]:
        problems.append("navigator.webdriver is true")
    if re.search(r"headless", p["ua"], re.I) or any("headless" in b.lower() for b in p["brands"]):
        problems.append("headless name in user agent / brands")
    for k in ("worker_ua", "header_ua"):
        if p[k] is None or re.search(r"headless", p[k] or "", re.I) or p[k] != p["ua"]:
            problems.append(f"{k} differs from page user agent: {p[k]}")
    # Real Chrome lists "Google Chrome" (Edge "Microsoft Edge") next to Chromium; Chromium alone is a weak sign.
    if not any(b in ("Google Chrome", "Microsoft Edge") for b in p["brands"]):
        warns.append(f"brands are Chromium only: {p['brands']}")
    # A real window has toolbars, so outer > inner; equal sizes are typical of a window-less browser.
    if p["outer"] == p["inner"]:
        warns.append(f"window outer == inner {p['outer']} (no toolbar, window-less look)")
    if p["automation_globals"]:
        problems.append(f"automation globals: {p['automation_globals']}")
    if p["plugins"] == 0:
        problems.append("no plugins (headless-like)")
    if not p["languages"]:
        problems.append("no languages")
    if p["chrome"] != "object":
        problems.append("window.chrome missing")
    print("bot traces:", json.dumps({k: p[k] for k in ("webdriver", "ua", "worker_ua", "header_ua", "brands", "plugins", "languages", "chrome", "automation_globals", "outer", "inner")}, ensure_ascii=False))
    return problems, warns


async def main() -> int:
    remote = HeldSession(_connect)
    try:
        problems = code_check() + await tools_check(remote, "--write" in sys.argv)
        bot_problems, warns = await bot_check(remote)
        problems += bot_problems
    finally:
        await remote.close()
    for w in warns:
        print("WARN:", w)
    for p in problems:
        print("PROBLEM:", p)
    print("PASS" if not problems else "FAIL")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(main()))
