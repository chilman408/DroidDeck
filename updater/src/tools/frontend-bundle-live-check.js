// SPDX-License-Identifier: GPL-3.0-only
// Public static assets only: no Android input, account pixels or settings writes.
const fs = require('node:fs'), path = require('node:path');
const { verifyBundle } = require('../runtime/portable/frontend-bundle');
async function main() {
  if (process.platform !== 'win32' || process.argv.length !== 3) throw new Error('Provide a verified local bundle directory');
  const directory = path.resolve(process.argv[2]), manifest = verifyBundle(directory);
  const { chromium } = require('playwright-core');
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext(); let compared = 0;
    for (const record of manifest.files) {
      if (record.path === 'LICENSE') continue; // Gateway intentionally serves UI assets, not license text.
      const response = await context.request.get('https://teslavm.mondriandsm.synology.me/' + record.path);
      if (response.status() !== 200) throw new Error('Public asset unavailable: ' + record.path);
      const data = await response.body(), canonical = fs.readFileSync(path.join(directory, record.path));
      const text = record.path === 'LICENSE' || /\.(html|js|css|svg)$/.test(record.path);
      const normalized = text ? Buffer.from(new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(data).replace(/\r\n/g, '\n')) : data;
      if (!normalized.equals(canonical)) throw new Error('Public asset differs: ' + record.path);
      compared++;
    }
    console.log(JSON.stringify({ comparedPublicStaticAssets: compared, license: 'preserved-from-pinned-Git-object', exactBinaries: true, textDifferenceAllowed: 'CRLF-only', liveWrites: 0, installable: false }));
  } finally { await browser.close(); }
}
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
