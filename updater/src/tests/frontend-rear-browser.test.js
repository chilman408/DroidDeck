// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), os = require("node:os"), path = require("node:path"), http = require("node:http");
const { chromium } = require("../runtime/preview-gateway/node_modules/playwright-core");
const { WebSocketServer } = require("../runtime/preview-gateway/node_modules/ws");
const { createGateway } = require("../runtime/preview-gateway/server");
const { createDisplayControl } = require("../runtime/preview-gateway/display-control");
const { createRearDisplaySettings } = require("../runtime/preview-gateway/rear-display-settings");
const { createDeviceInfoProvider } = require("../runtime/preview-gateway/device-info");
const frontend = process.env.TA_FRONTEND_DIR;
if (!frontend) throw new Error("Set TA_FRONTEND_DIR to the patched frontend");

test("two real browsers select roles, wait for retirement, preserve streams and use only preferred geometry", async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "ta-rear-browser-"));
  const api = http.createServer((_req, res) => res.end("{}"));
  await new Promise(resolve => api.listen(0, "127.0.0.1", resolve));
  const backend = new WebSocketServer({ host: "127.0.0.1", port: 0 });
  await new Promise(resolve => backend.once("listening", resolve));
  let state = { width: 852, height: 676, density: 180, resolutionPreset: 1, isResponsive: 1, recoveryRequired: false };
  const resizes = []; let gateway, finish, entered, retirements = 0;
  const retirementStarted = new Promise(resolve => { entered = resolve; });
  const control = createDisplayControl({ rearSettings: createRearDisplaySettings(path.join(directory, "rear.json")),
    retireAuthority: (...args) => gateway.retireDisplayAuthority(...args),
    helper: { inspect: async () => state, resize: async desired => { resizes.push(desired); return state = { ...desired, recoveryRequired: false }; } } });
  await control.initialize();
  const target = "ws://127.0.0.1:" + backend.address().port;
  gateway = createGateway({ frontendDir: frontend, displayControl: control,
    deviceInfoProvider: createDeviceInfoProvider({ lanOrigin: "https://192.168.2.90:8443", system: {
      hostname: () => "vm", cpus: () => [], totalmem: () => 8 * 1024 ** 3, freemem: () => 1, uptime: () => 1,
      release: () => "kernel", networkInterfaces: () => ({ eth0: [{ family: "IPv4", address: "192.168.240.112", internal: false }] }) } }),
    retireTouch: async () => {
    retirements++; await new Promise(resolve => { finish = resolve; entered(); }); return state;
  }, targets: { api: "http://127.0.0.1:" + api.address().port, display: target, touchscreen: target } });
  await new Promise(resolve => gateway.listen(0, "127.0.0.1", resolve));
  const origin = "http://127.0.0.1:" + gateway.address().port;
  const browser = await chromium.launch({ executablePath: process.env.EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", headless: true });
  t.after(async () => {
    finish?.(); await browser.close();
    for (const socket of backend.clients) socket.terminate();
    await new Promise(resolve => gateway.close(resolve));
    await new Promise(resolve => backend.close(resolve)); await new Promise(resolve => api.close(resolve));
    assert.ok(directory.startsWith(path.resolve(os.tmpdir()) + path.sep));
    fs.rmSync(directory, { recursive: true, force: true });
  });
  const errors = [], pages = [];
  for (const viewport of [{ width: 1280, height: 800 }, { width: 390, height: 844 }]) {
    const context = await browser.newContext({ viewport }), page = await context.newPage(); pages.push(page);
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(origin + "/css/app.css");
    await page.setContent('<link rel="stylesheet" href="/css/app.css"><div id="home-stage"></div><div class="settings-content" id="content"></div><div id="banner"><span id="banner-text"></span><button id="action"></button></div>');
    for (const file of ["js/core/shared.js", "js/core/api-client.js", "js/display/display-controller.js", "js/settings/settings-controller.js"]) {
      await page.addScriptTag({ path: path.join(frontend, file) });
    }
    await page.evaluate(async () => {
      window.api = new TAHtml.ApiClient("/api"); await api.openDisplaySession();
      window.controller = new TAHtml.DisplayController({ apiClient: api,
        rendererManager: { applyDisplaySize() {}, ensureDisplaySocket: async () => {}, closeDisplaySocket() {} },
        flavor: { displayWebSocket: location.origin.replace("http:", "ws:") + "/sockets/display" } });
      for (const key of TAHtml.constants.DISPLAY_PREF_KEYS) localStorage.setItem(key, 'true');
      await controller._registerStoredRole(await api.fetchDisplayState());
      window.stream = new WebSocket(api.socketUrl(location.origin.replace("http:", "ws:") + "/sockets/display"));
      await new Promise((resolve, reject) => { stream.onopen = resolve; stream.onerror = reject; });
      controller.remoteDisplayState = await api.fetchDisplayState(); controller.isPaused = true;
      controller.display = { viewSize: { width: innerWidth, height: innerHeight }, adjustedSize: controller.remoteDisplayState };
      window.settings = new TAHtml.SettingsController({ apiClient: api, displayController: controller,
        content: document.querySelector("#content"), bannerAction: document.querySelector("#action"),
        banner: document.querySelector("#banner"), bannerText: document.querySelector("#banner-text") });
      settings.activeSectionId = "display";
      controller.onControlStateChange = state => settings.refreshDisplayControl(state);
      await settings._renderActiveSection();
    });
  }
  const [main, rear] = pages;
  assert.equal(await main.locator('[data-rear-role] input[value="primary"]').isChecked(), true);
  assert.equal(await rear.locator('[data-rear-role] input[value="rear"]').isChecked(), true);
  await main.locator('[data-rear-role] label').filter({ hasText: "Primary Display" }).click();
  await rear.locator('[data-rear-role] label').filter({ hasText: "Secondary Display" }).click();
  await main.waitForFunction(() => controller.remoteDisplayState.displayRole === "primary" && !settings.rearSettingsPending);
  await rear.waitForFunction(() => controller.remoteDisplayState.displayRole === "rear" && !settings.rearSettingsPending);
  assert.equal(await rear.locator('[data-rear-shared] input').isDisabled(), true);
  await main.locator('[data-rear-shared]').first().click();
  await main.waitForFunction(() => document.querySelectorAll("[data-rear-shared]").length === 1 && !settings.rearSettingsPending);
  await rear.locator('[data-rear-role] label').filter({ hasText: 'Primary Display' }).click();
  await retirementStarted;
  assert.equal(retirements, 1); finish();
  await rear.waitForFunction(() => !settings.rearSettingsPending && controller.remoteDisplayState.canControl);
  await main.evaluate(() => controller.refreshControlState());
  assert.equal(await main.locator('[data-rear-role] input[value="rear"]').isChecked(), true);
  assert.equal(await main.evaluate(() => controller.remoteDisplayState.canControl), false);
  for (const page of pages) assert.equal(await page.evaluate(() => stream.readyState), 1);
  const returnStarted = new Promise(resolve => { entered = resolve; });
  await main.locator('[data-rear-role] label').filter({ hasText: 'Primary Display' }).click();
  await returnStarted; assert.equal(retirements, 2); finish();
  await main.waitForFunction(() => !settings.rearSettingsPending && controller.remoteDisplayState.canControl);
  await rear.evaluate(() => controller.refreshControlState());
  assert.equal(await rear.locator('[data-rear-role] input[value="rear"]').isChecked(), true);
  const priorityStarted = new Promise(resolve => { entered = resolve; });
  // Exercise retained protocol compatibility without restoring the removed UI.
  await main.evaluate(() => { void controller.updateRearSettings({ revision: controller.remoteDisplayState.rearDisplayRevision, enabled: true, prioritised: true }); });
  await main.waitForFunction(() => controller.controlRequestInFlight);
  await priorityStarted;
  await rear.evaluate(() => controller.refreshControlState());
  assert.equal(retirements, 3);
  assert.equal(await rear.locator('[data-rear-role] input').first().isDisabled(), true);
  for (const page of pages) assert.equal(await page.evaluate(() => stream.readyState), 1);
  finish();
  await main.waitForFunction(() => !settings.rearSettingsPending && !controller.remoteDisplayState.canControl);
  await rear.evaluate(() => controller.refreshControlState());
  assert.equal(await rear.evaluate(() => controller.remoteDisplayState.canResize), true);
  assert.equal(await main.locator('[data-rear-shared] input').first().isDisabled(), true);
  await main.evaluate(async () => { await api.postDisplayRole('rear'); await controller.refreshControlState(); });
  assert.equal(await main.locator('[data-rear-role] input[value="rear"]').isChecked(), true);
  const sameRoleStarted = new Promise(resolve => { entered = resolve; });
  await main.locator('[data-rear-role] label').filter({ hasText: 'Secondary Display' }).click();
  await sameRoleStarted; assert.equal(retirements, 4); finish();
  await main.waitForFunction(() => !settings.rearSettingsPending && controller.remoteDisplayState.canControl);
  await rear.evaluate(() => controller.refreshControlState());
  const restoreRearStarted = new Promise(resolve => { entered = resolve; });
  await rear.locator('[data-rear-role] label').filter({ hasText: 'Secondary Display' }).click();
  await restoreRearStarted; assert.equal(retirements, 5); finish();
  await rear.waitForFunction(() => !settings.rearSettingsPending && controller.remoteDisplayState.canControl);
  await main.evaluate(() => controller.refreshControlState());
  for (const page of pages) assert.equal(await page.evaluate(() => stream.readyState), 1);
  for (const page of pages) assert.equal(await page.evaluate(() => stream.readyState), 1);
  await main.evaluate(async () => { controller.isPaused = false; controller.resize.pendingViewSize = { width: 1200, height: 700 }; await controller._sendControlledResize(); });
  assert.equal(resizes.length, 0);
  await rear.evaluate(async () => { controller.isPaused = false; controller.resize.pendingViewSize = { width: 390, height: 844 }; await controller._sendControlledResize(); });
  assert.equal(resizes.length, 1); assert.equal(state.width, 378); assert.equal(state.height, 672);
  for (const page of pages) {
    await page.evaluate(() => controller.refreshControlState());
    const overflow = await page.evaluate(() => [...document.querySelectorAll("[data-rear-role] label")].some(label => {
      const rect = label.getBoundingClientRect(); return rect.right > innerWidth || rect.left < 0;
    }));
    assert.equal(overflow, false);
    if (process.env.REAR_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.REAR_SCREENSHOT_DIR,
      page === main ? "rear-settings-desktop.png" : "rear-settings-mobile.png") });
    if (page === rear) {
      await page.setViewportSize({ width: 320, height: 844 });
      assert.equal(await page.evaluate(() => [...document.querySelectorAll('[data-display-role-tile] label')].some(label => {
        const rect = label.getBoundingClientRect(); return rect.right > innerWidth || rect.left < 0;
      })), false);
    }
    await page.evaluate(async () => { settings.activeSectionId = "network"; await settings._renderActiveSection(); });
    assert.equal(await page.locator("[data-vm-network-info] input, [data-vm-network-info] button").count(), 0);
    assert.ok((await page.locator("#content").textContent()).includes("192.168.240.112"));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    if (process.env.REAR_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.REAR_SCREENSHOT_DIR,
      page === main ? "network-settings-desktop.png" : "network-settings-mobile.png") });
  }
  assert.equal(resizes.length, 1, "Network diagnostics must not mutate Android geometry");
  assert.deepEqual(errors, []);
});
