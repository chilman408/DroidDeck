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
  const errors = [], writes = [];
  let context, viewerContext;
  try {
    context = await browser.newContext();
    const response = await context.request.post(origin + '/api/displaySession', {
      headers: { Origin: origin, 'Content-Type': 'application/json' }, data: {},
    });
    assert.equal(response.status(), 200);
    const session = await response.json();
    const stateResponse = await context.request.get(origin + '/api/displayState', {
      headers: { 'X-Display-Session': session.token },
    });
    assert.equal(stateResponse.status(), 200);
    const state = await stateResponse.json();
    async function pageFor(ctx, legacy = false) {
      const page = await ctx.newPage();
      await page.setViewportSize({ width: state.width, height: state.height });
      if (legacy) await page.addInitScript(() => { window.VideoDecoder = undefined; });
      page.on('pageerror', error => errors.push(error.message));
      page.on('request', request => {
        if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/displayState') writes.push('display');
      });
      if (process.env.TA_FRONTEND_DIR) await page.route(origin + '/js/**', async route => {
        const root = path.resolve(process.env.TA_FRONTEND_DIR);
        const file = path.resolve(root, new URL(route.request().url()).pathname.slice(1));
        assert.ok(file.startsWith(root + path.sep));
        await route.fulfill({ body: fs.readFileSync(file), contentType: file.endsWith('.wasm') ? 'application/wasm' : 'text/javascript' });
      });
      await page.goto(origin + '/?check=renderer#home', { waitUntil: 'domcontentloaded' });
      return page;
    }
    async function visible(page, expected) {
      await page.waitForFunction(() => Number(document.querySelector('#canvas')?.dataset.videoFrames) > 0,
        undefined, { timeout: 30000 });
      const script = await page.locator('script[src*="h264"]').evaluateAll(elements => elements.map(element => element.src));
      assert.ok(script.some(src => src.includes(expected === 'legacy' ? 'h264-broadway/h264Brodway.js' : 'h264-webcodecs/h264WebCodecs.js')),
        JSON.stringify({ expected, script, choice: await page.evaluate(() => window.TAHtml.browserRenderer.choice()) }));
      const png = PNG.sync.read(await page.locator('#canvas').screenshot());
      let nonblack = 0;
      for (let i = 0; i < png.data.length; i += 4) {
        if (png.data[i] > 8 || png.data[i + 1] > 8 || png.data[i + 2] > 8) nonblack++;
      }
      assert.ok(nonblack > png.width * png.height * .01);
    }
    async function settings(page) {
      await page.locator('#home-settings-button').click();
      await page.locator('[data-page="settings"]').click();
      await page.locator('[data-browser-renderer] .settings-dropdown-trigger').waitFor();
    }
    const legacyOnly = process.argv.includes('--legacy-capability');
    const page = await pageFor(context, legacyOnly);
    await visible(page, legacyOnly ? 'legacy' : 'webcodecs');
    for (const choice of legacyOnly ? ['legacy', 'auto'] : ['legacy', 'webcodecs', 'auto']) {
      await settings(page);
      const selector = page.locator('[data-browser-renderer]');
      await selector.locator('.settings-dropdown-trigger').click();
      const navigation = page.waitForEvent('framenavigated', frame => frame === page.mainFrame());
      await selector.locator(`[data-value="${choice}"]`).click();
      await navigation; await page.waitForLoadState('domcontentloaded');
      await visible(page, choice === 'legacy' || legacyOnly ? 'legacy' : 'webcodecs');
      assert.equal(await page.evaluate(() => window.TAHtml.browserRenderer.choice()), choice);
      assert.equal(new URL(page.url()).searchParams.get('check'), 'renderer');
      assert.equal(new URL(page.url()).hash, '#home');
      await page.reload({ waitUntil: 'domcontentloaded' });
      await visible(page, choice === 'legacy' || legacyOnly ? 'legacy' : 'webcodecs');
    }
    viewerContext = await browser.newContext();
    const viewer = await pageFor(viewerContext, true);
    await visible(viewer, 'legacy');
    await settings(viewer);
    await viewer.waitForTimeout(2000);
    assert.equal(await viewer.locator('[data-browser-renderer] .settings-dropdown-trigger').isEnabled(), true);
    assert.equal(await viewer.locator('.settings-dropdown-trigger').first().isDisabled(), true,
      'Local renderer controls must not grant global display authority');
    await viewer.locator('[data-browser-renderer] .settings-dropdown-trigger').click();
    assert.equal(await viewer.locator('[data-browser-renderer] [data-value="webcodecs"]').count(), 0);
    const viewerNavigation = viewer.waitForEvent('framenavigated', frame => frame === viewer.mainFrame());
    await viewer.locator('[data-browser-renderer] [data-value="legacy"]').click();
    await viewerNavigation; await viewer.waitForLoadState('domcontentloaded');
    await visible(viewer, 'legacy');
    assert.equal(await page.evaluate(() => window.TAHtml.browserRenderer.choice()), 'auto');
    assert.deepEqual(writes, []);
    assert.deepEqual(errors, []);
    if (process.env.SMOKE_SCREENSHOT_DIR) {
      await settings(page);
      await page.screenshot({ path: path.join(process.env.SMOKE_SCREENSHOT_DIR, 'renderer-settings.png') });
    }
    console.log(JSON.stringify({ rendererSwitches: legacyOnly ? 2 : 3, persistedAfterReload: true,
      viewerSwitch: true, globalWrites: writes.length, pageErrors: errors }));
  } finally { await viewerContext?.close(); await context?.close(); await browser.close(); }
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
