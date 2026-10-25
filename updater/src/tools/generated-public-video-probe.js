// SPDX-License-Identifier: GPL-3.0-only
// Run only with the generated fixture or launcher visible, never paid media.
const assert = require('node:assert/strict');
const { chromium } = require('playwright-core');
const { PNG } = require('pngjs');
const fs = require('node:fs/promises'), crypto = require('node:crypto');
const { generatedVideoPixels, generatedMotion } = require('./generated-video-pixels');
const { unobstructed, transportCounters, regions } = require('../runtime/preview-gateway/live-capture-region-probe');
const origin = 'https://teslavm.mondriandsm.synology.me';

async function main() {
  assert.ok(process.argv.length === 2 || (process.argv.length === 3 && ['--launcher-only', '--legacy-candidate'].includes(process.argv[2])), 'Only fixed generated or launcher probe supported');
  assert.equal(process.env.SMOKE_SCREENSHOT_DIR, undefined, 'Generated probe never saves screenshots');
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext();
    const response = await context.request.post(origin + '/api/displaySession', { headers: { Origin: origin }, data: {} });
    assert.equal(response.status(), 200);
    const session = await response.json();
    const stateResponse = await context.request.get(origin + '/api/displayState', { headers: { 'X-Display-Session': session.token } });
    assert.equal(stateResponse.status(), 200);
    const state = await stateResponse.json();
    const page = await context.newPage();
    if (process.argv[2] === '--legacy-candidate') {
      const directory = 'C:/Users/Hilman/Documents/Codex/.codex-runner-temp/frontend-legacy-backpressure-candidate-20261011p2';
      for (const [name, expected] of [
        ['h264Brodway.js', 'a42d5e5a1b15383c3d96a40fc14d63d03dd4f789769ef58eb628c6ad7f58b1d6'],
        ['h264BrodwayWorker.js', 'f34045ad9d0c10aed19bb9c5d2e621dd96779d56c74e5040e338a09409acb280'],
      ]) {
        const body = await fs.readFile(directory + '/js/display/renderers/h264-broadway/' + name);
        assert.equal(crypto.createHash('sha256').update(body).digest('hex'), expected);
        await page.route(origin + '/js/display/renderers/h264-broadway/' + name + '*', route =>
          route.fulfill({ status: 200, contentType: 'text/javascript', headers: { 'Cache-Control': 'no-store' }, body }));
      }
    }
    const transport = transportCounters();
    page.on('websocket', socket => {
      if (new URL(socket.url()).pathname === '/sockets/display') socket.on('framereceived', frame => transport.receive(frame.payload));
    });
    await page.addInitScript(() => {
      for (const key of ['DisplayRepository_isPrimaryDisplaySharedPreferencesKey', 'flutter.DisplayRepository_isPrimaryDisplaySharedPreferencesKey']) {
        localStorage.setItem(key, 'true');
      }
    });
    await page.route(origin + '/api/**', async route => {
      const request = route.request(), endpoint = new URL(request.url()).pathname;
      if (request.method() !== 'GET' && !['/api/displaySession', '/api/displayRole'].includes(endpoint)) await route.abort();
      else await route.continue();
    });
    await page.setViewportSize({ width: state.width, height: state.height });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin, { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForFunction(() => Number(document.querySelector('#canvas')?.dataset.videoFrames) > 0,
      undefined, { timeout: 30000 });
    assert.equal(await page.locator('#canvas').getAttribute('data-video-decoder'), 'legacy');
    async function sample() {
      const elements = await page.locator('#canvas').evaluate(canvas => {
        const bounds = canvas.getBoundingClientRect();
        return document.elementsFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2)
          .slice(0, 8).map(element => ({ tag: element.tagName, id: element.id }));
      });
      assert.ok(unobstructed(elements), 'Browser dialog obscures video; not GPU capture evidence');
      assert.equal(new URL(page.url()).searchParams.get('video'), null, 'PNG fallback forbidden');
      return PNG.sync.read(await page.locator('#canvas').screenshot());
    }
    const initial = await sample();
    if (process.argv[2] === '--launcher-only') {
      const nonblack = regions(initial).whole;
      assert.ok(nonblack > 0.01, 'Restored launcher picture is black');
      console.log(JSON.stringify({ unobstructedLauncher: true, nonblack, legacyH264: true, savedPixels: false, globalSettingsWrites: false }));
      return;
    }
    console.log(JSON.stringify({ generatedVideoProbeReady: true, unobstructed: true }));
    const signatures = new Set(), ratios = [], allRatios = [];
    let previousColorFrame, motionSamples = 0, maximumChangedRatio = 0, maximumMeanDifference = 0;
    let maximumWorkerPackets = 0, maximumBrowserFrames = 0;
    const deadline = performance.now() + 55000;
    while (performance.now() < deadline) {
      const pixels = await sample(), facts = generatedVideoPixels(pixels);
      if (process.argv[2] === '--legacy-candidate') {
        const queue = await page.evaluate(() => ({ ready: legacyWorkerReady, failed: legacyFailed,
          worker: legacyInFlight.size, browser: legacyQueue.length }));
        assert.equal(queue.failed, false); assert.equal(queue.ready, true);
        assert.ok(queue.worker <= 2 && queue.browser <= 120, 'Encoded queue exceeded its bounds');
        maximumWorkerPackets = Math.max(maximumWorkerPackets, queue.worker);
        maximumBrowserFrames = Math.max(maximumBrowserFrames, queue.browser);
      }
      allRatios.push(facts.colorfulRatio);
      if (facts.colorfulRatio > 0.2) {
        signatures.add(facts.signature); ratios.push(facts.colorfulRatio);
        if (previousColorFrame) {
          const motion = generatedMotion(previousColorFrame, pixels);
          if (motion.moving) motionSamples++;
          maximumChangedRatio = Math.max(maximumChangedRatio, motion.changedRatio);
          maximumMeanDifference = Math.max(maximumMeanDifference, motion.meanDifference);
        }
        previousColorFrame = pixels;
      }
      await page.waitForTimeout(150);
    }
    console.log(JSON.stringify({ generatedColorSamples: ratios.length, distinctGeneratedFrames: signatures.size,
      minimumColorfulRatio: ratios.length ? Math.min(...ratios) : null,
      maximumObservedColorfulRatio: Math.max(...allRatios), meaningfulMotionSamples: motionSamples,
      maximumChangedRatio, maximumMeanDifference, transport: transport.snapshot(),
      candidateOnly: process.argv[2] === '--legacy-candidate', maximumWorkerPackets, maximumBrowserFrames,
      h264: true, savedPixels: false, globalSettingsWrites: false }));
    assert.ok(ratios.length >= 8 && signatures.size >= 5, 'Generated video missing, black or frozen in browser');
    assert.ok(motionSamples >= 5, 'Colored frames lack meaningful motion; hash changes alone are insufficient');
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
}
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
