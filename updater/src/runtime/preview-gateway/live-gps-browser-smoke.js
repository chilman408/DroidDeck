// SPDX-License-Identifier: GPL-3.0-only
const assert = require("node:assert/strict");
const fs = require("node:fs");
const { chromium } = require("playwright-core");
async function main() {
  const base = "https://127.0.0.1:18445/";
  if (!process.env.GPS_TEST_AUTH_FILE) throw new Error("GPS_TEST_AUTH_FILE required");
  const auth = JSON.parse(fs.readFileSync(process.env.GPS_TEST_AUTH_FILE, "utf8"));
  assert.equal(auth.username, "gps-test"); assert.match(auth.password, /^[a-f0-9]{64}$/);
  const browser = await chromium.launch({ executablePath: process.env.EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", headless: true });
  const context = await browser.newContext({ viewport: { width: 1306, height: 960 }, ignoreHTTPSErrors: true,
    httpCredentials: { origin: new URL(base).origin, ...auth } });
  await context.addInitScript(() => {
    window.testGpsRequests = 0; window.testGpsSends = 0;
    navigator.geolocation.getCurrentPosition = success => {
      window.testGpsRequests++; success({ timestamp: Date.now(), coords: { latitude: 0, longitude: 0, accuracy: 5, speed: 2, heading: 90 } });
    };
    const Native = WebSocket;
    window.WebSocket = new Proxy(Native, { construct(target, args) {
      const socket = new target(...args);
      if (new URL(args[0]).pathname === "/sockets/gps") {
        const send = socket.send.bind(socket); socket.send = data => { window.testGpsSends++; return send(data); };
      }
      return socket;
    } });
  });
  const page = await context.newPage(), errors = [];
  page.on("pageerror", error => errors.push(error.message));
  try {
    await page.goto(base, { waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => Number(document.querySelector("#canvas")?.dataset.videoFrames) > 0, undefined, { timeout: 40000 });
    assert.equal(await page.evaluate(() => window.testGpsRequests), 0, "Default-off GPS must not prompt");
    await page.locator("#home-settings-button").click(); await page.locator('[data-page="settings"]').click();
    await page.locator('.settings-menu-item').filter({ hasText: 'GPS' }).click();
    const toggle = page.locator('#settings-content input[type="checkbox"]');
    await Promise.all([page.waitForResponse(response => response.url().endsWith('/api/gpsState') && response.status() === 200),
      page.locator('#settings-content .settings-switch-slider').click()]);
    assert.equal(await toggle.isChecked(), true);
    await page.locator('[data-page="home"]').click();
    await page.waitForFunction(() => window.testGpsSends >= 2, undefined, { timeout: 20000 });
    console.log(JSON.stringify({ browserGpsSending: true, syntheticOnly: true, androidReadbackWindowSeconds: 30 }));
    // The separate protected Android readback must run during this bounded window.
    await page.waitForTimeout(30000);
    const viewer = await context.newPage(); await viewer.goto(base, { waitUntil: "domcontentloaded" });
    await viewer.waitForFunction(() => Number(document.querySelector("#canvas")?.dataset.videoFrames) > 0, undefined, { timeout: 20000 });
    await viewer.waitForTimeout(1200); assert.equal(await viewer.evaluate(() => window.testGpsRequests), 0, "Viewer must not request location");
    await viewer.close();
    await page.locator("#home-settings-button").click(); await page.locator('[data-page="settings"]').click();
    await page.locator('.settings-menu-item').filter({ hasText: 'GPS' }).click();
    await Promise.all([page.waitForResponse(response => response.url().endsWith('/api/gpsState') && response.status() === 200),
      page.locator('#settings-content .settings-switch-slider').click()]);
    assert.equal(await toggle.isChecked(), false);
    await page.locator('[data-page="home"]').click();
    const requests = await page.evaluate(() => window.testGpsRequests); await page.waitForTimeout(2200);
    assert.equal(await page.evaluate(() => window.testGpsRequests), requests);
    await page.reload(); await page.waitForFunction(() => Number(document.querySelector("#canvas")?.dataset.videoFrames) > 0, undefined, { timeout: 20000 });
    assert.equal(await page.evaluate(() => window.testGpsRequests), 0);
    assert.equal(new URL(page.url()).searchParams.get("video"), null); assert.deepEqual(errors, []);
    console.log(JSON.stringify({ gpsSettings: true, ownerOnly: true, disabled: true, preferenceRestoredOff: true, h264: true, pageErrors: 0 }));
  } finally { await browser.close(); }
}
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
