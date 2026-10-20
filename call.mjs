// Call one web-reader tool from the command line (for testing without Claude):
//   node call.mjs <tool> '<json args>' [out-prefix]
// Text goes to stdout; returned images are saved as <out-prefix>-N.jpg|png.
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const [tool, json = '{}', prefix = path.join(HERE, 'data', 'call-out')] = process.argv.slice(2);
const client = new Client({ name: 'call', version: '1' });
await client.connect(new StdioClientTransport({ command: process.execPath, args: [path.join(HERE, 'server.mjs')], env: process.env, stderr: 'inherit' }));
if (!tool || tool === 'list') {
  for (const t of (await client.listTools()).tools) console.log(t.name);
} else {
  const t0 = Date.now();
  const r = await client.callTool({ name: tool, arguments: JSON.parse(json) }, undefined, { timeout: 600000 });
  let n = 0;
  for (const c of r.content) {
    if (c.type === 'text') console.log(c.text);
    else if (c.type === 'image') { fs.mkdirSync(path.dirname(prefix), { recursive: true }); const f = `${prefix}-${++n}.${c.mimeType.includes('png') ? 'png' : 'jpg'}`; fs.writeFileSync(f, Buffer.from(c.data, 'base64')); console.log(`[image saved: ${f}]`); }
  }
  console.log(`[${tool} ${Date.now() - t0}ms${r.isError ? ' ERROR' : ''}]`);
}
await client.close();
process.exit(0);
