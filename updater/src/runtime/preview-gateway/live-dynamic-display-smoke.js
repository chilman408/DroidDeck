// SPDX-License-Identifier: GPL-3.0-only
const assert = require("node:assert/strict");
const path = require("node:path");
const { chromium } = require("playwright-core");
async function main() {
  const base = process.argv[2];
  const url = new URL(base);
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || url.port !== "18444" || url.pathname !== "/") {
    throw new Error("Use the isolated SSH-tunneled loopback preview on port 18444");
  }
  const browser = await chromium.launch({ executablePath: process.env.EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", headless: true });
  let session, owner, viewer;
  const sessions = new Map();
  function watch(page, role) {
    let last;
    page.on("response", async response => {
      try {
        if (response.url().endsWith('/api/displaySession') && response.ok()) {
          sessions.set(page, await response.json());
          if (role === 'owner') session = sessions.get(page);
        }
        if (response.url().endsWith('/api/displayState') && response.ok()) {
          const state = await response.json();
          const record = { role, method: response.request().method(), width: state.width, height: state.height,
            generation: state.generation, canControl: state.canControl, status: state.transactionStatus, dynamic: state.isResponsive };
          const key = JSON.stringify(record);
          if (key !== last) { last = key; console.log(key); }
        }
      } catch { /* Diagnostics must not interrupt the acceptance test. */ }
    });
  }
  const errors = [], evidence = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    owner = await context.newPage(); owner.on("pageerror", error => errors.push(error.message));
    watch(owner, 'owner');
    await owner.goto(base);
    if (process.argv[3] === '--presets-only') {
      await owner.waitForFunction(() => document.querySelector('#image')?.naturalWidth === 1332 &&
        document.querySelector('#image').naturalHeight === 832, undefined, { timeout: 40000 });
      for (const [label, preset, width, height] of [['720p', 1, 1152, 720], ['480p', 4, 768, 480], ['832p', 0, 1332, 832]]) {
        await owner.locator('#home-settings-button').click(); await owner.locator('[data-page="settings"]').click();
        await owner.locator('.settings-dropdown-trigger').click(); await owner.getByRole('button', { name: label, exact: true }).click();
        await owner.waitForFunction(label => document.querySelector('.settings-dropdown-value')?.textContent === label &&
          !document.querySelector('.settings-dropdown-trigger').disabled, label, { timeout: 30000 });
        const state = await owner.evaluate(async token => (await fetch('/api/displayState', { headers: { 'X-Display-Session': token } })).json(), session.token);
        assert.equal(state.resolutionPreset, preset); assert.equal(state.density, 180); assert.equal(state.isResponsive, 1);
        await owner.locator('[data-page="home"]').click();
        await owner.waitForFunction(size => document.querySelector('#image')?.naturalWidth === size.width &&
          document.querySelector('#image').naturalHeight === size.height, { width, height }, { timeout: 40000 });
        const aligned = await owner.evaluate(() => {
          const a = document.querySelector('#image').getBoundingClientRect(), b = document.querySelector('#touch-layer').getBoundingClientRect();
          return [a.x, a.y, a.width, a.height].every((value, index) => value === [b.x, b.y, b.width, b.height][index]);
        });
        assert.equal(aligned, true); console.log(JSON.stringify({ preset: label, width, height, density: state.density, overlayAligned: aligned }));
      }
      await owner.setViewportSize({ width: 390, height: 844 });
      await owner.waitForFunction(() => document.querySelector('#image')?.naturalWidth === 390 &&
        document.querySelector('#image').naturalHeight === 844, undefined, { timeout: 40000 });
      await owner.locator('#home-settings-button').click(); await owner.locator('[data-page="settings"]').click();
      if (process.env.SMOKE_SCREENSHOT_DIR) await owner.screenshot({ path: path.join(process.env.SMOKE_SCREENSHOT_DIR, 'live-dynamic-settings-mobile.png') });
      assert.equal(errors.length, 0, errors.join('\n'));
      console.log('Live Android resolution presets 720p/480p/832p and mobile settings passed; no Android input sent');
      return;
    }
    for (const [name, viewport, expected] of [
      ["live-dynamic-desktop", { width: 1440, height: 900 }, { width: 1332, height: 832 }],
      ["live-dynamic-portrait", { width: 390, height: 844 }, { width: 390, height: 844 }],
      ["live-dynamic-wide", { width: 1920, height: 720 }, { width: 1920, height: 720 }],
    ]) {
      await owner.setViewportSize(viewport);
      await owner.waitForFunction(expected => {
        const image = document.querySelector("#image"); return image?.naturalWidth === expected.width && image.naturalHeight === expected.height;
      }, expected, { timeout: 40000 });
      const state = await owner.evaluate(async token => (await fetch('/api/displayState', { headers: { 'X-Display-Session': token } })).json(), session.token);
      assert.equal(state.width, expected.width); assert.equal(state.height, expected.height); assert.equal(state.isResponsive, 1);
      const geometry = await owner.evaluate(() => {
        const image = document.querySelector('#image'), touch = document.querySelector('#touch-layer');
        const a = image.getBoundingClientRect(), b = touch.getBoundingClientRect();
        const canvas = document.createElement('canvas'); canvas.width = 64; canvas.height = 64;
        const ctx = canvas.getContext('2d'); ctx.drawImage(image, 0, 0, 64, 64);
        const pixels = ctx.getImageData(0, 0, 64, 64).data;
        return { image: [a.x, a.y, a.width, a.height], touch: [b.x, b.y, b.width, b.height],
          nonblack: [...pixels].some((v, i) => i % 4 !== 3 && v > 8), overflow: document.documentElement.scrollWidth > innerWidth };
      });
      assert.deepEqual(geometry.image, geometry.touch); assert.equal(geometry.nonblack, true); assert.equal(geometry.overflow, false);
      assert.ok(Math.abs(geometry.image[2] / geometry.image[3] / (expected.width / expected.height) - 1) < 0.01);
      evidence.push({ name, width: state.width, height: state.height, generation: state.generation, nonblack: true, overlayAligned: true });
      if (process.env.SMOKE_SCREENSHOT_DIR) await owner.screenshot({ path: path.join(process.env.SMOKE_SCREENSHOT_DIR, name + '.png') });
      console.log(JSON.stringify(evidence[evidence.length - 1]));
    }
    viewer = await context.newPage(); watch(viewer, 'viewer'); await viewer.setViewportSize({ width: 390, height: 844 }); await viewer.goto(base);
    await viewer.waitForFunction(() => document.querySelector('#image')?.naturalWidth === 1920, undefined, { timeout: 30000 });
    await new Promise(resolve => setTimeout(resolve, 1500));
    assert.equal(await viewer.evaluate(() => document.querySelector('#image').naturalWidth), 1920);
    await owner.locator('#home-settings-button').click(); await owner.locator('[data-page="settings"]').click();
    await owner.waitForSelector('.settings-switch'); assert.equal(await owner.locator('.settings-switch input').isChecked(), true);
    if (process.env.SMOKE_SCREENSHOT_DIR) await owner.screenshot({ path: path.join(process.env.SMOKE_SCREENSHOT_DIR, 'live-dynamic-settings.png') });
    await owner.locator('.settings-switch').click();
    await owner.waitForFunction(() => document.querySelector('.settings-switch input')?.checked === false &&
      !document.querySelector('.settings-switch input').disabled, undefined, { timeout: 30000 });
    await owner.locator('[data-page="home"]').click(); await owner.setViewportSize({ width: 1440, height: 900 });
    await owner.waitForFunction(() => document.querySelector('#image')?.naturalWidth === 1440 && document.querySelector('#image').naturalHeight === 540, undefined, { timeout: 40000 });
    await owner.locator('#home-settings-button').click(); await owner.locator('[data-page="settings"]').click();
    await owner.locator('.settings-switch').click();
    await owner.waitForFunction(() => document.querySelector('.settings-switch input')?.checked === true &&
      !document.querySelector('.settings-switch input').disabled, undefined, { timeout: 30000 });
    await owner.locator('[data-page="home"]').click();
    await owner.waitForFunction(() => document.querySelector('#image')?.naturalWidth === 1332 && document.querySelector('#image').naturalHeight === 832, undefined, { timeout: 40000 });
    await owner.close();
    await viewer.waitForFunction(() => document.querySelector('#image')?.naturalWidth === 390 && document.querySelector('#image').naturalHeight === 844, undefined, { timeout: 40000 });
    assert.equal(errors.length, 0, errors.join('\n'));
    console.log('Live Android: dynamic off/on, view-only second tab and owner takeover passed; no Android input sent');
  } catch (error) {
    for (const [role, page] of [['owner', owner], ['viewer', viewer]]) {
      if (!page || page.isClosed()) continue;
      const details = await page.evaluate(() => {
        const image = document.querySelector('#image'), area = document.querySelector('#home-stage')?.getBoundingClientRect();
        return { hidden: document.hidden, focusedTag: document.activeElement?.tagName,
          frame: { width: image?.naturalWidth, height: image?.naturalHeight },
          area: area && { width: area.width, height: area.height } };
      }).catch(() => ({}));
      console.error(JSON.stringify({ failurePage: role, ...details }));
      if (process.env.SMOKE_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.SMOKE_SCREENSHOT_DIR, 'live-dynamic-failure-' + role + '.png') }).catch(() => {});
    }
    throw error;
  } finally { await browser.close(); }
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
