// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict'), path = require('node:path');
const { chromium } = require('../runtime/preview-gateway/node_modules/playwright-core');
const { PNG } = require('../runtime/waydroid-display/node_modules/pngjs');
const frontend = process.env.TA_FRONTEND_DIR;
if (!frontend) throw new Error('Set TA_FRONTEND_DIR to the patched pinned frontend');

test('supported ratios fit real browser canvases and map both touch edges without letterbox input', async () => {
  const browser = await chromium.launch({ executablePath: process.env.EDGE_PATH ||
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const page = await browser.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.setContent('<section id="home-shell"><div id="home-stage"><div id="display-root">' +
      '<canvas id="canvas"></canvas><div id="touch-layer"></div></div></div></section>');
    await page.addStyleTag({ path: path.join(frontend, 'css/app.css') });
    for (const file of ['js/core/shared.js', 'js/display/display-controller.js',
      'js/display/display-renderer-manager.js', 'js/touchscreen/touchscreen-controller.js']) {
      await page.addScriptTag({ path: path.join(frontend, file) });
    }
    await page.evaluate(() => {
      window.messages = [];
      window.ReconnectingWebSocket = function() {
        this.readyState = 1; this.send = value => messages.push(value); this.close = () => {};
      };
      window.touch = new TAHtml.TouchscreenController({ touchLayer: document.querySelector('#touch-layer'),
        apiClient: { displaySession: {}, socketUrl: url => url }, canControl: () => true,
        socketUrl: 'ws://unused', getTouchscreenSize: () => window.logicalSize });
      touch.initialize(); touch.touchScreenSocket.onopen();
    });
    for (const [width, height, a, b] of [[1200, 900, 4, 3], [700, 700, 1, 1],
      [1920, 1080, 16, 9], [390, 844, 9, 16], [768, 1024, 3, 4]]) {
      await page.setViewportSize({ width, height });
      const result = await page.evaluate(() => {
        logicalSize = TAHtml.calculateDynamicSize({ width: innerWidth, height: innerHeight }, 0,
          { landscapeNavigationMinHeight: 676 }, true);
        TAHtml.DisplayRendererManager.prototype.applyDisplaySize.call({
          displayRoot: document.querySelector('#display-root'), updateRendererRuntimeConfig() {},
        }, logicalSize.width, logicalSize.height);
        const canvas = document.querySelector('#canvas');
        canvas.width = logicalSize.width; canvas.height = logicalSize.height;
        const context = canvas.getContext('2d');
        context.fillStyle = '#d32040'; context.fillRect(0, 0, canvas.width / 2, canvas.height);
        context.fillStyle = '#20b050'; context.fillRect(canvas.width / 2, 0, canvas.width / 2, canvas.height);
        messages.length = 0;
        return logicalSize;
      });
      assert.equal(result.width * b, result.height * a);
      const box = await page.locator('#canvas').boundingBox(), touchBox = await page.locator('#touch-layer').boundingBox();
      assert.deepEqual(touchBox, box);
      assert.ok(box.x >= -1 && box.y >= -1 && box.x + box.width <= width + 1 && box.y + box.height <= height + 1);
      assert.ok(Math.abs(box.width - width) < 2 || Math.abs(box.height - height) < 2);
      const pixels = PNG.sync.read(await page.locator('#canvas').screenshot());
      const row = Math.floor(pixels.height / 2) * pixels.width;
      assert.ok(pixels.data[(row + Math.floor(pixels.width / 4)) * 4] > 150 &&
        pixels.data[(row + Math.floor(pixels.width * 3 / 4)) * 4 + 1] > 100);
      for (const fraction of [0.01, 0.99]) await page.mouse.click(box.x + box.width * fraction, box.y + box.height / 2);
      const messages = await page.evaluate(() => window.messages.slice());
      assert.equal(messages.length, 4);
      for (const [index, fraction] of [[0, 0.01], [2, 0.99]]) {
        const x = Number(/\nX (\d+)/.exec(messages[index])[1]);
        assert.ok(Math.abs(x / result.width - fraction) < 0.01);
      }
      if (box.x > 2) await page.mouse.click(box.x / 2, height / 2);
      if (box.y > 2) await page.mouse.click(width / 2, box.y / 2);
      assert.equal(await page.evaluate(() => messages.length), 4);
      if (process.env.ASPECT_SCREENSHOT_DIR) await page.screenshot({
        path: path.join(process.env.ASPECT_SCREENSHOT_DIR, `aspect-${a}-${b}.png`),
      });
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
