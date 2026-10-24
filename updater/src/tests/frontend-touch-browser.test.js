// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require('../runtime/preview-gateway/node_modules/playwright-core');
const frontend = process.env.TA_FRONTEND_DIR;
if (!frontend) throw new Error('Set TA_FRONTEND_DIR to the patched pinned frontend');

test('real browser pointer capture sends one tap and one cancellation without Android input', async () => {
  const browser = await chromium.launch({ executablePath: process.env.EDGE_PATH ||
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 800, height: 600 } });
    await page.setContent('<div id="touch" style="position:absolute;left:10px;top:20px;width:400px;height:300px;touch-action:none"></div>');
    await page.evaluate(() => {
      window.messages = [];
      window.TAHtml = { constants: { TOUCH_SLOT_COUNT: 10 }, log() {} };
      window.ReconnectingWebSocket = function() {
        this.readyState = 1; this.send = payload => window.messages.push(payload); this.close = () => {};
      };
    });
    await page.addScriptTag({ path: path.join(frontend, 'js/touchscreen/touchscreen-controller.js') });
    await page.evaluate(() => {
      window.touch = new TAHtml.TouchscreenController({ touchLayer: document.querySelector('#touch'),
        apiClient: { displaySession: {}, socketUrl: url => url }, canControl: () => true,
        socketUrl: 'ws://unused', getTouchscreenSize: () => ({ width: 800, height: 600 }) });
      touch.initialize(); touch.touchScreenSocket.onopen();
    });
    await page.mouse.click(210, 170);
    await page.waitForTimeout(50);
    let messages = await page.evaluate(() => window.messages.slice());
    assert.equal(messages.length, 2, 'normal tap plus automatic capture loss must not duplicate release');
    assert.match(messages[0], /T 1\na 1\nX 400\nY 300/);
    assert.match(messages[1], /T -1\na 0/);
    await page.mouse.down({ button: 'right' }); await page.mouse.up({ button: 'right' });
    assert.equal(await page.evaluate(() => messages.length), 2);
    await page.mouse.down();
    await page.mouse.move(700, 500);
    messages = await page.evaluate(() => window.messages.slice());
    assert.match(messages.at(-1), /X 799\nY 599/);
    await page.evaluate(() => {
      const slot = touch.slots.find(slot => slot.trackingId !== -1);
      document.querySelector('#touch').releasePointerCapture(slot.trackingId);
    });
    await page.mouse.move(701, 501);
    await page.mouse.up();
    await page.waitForTimeout(50);
    messages = await page.evaluate(() => window.messages.slice());
    assert.equal(messages.filter(payload => payload.includes('T -1')).length, 2);
    assert.equal(await page.evaluate(() => touch.slots.every(slot => slot.trackingId === -1)), true);
  } finally { await browser.close(); }
});
