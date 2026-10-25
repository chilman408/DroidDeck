// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const { build } = require('./build-webcodecs-diagnostics');
const { chromium } = require('../runtime/preview-gateway/node_modules/playwright-core');

test('generated self-test separates decoding and canvas presentation without Android traffic', async () => {
  const html = build(); assert.equal(html, build());
  assert.ok(!/192\.168\.|\/api\/|\/sockets\/|localStorage|sessionStorage|fetch\(/.test(html));
  const server = http.createServer((_req, res) => { res.setHeader('Content-Type', 'text/html'); res.end(html); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const page = await browser.newPage(), errors = [], foreign = [];
    page.on('pageerror', error => errors.push(error.name));
    page.on('request', request => { if (new URL(request.url()).origin !== origin) foreign.push(request.url()); });
    await page.goto(origin, { waitUntil: 'domcontentloaded' });
    await page.locator('#run').click();
    await page.waitForFunction(() => !document.getElementById('run').disabled, undefined, { timeout: 45000 });
    const report = JSON.parse(await page.locator('#result').textContent());
    assert.equal(report.mainDecoderAvailable, true);
    assert.equal(report.results[0].workerDecoderAvailable, true);
    const decoded = report.results.find(row => row.preference === 'no-preference');
    assert.equal(decoded.decoded, true, JSON.stringify(report));
    for (const kind of ['2d', 'webgl', 'webgl2']) {
      assert.equal(decoded.presentation.find(row => row.kind === kind).patternVisible, true, JSON.stringify(report));
    }
    assert.ok(report.results.at(-1).done);
    assert.deepEqual(errors, []); assert.deepEqual(foreign, []);
    console.log(JSON.stringify({ generatedOnly: true, noPreferenceDecoded: true,
      presentation: decoded.presentation, externalRequests: 0, androidContentRead: false }));
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
});
