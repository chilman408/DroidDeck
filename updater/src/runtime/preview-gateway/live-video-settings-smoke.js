// SPDX-License-Identifier: GPL-3.0-only
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright-core');
const { PNG } = require('../waydroid-display/node_modules/pngjs');
const origin = 'https://teslavm.mondriandsm.synology.me';
async function main() {
  const browser = await chromium.launch({ executablePath: process.env.EDGE_PATH ||
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  const context = await browser.newContext(), errors = [];
  let page, original, lastRevision, viewerContext, pageToken, pageCsrf;
  try {
    const response = await context.request.post(origin + '/api/displaySession', {
      headers: { Origin: origin, 'Content-Type': 'application/json' }, data: {},
    }); assert.equal(response.status(), 200);
    const session = await response.json();
    const state = await (await context.request.get(origin + '/api/displayState', {
      headers: { 'X-Display-Session': session.token },
    })).json();
    assert.equal(state.videoControlSupported, 1);
    page = await context.newPage(); await page.setViewportSize({ width: state.width, height: state.height });
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      window.testVideoSessions = 0;
      const Native = WebSocket;
      window.WebSocket = new Proxy(Native, { construct(target, args) {
        const socket = new target(...args);
        if (new URL(args[0]).pathname === '/sockets/display') socket.addEventListener('message', event => {
          if (typeof event.data === 'string' && event.data.includes('"scrcpy-session"')) {
            window.testVideoSessions++;
            window.testVideoSessionFrames = Number(document.querySelector('#canvas')?.dataset.videoFrames || 0);
          }
        });
        return socket;
      } });
    });
    if (process.argv.includes('--legacy-capability')) await page.addInitScript(() => { window.VideoDecoder = undefined; });
    await page.goto(origin + '/', { waitUntil: 'domcontentloaded' });
    async function visible() {
      await page.waitForFunction(() => document.querySelector('#canvas')?.dataset.videoState === 'ready' &&
        Number(document.querySelector('#canvas').dataset.videoFrames) > (window.testVideoSessionFrames || 0),
        undefined, { timeout: 30000 });
      const png = PNG.sync.read(await page.locator('#canvas').screenshot()); let nonblack = 0;
      for (let i = 0; i < png.data.length; i += 4) if (png.data[i] > 8 || png.data[i + 1] > 8 || png.data[i + 2] > 8) nonblack++;
      assert.ok(nonblack > png.width * png.height * .01, 'Encoder reconfiguration must return nonblack H264');
    }
    await visible();
    // Obtain the page's session from its display request without relying on application internals.
    await page.route(origin + '/api/videoState', async route => {
      const request = route.request(), headers = request.headers();
      pageToken = headers['x-display-session']; pageCsrf = headers['x-display-csrf'];
      await route.continue();
    });
    original = state.videoTargets; lastRevision = original.revision;
    await page.locator('#home-audio-button').click();
    await page.waitForFunction(() => window.__liveAudioEl && !window.__liveAudioEl.paused && window.__liveAudioEl.currentTime > 1,
      undefined, { timeout: 30000 });
    const audioBefore = await page.evaluate(() => window.__liveAudioEl.currentTime);
    async function settings() {
      await page.locator('#home-settings-button').click(); await page.locator('[data-page="settings"]').click();
      await page.locator('[data-video-bitrate] .settings-dropdown-trigger').waitFor();
    }
    const results = [];
    for (const [selector, value] of [['[data-video-bitrate]', 2000000], ['[data-video-frame-rate]', 45],
      ['[data-video-bitrate]', 8000000], ['[data-video-frame-rate]', 60]]) {
      await settings();
      const before = await page.evaluate(() => window.testVideoSessions);
      await page.locator(selector + ' .settings-dropdown-trigger').click();
      const reply = page.waitForResponse(res => new URL(res.url()).pathname === '/api/videoState' && res.request().method() === 'POST');
      await page.locator(selector + ` [data-value="${value}"]`).click();
      const savedResponse = await reply; assert.equal(savedResponse.status(), 200);
      const saved = await savedResponse.json(); lastRevision = saved.revision;
      await page.locator('[data-page="home"]').click();
      await page.waitForFunction(prior => window.testVideoSessions > prior, before, { timeout: 30000 });
      await visible(); results.push(saved);
    }
    const audioAfter = await page.evaluate(() => ({ time: window.__liveAudioEl.currentTime,
      paused: window.__liveAudioEl.paused, error: window.__liveAudioEl.error?.code || null }));
    assert.ok(audioAfter.time > audioBefore + 1); assert.equal(audioAfter.paused, false); assert.equal(audioAfter.error, null);
    viewerContext = await browser.newContext(); const viewer = await viewerContext.newPage();
    await viewer.setViewportSize({ width: state.width, height: state.height });
    await viewer.goto(origin + '/', { waitUntil: 'domcontentloaded' });
    await viewer.waitForFunction(() => document.querySelector('#canvas')?.dataset.videoState === 'ready' &&
      Number(document.querySelector('#canvas').dataset.videoFrames) > 0, undefined, { timeout: 30000 });
    await viewer.locator('#home-settings-button').click(); await viewer.locator('[data-page="settings"]').click();
    await viewer.locator('[data-video-bitrate] .settings-dropdown-trigger').waitFor();
    assert.equal(await viewer.locator('[data-video-bitrate] .settings-dropdown-trigger').isDisabled(), true);
    assert.equal(await viewer.locator('[data-video-frame-rate] .settings-dropdown-trigger').isDisabled(), true);
    assert.equal(await viewer.locator('[data-browser-renderer] .settings-dropdown-trigger').isEnabled(), true);
    await viewerContext.close(); viewerContext = undefined;
    await settings(); await page.reload({ waitUntil: 'domcontentloaded' }); await visible();
    await settings();
    assert.equal(await page.locator('[data-video-bitrate] .settings-dropdown-value').textContent(), '8 Mbps');
    assert.equal(await page.locator('[data-video-frame-rate] .settings-dropdown-value').textContent(), '60 fps');
    // Reload renews the owner token; intercept a no-op menu submission to obtain it for restoration.
    await page.locator('[data-video-bitrate] .settings-dropdown-trigger').click();
    const renewed = page.waitForResponse(res => new URL(res.url()).pathname === '/api/videoState' && res.request().method() === 'POST');
    await page.locator('[data-video-bitrate] [data-value="8000000"]').click();
    assert.equal((await renewed).status(), 200);
    if (process.env.SMOKE_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.SMOKE_SCREENSHOT_DIR, 'video-settings.png') });
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ targets: results, settingsPersisted: true, viewerDenied: true, errors,
      audioBefore, audioAfter, renderer: process.argv.includes('--legacy-capability') ? 'legacy' : 'webcodecs' }));
  } finally {
    try {
      if (original && pageToken && pageCsrf) {
        const restore = await context.request.post(origin + '/api/videoState', { headers: { Origin: origin,
          'X-Display-Session': pageToken, 'X-Display-CSRF': pageCsrf },
          data: { revision: lastRevision, bitRate: original.bitRate, maxFps: original.maxFps } });
        assert.equal(restore.status(), 200, 'Restore original encoder targets without overwriting a concurrent user change');
        console.log(JSON.stringify({ restored: await restore.json() }));
      }
    } finally { await viewerContext?.close(); await context.close(); await browser.close(); }
  }
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
