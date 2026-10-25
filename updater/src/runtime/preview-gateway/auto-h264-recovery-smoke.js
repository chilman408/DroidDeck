// SPDX-License-Identifier: GPL-3.0-only
// An isolated browser exercises candidate assets; no Android input or preference writes.
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const { regions } = require('./live-capture-region-probe');
async function main() {
  if (process.argv.length < 3 || process.argv.length > 4 ||
    (process.argv[3] && !['--role', '--installed'].includes(process.argv[3]))) throw new Error('Frontend directory and optional --role/--installed required');
  const roleTrial = process.argv[3] === '--role';
  const installed = process.argv[3] === '--installed';
  const frontend = path.resolve(process.argv[2]);
  const origin = 'https://teslavm.mondriandsm.synology.me';
  const files = ['js/display/display-renderer-manager.js',
    'js/display/renderers/h264-webcodecs/h264WebCodecs.js',
    ...(roleTrial ? ['js/display/display-controller.js'] : [])];
  const { chromium } = require('playwright-core');
  const { PNG } = require('../waydroid-display/node_modules/pngjs');
  const browser = await chromium.launch({ executablePath: process.env.EDGE_PATH ||
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext();
    const created = await context.request.post(origin + '/api/displaySession', {
      headers: { Origin: origin, 'Content-Type': 'application/json' }, data: {}, timeout: 10000 });
    assert.equal(created.status(), 200); const session = await created.json();
    const reply = await context.request.get(origin + '/api/displayState', {
      headers: { 'X-Display-Session': session.token }, timeout: 10000 });
    assert.equal(reply.status(), 200); const state = await reply.json();
    assert.ok([state.width, state.height].every(n => Number.isInteger(n) && n >= 96 && n <= 1920));
    const page = await context.newPage();
    await page.setViewportSize({ width: state.width, height: state.height });
    const errors = [], blocked = []; let navigations = 0, pngRenderer = false, roleRequests = 0;
    page.on('pageerror', () => errors.push('page-error'));
    page.on('framenavigated', frame => { if (frame === page.mainFrame()) navigations++; });
    page.on('request', request => {
      if (new URL(request.url()).pathname.includes('/renderers/mjpeg/')) pngRenderer = true;
    });
    if (!installed) for (const file of files) await page.route(origin + '/' + file, route => route.fulfill({
      status: 200, contentType: 'application/javascript', body: fs.readFileSync(path.join(frontend, file)) }));
    await page.route(origin + '/api/**', async route => {
      const request = route.request(), endpoint = new URL(request.url()).pathname;
      if (roleTrial && endpoint === '/api/displayRole' && request.method() === 'POST' && ++roleRequests === 1) {
        await route.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"Display authority unavailable"}' });
        return;
      }
      if (request.method() !== 'GET' && !['/api/displaySession', '/api/displayRole'].includes(endpoint)) {
        blocked.push(endpoint); await route.abort();
      } else await route.continue();
    });
    await page.addInitScript(roleTrial => {
      localStorage.setItem('TeslaAndroid_browserH264Renderer_v1', roleTrial ? 'legacy' : 'auto');
      for (const key of ['DisplayRepository_isPrimaryDisplaySharedPreferencesKey',
        'flutter.DisplayRepository_isPrimaryDisplaySharedPreferencesKey']) localStorage.setItem(key, 'true');
      const NativeWorker = window.Worker;
      window.__webCodecsSmokeAttempts = 0;
      window.Worker = class extends NativeWorker {
        constructor(url, options) {
          if (String(url).includes('/h264-webcodecs/')) {
            window.__webCodecsSmokeAttempts++;
            throw new Error('Synthetic WebCodecs initialization failure');
          }
          super(url, options);
        }
      };
    }, roleTrial);
    await page.goto(origin + '/', { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForFunction(() => {
      const canvas = document.querySelector('#canvas');
      return canvas?.dataset.videoState === 'ready' && canvas.dataset.videoDecoder === 'legacy';
    }, undefined, { timeout: 45000 });
    await page.waitForTimeout(1500);
    const metadata = await page.evaluate(() => ({
      selected: window.TAHtml.browserRenderer.choice(), effective: window.TAHtml.browserRenderer.effectiveChoice(),
      fallback: sessionStorage.getItem('TeslaAndroid_autoH264Fallback_v1'),
      webCodecsAttempts: window.__webCodecsSmokeAttempts,
      frames: Number(document.querySelector('#canvas').dataset.videoFrames),
    }));
    assert.equal(metadata.selected, roleTrial ? 'legacy' : 'auto'); assert.equal(metadata.effective, 'legacy');
    assert.equal(metadata.fallback, null); assert.ok(metadata.frames > 0);
    assert.equal(metadata.webCodecsAttempts, 0, 'Automatic must not trial an unproven decoder');
    assert.equal(navigations, 1, 'The accepted default requires no recovery reload');
    if (roleTrial) assert.ok(roleRequests >= 2, 'Role registration must retry after initial503');
    assert.equal(pngRenderer, false); assert.deepEqual(errors, []); assert.deepEqual(blocked, []);
    const pixels = regions(PNG.sync.read(await page.locator('#canvas').screenshot()));
    assert.ok(pixels.center > .01, 'Recovered H264 picture must not be black');
    console.log(JSON.stringify({ ...metadata, installedAssets: installed, navigations, roleRequests, pngRenderer, pageErrorCount: errors.length,
      blockedWrites: blocked, nonblackFraction: pixels,
      scope: roleTrial ? 'Synthetic initial role503, real H264 and polling retry; no saved images/input/Android settings changes' :
        'Automatic defaults to real legacy H264 without trial/reload; no saved images/input/Android settings changes' }));
  } finally { await browser.close(); }
}
if (require.main === module) main().catch(() => {
  console.error('Automatic H264 recovery smoke failed; no raw browser data exported'); process.exitCode = 1;
});
