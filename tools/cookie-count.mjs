// node ck.mjs <profileDir> [close] : prints cookie count / login-ish domains from the live browser; "close" closes it gracefully.
import fs from 'node:fs';
const dir = process.argv[2];
const [port, wsPath] = fs.readFileSync(dir + '/DevToolsActivePort', 'utf8').split(/\r?\n/);
const ws = new WebSocket(`ws://127.0.0.1:${port}${wsPath}`);
await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
let id = 0; const pend = {};
ws.onmessage = (e) => { const m = JSON.parse(e.data); if (pend[m.id]) { pend[m.id](m.result || m.error); delete pend[m.id]; } };
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pend[i] = r; ws.send(JSON.stringify({ id: i, method, params })); });
const v = await send('Browser.getVersion');
const { cookies = [] } = await send('Storage.getCookies');
const doms = {};
for (const c of cookies) { const d = c.domain.replace(/^\./, '').split('.').slice(-2).join('.'); doms[d] = (doms[d] || 0) + 1; }
console.log('cookies', cookies.length, 'domains', Object.keys(doms).length, 'top', Object.entries(doms).sort((a, b) => b[1] - a[1]).slice(0, 12).map(([d, n]) => d + ':' + n).join(' '));
if (process.argv[3] === 'close') { await send('Browser.close'); console.log('closed'); }
process.exit(0);
