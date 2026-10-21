// SPDX-License-Identifier: GPL-3.0-only
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { chromium } = require("playwright-core");
const { WebSocketServer } = require("ws");
const { PNG } = require("../waydroid-display/node_modules/pngjs");
const { createGateway } = require("./server");
const { createDisplayControl } = require("./display-control");
const listen = server => new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
async function main() {
  const frontendDir = process.argv[2]; assert.ok(frontendDir, "Pass the patched pinned frontend directory");
  let actual = { width: 1280, height: 768, density: 180, resolutionPreset: 0, isResponsive: 1, recoveryRequired: false };
  let frame, gateway, browser, touchMessages = 0, failNext = false, mutations = 0;
  const makeFrame = () => {
    const png = new PNG({ width: actual.width, height: actual.height });
    for (let i = 0; i < png.data.length; i += 4) { png.data[i] = 30; png.data[i + 1] = 170; png.data[i + 2] = 90; png.data[i + 3] = 255; }
    frame = PNG.sync.write(png);
  }; makeFrame();
  const api = http.createServer((req, res) => {
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify(req.url === "/api/health" ? { status: "ok" } : req.url === "/api/configuration" ? {
      "persist.tesla-android.gps.is_active": 0, "persist.tesla-android.browser_audio.is_enabled": 0,
    } : req.url === "/api/deviceInfo" ? { device_model: "Test VM", cpu_temperature: -1 } : {}));
  });
  const display = new WebSocketServer({ host: "127.0.0.1", port: 0 });
  const touch = new WebSocketServer({ host: "127.0.0.1", port: 0 });
  touch.on("connection", socket => socket.on("message", () => { touchMessages++; }));
  display.on("connection", socket => { socket.send(frame); });
  const ticker = setInterval(() => { for (const socket of display.clients) if (socket.readyState === 1) socket.send(frame); }, 300);
  const control = createDisplayControl({ helper: { inspect: async () => actual, resize: async desired => {
    mutations++; await new Promise(resolve => setTimeout(resolve, 150));
    if (failNext) { failNext = false; throw new Error("Test capture failure"); }
    actual = { ...desired, recoveryRequired: false }; makeFrame(); return actual;
  } } });
  try {
    await listen(api);
    await control.initialize();
    gateway = createGateway({ frontendDir, displayControl: control, targets: {
      api: `http://127.0.0.1:${api.address().port}`, display: `ws://127.0.0.1:${display.address().port}`,
      touchscreen: `ws://127.0.0.1:${touch.address().port}` } }); await listen(gateway);
    const base = `http://127.0.0.1:${gateway.address().port}`;
    browser = await chromium.launch({ executablePath: process.env.EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const errors = []; let ownerSession;
    const owner = await context.newPage(); owner.on("pageerror", error => errors.push(error.message));
    owner.on("response", async response => { if (response.url().endsWith("/api/displaySession") && response.ok()) ownerSession = await response.json(); });
    await owner.goto(base);
    async function frameCheck(page, expected, screenshot) {
      await page.waitForFunction(expected => {
        const image = document.querySelector("#image"); return image?.naturalWidth === expected.width && image.naturalHeight === expected.height;
      }, expected, { timeout: 15000 });
      const result = await page.evaluate(() => {
        const image = document.querySelector("#image"), touch = document.querySelector("#touch-layer");
        const a = image.getBoundingClientRect(), b = touch.getBoundingClientRect();
        const canvas = document.createElement("canvas"); canvas.width = 1; canvas.height = 1;
        const drawing = canvas.getContext("2d"); drawing.drawImage(image, 0, 0, 1, 1);
        return { image: [a.x, a.y, a.width, a.height], touch: [b.x, b.y, b.width, b.height], pixel: [...drawing.getImageData(0, 0, 1, 1).data],
          overflow: document.documentElement.scrollWidth > innerWidth };
      });
      assert.deepEqual(result.image, result.touch); assert.equal(result.overflow, false);
      assert.ok(result.pixel[1] > 100); assert.ok(Math.abs(result.image[2] / result.image[3] / (expected.width / expected.height) - 1) < 0.01);
      if (process.env.SMOKE_SCREENSHOT_DIR && screenshot) await page.screenshot({ path: path.join(process.env.SMOKE_SCREENSHOT_DIR, screenshot + ".png") });
    }
    for (const [name, viewport, expected] of [
      ["dynamic-desktop", { width: 1440, height: 900 }, { width: 1332, height: 832 }],
      ["dynamic-portrait", { width: 390, height: 844 }, { width: 390, height: 844 }],
      ["dynamic-wide", { width: 1920, height: 720 }, { width: 1920, height: 720 }],
    ]) { await owner.setViewportSize(viewport); await frameCheck(owner, expected, name); }
    const viewer = await context.newPage(); await viewer.setViewportSize({ width: 390, height: 844 }); await viewer.goto(base);
    await frameCheck(viewer, { width: 1920, height: 720 });
    const count = mutations; await new Promise(resolve => setTimeout(resolve, 1500)); assert.equal(mutations, count);
    const openSettings = async () => { await owner.locator('#home-settings-button').click(); await owner.locator('[data-page="settings"]').click(); };
    await openSettings(); await owner.waitForSelector(".settings-switch");
    assert.equal(await owner.locator(".settings-switch input").isChecked(), true);
    await owner.locator(".settings-switch").click();
    await owner.waitForFunction(() => document.querySelector('.settings-switch input')?.checked === false && !document.querySelector('.settings-switch input').disabled);
    assert.equal(actual.isResponsive, 0);
    await owner.locator('[data-page="home"]').click(); await owner.setViewportSize({ width: 1440, height: 900 });
    await frameCheck(owner, { width: 1440, height: 540 });
    await openSettings();
    await owner.locator('.settings-dropdown-trigger').click(); await owner.getByRole("button", { name: "480p", exact: true }).click();
    await owner.waitForFunction(() => document.querySelector('.settings-dropdown-value')?.textContent === '480p' &&
      !document.querySelector('.settings-dropdown-trigger').disabled);
    assert.equal(actual.resolutionPreset, 4); assert.equal(actual.height, 480);
    failNext = true;
    await owner.locator('.settings-dropdown-trigger').click(); await owner.getByRole("button", { name: "720p", exact: true }).click();
    await owner.waitForSelector('.settings-error'); assert.equal(actual.resolutionPreset, 4);
    assert.equal(await owner.locator('.settings-dropdown-value').textContent(), "480p");
    await owner.locator(".settings-switch").click();
    await owner.waitForFunction(() => document.querySelector('.settings-switch input')?.checked === true && !document.querySelector('.settings-switch input').disabled);
    await owner.locator('[data-page="home"]').click(); await frameCheck(owner, { width: 768, height: 480 });
    await owner.close(); await new Promise(resolve => setTimeout(resolve, 6500));
    await frameCheck(viewer, { width: 390, height: 844 });
    assert.equal(errors.length, 0, errors.join("\n")); assert.equal(touchMessages, 0, "No Android input may be sent by this smoke test");
    assert.ok(ownerSession); console.log("Dynamic UI: desktop/portrait/wide actual frames, overlays, presets, dynamic off/on, rollback, viewer takeover; zero input messages");
  } finally {
    clearInterval(ticker); if (browser) await browser.close(); control.close();
    for (const server of [display, touch]) { for (const socket of server.clients) socket.terminate(); await new Promise(resolve => server.close(resolve)); }
    if (gateway) await new Promise(resolve => gateway.close(resolve)); await new Promise(resolve => api.close(resolve));
  }
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
