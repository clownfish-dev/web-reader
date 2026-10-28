// Registers web-reader (Scrapling fork, fork\scrapling\webreader) as the MCP server "web-reader" for Claude Code on this PC,
// replacing web-reader 0.9.4's entry and the interim "scrapling" entry:
// user scope in ~/.claude.json, and also in a project .mcp.json when given (where web-reader is
// registered for a project). Backs up each file first, keeps every other entry, checks the JSON.
// Claude starts the stdio shim; the shim starts the one shared server when it isn't running.
//   node register.mjs                          (PC with a desktop: shared window)
//   node register.mjs --headless               (no desktop, e.g. Windows session 0)
//   node register.mjs --headless --mcp-json <project>\.mcp.json
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
const entry = {
  type: 'stdio',
  command: path.join(HERE, '.venv', 'Scripts', 'python.exe'),
  args: ['-I', '-m', 'scrapling.webreader.shim'],
  env: { PLAYWRIGHT_BROWSERS_PATH: path.join(HERE, 'browsers'), ...(argv.includes('--headless') ? { WEB_READER_HEADLESS: '1' } : {}) },
};
if (!fs.existsSync(entry.command)) { console.error('not found:', entry.command); process.exit(1); }

const d = new Date(), pad = (n) => String(n).padStart(2, '0');
const stamp = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
function add(file) {
  const cfg = fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf8')) : {};
  if (fs.existsSync(file)) fs.copyFileSync(file, `${file}.bak-${stamp}`);
  cfg.mcpServers = cfg.mcpServers || {};
  delete cfg.mcpServers.scrapling;
  cfg.mcpServers['web-reader'] = entry;
  const out = JSON.stringify(cfg, null, 2);
  JSON.parse(out);
  const tmp = file + '.tmp-scrapling';
  fs.writeFileSync(tmp, out);
  JSON.parse(fs.readFileSync(tmp, 'utf8'));
  fs.renameSync(tmp, file);
  console.log(`${file}: ${Object.keys(JSON.parse(fs.readFileSync(file, 'utf8')).mcpServers).join(', ')} (backup .bak-${stamp})`);
}
add(path.join(os.homedir(), '.claude.json'));
const i = argv.indexOf('--mcp-json');
if (i >= 0 && argv[i + 1]) add(argv[i + 1]);
console.log('Done. Open a new Claude session to see the web-reader tools.');
