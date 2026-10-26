// Copy web-reader's logins (cookies + localStorage) into a Playwright state file
// that the web-reader server loads into its browsers and HTTP requests.
//   node export-login.mjs
// Reads both web-reader browsers (headless profile and the shared window's profile,
// which holds most logins; it wins on duplicates) by attaching to their debugging
// ports, so they stay in use. A browser that is not running is skipped.
// Writes data\login-state.json (secret: never print or share its values) and prints
// only counts. Restart the Scrapling server (or a new Claude session) to use it.
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
// web-reader sits next to this folder on every PC.
const WR = process.env.OLD_WEB_READER || path.join(path.dirname(HERE), 'web-reader');
const { chromium } = createRequire(path.join(WR, 'package.json'))('playwright-core');
const OUT = path.join(HERE, 'data', 'login-state.json');

async function read(name) {
  let port, wsPath;
  try { [port, wsPath] = fs.readFileSync(path.join(WR, name, 'DevToolsActivePort'), 'utf8').split(/\r?\n/); } catch {}
  if (!port) return { profile: name, skipped: 'not running' };
  try {
    // Never close() this browser, it is web-reader's; the socket drops when the process exits.
    const browser = await chromium.connectOverCDP(`ws://127.0.0.1:${port}${wsPath}`, { timeout: 15000 });
    return { profile: name, state: await browser.contexts()[0].storageState() };
  } catch (e) {
    return { profile: name, skipped: 'not running (' + String(e.message).split('\n')[0].slice(0, 80) + ')' };
  }
}

const parts = [await read('profile'), await read('profile-headed')];
const cookies = new Map();
const origins = new Map();
for (const p of parts) {
  if (!p.state) continue;
  for (const c of p.state.cookies) cookies.set(`${c.name}|${c.domain}|${c.path}`, c);
  for (const o of p.state.origins) origins.set(o.origin, o);
}
const report = parts.map((p) => (p.state ? { profile: p.profile, cookies: p.state.cookies.length } : { profile: p.profile, skipped: p.skipped }));
if (!cookies.size) { console.log(JSON.stringify({ ok: false, error: 'no web-reader browser running (read any page with web-reader first)', profiles: report })); process.exit(1); }
fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, JSON.stringify({ cookies: [...cookies.values()], origins: [...origins.values()] }));
const sites = new Set([...cookies.values()].map((c) => c.domain.replace(/^\./, '').split('.').slice(-2).join('.')));
console.log(JSON.stringify({ ok: true, cookies: cookies.size, sites: sites.size, local_storage_origins: origins.size, profiles: report, file: OUT }));
process.exit(0);
