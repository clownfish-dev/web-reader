// Registers web-reader as a user-scope MCP server for Claude Code on this PC:
// adds mcpServers["web-reader"] at the top level of ~/.claude.json so every
// session and project can use it. Backs the file up first, keeps all other
// entries, and checks the result parses before and after writing.
//   node register-global.js            (headless only: never opens a window)
//   node register-global.js --windowed (allow the windowed browser)
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const file = path.join(os.homedir(), '.claude.json');
const entry = {
  type: 'stdio',
  command: process.execPath,
  args: [path.join(HERE, 'server.mjs')],
  env: process.argv.includes('--windowed') ? {} : { WEB_READER_HEADLESS_ONLY: '1' },
};

if (!fs.existsSync(entry.args[0])) { console.error('server.mjs not found:', entry.args[0]); process.exit(1); }

let cfg = {};
if (fs.existsSync(file)) {
  const raw = fs.readFileSync(file, 'utf8');
  try { cfg = JSON.parse(raw); } catch (e) { console.error('~/.claude.json is not valid JSON, not touched:', e.message); process.exit(1); }
  const d = new Date(), pad = n => String(n).padStart(2, '0');
  const stamp = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
  const backup = `${file}.bak-${stamp}`;
  fs.copyFileSync(file, backup);
  console.log('backup:', backup);
}

cfg.mcpServers = cfg.mcpServers || {};
const before = JSON.stringify(cfg.mcpServers['web-reader'] || null);
cfg.mcpServers['web-reader'] = entry;
const out = JSON.stringify(cfg, null, 2);
JSON.parse(out);
const tmp = file + '.tmp-web-reader';
fs.writeFileSync(tmp, out);
JSON.parse(fs.readFileSync(tmp, 'utf8'));
fs.renameSync(tmp, file);

const check = JSON.parse(fs.readFileSync(file, 'utf8'));
console.log(before === JSON.stringify(entry) ? 'already registered (unchanged):' : 'registered:', file);
console.log('user-scope servers:', Object.keys(check.mcpServers).join(', '));
console.log('web-reader ->', JSON.stringify(check.mcpServers['web-reader']));
