// SPDX-License-Identifier: GPL-3.0-only
const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const { chromium } = require("playwright-core");
const { createGateway } = require("./server");

async function main() {
  const frontendDir = process.argv[2];
  assert.ok(frontendDir, "pass a patched frontend directory");
  const root = path.resolve(os.tmpdir());
  const fixture = fs.mkdtempSync(path.join(root, "ta-login-ui-"));
  let browser, gateway, api;
  try {
    fs.writeFileSync(path.join(fixture, "openssl.cnf"), "[req]\ndistinguished_name=dn\n[dn]\n");
    execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes",
      "-config", path.join(fixture, "openssl.cnf"), "-keyout", path.join(fixture, "key.pem"),
      "-out", path.join(fixture, "cert.pem"), "-days", "1", "-subj", "/CN=localhost"], { stdio: "ignore" });
    const reservation = net.createServer();
    await new Promise((resolve) => reservation.listen(0, "127.0.0.1", resolve));
    const port = reservation.address().port;
    await new Promise((resolve) => reservation.close(resolve));
    const origin = `https://127.0.0.1:${port}`;
    api = http.createServer((_req, res) => { res.setHeader("Content-Type", "application/json"); res.end("{}"); });
    await new Promise((resolve) => api.listen(0, "127.0.0.1", resolve));
    gateway = createGateway({ frontendDir, publicOrigin: origin,
      accessFile: path.join(fixture, "access.json"),
      tls: { key: fs.readFileSync(path.join(fixture, "key.pem")), cert: fs.readFileSync(path.join(fixture, "cert.pem")) },
      targets: { api: `http://127.0.0.1:${api.address().port}`,
        display: "ws://127.0.0.1:1", touchscreen: "ws://127.0.0.1:1" } });
    await new Promise((resolve) => gateway.listen(port, "127.0.0.1", resolve));
    browser = await chromium.launch({ executablePath: process.env.EDGE_PATH ||
      "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", headless: true });
    const credentials = { origin, username: "testdriver", password: "test-chosen-password-123" };
    const page = await browser.newPage({ ignoreHTTPSErrors: true, httpCredentials: credentials,
      viewport: { width: 1440, height: 900 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error));
    // Exercise the real settings component and real gateway without Android input.
    await page.route("**/js/core/app.js", (route) => route.fulfill({ contentType: "application/javascript", body: `
      document.addEventListener('DOMContentLoaded', async () => {
        document.querySelector('#home-shell').hidden = true;
        document.querySelector('#panel-shell').hidden = false;
        document.querySelector('#page-about').hidden = true;
        document.querySelector('#page-settings').hidden = false;
        document.querySelector('#app-title').textContent = 'Settings';
        window.settings = new TAHtml.SettingsController({
          apiClient: new TAHtml.ApiClient(location.origin + '/api'),
          sidebar: document.querySelector('#settings-sidebar'), content: document.querySelector('#settings-content'),
          banner: document.querySelector('#settings-banner'), bannerText: document.querySelector('#settings-banner-text'),
          bannerAction: document.querySelector('#settings-banner-action')
        });
        await settings.setActiveSection('browserAccess');
      });` }));
    await page.goto(origin, { waitUntil: "networkidle" });
    assert.equal(await page.getByRole("checkbox", { name: "Require login" }).count(), 1);
    assert.equal(await page.getByRole("checkbox", { name: "Require login" }).isChecked(), false);
    await page.evaluate((value) => {
      const toggle = document.querySelector('[aria-label="Require login"]');
      toggle.checked = true; toggle.dispatchEvent(new Event("change", { bubbles: true }));
      document.querySelector('#access-username').value = value.username;
      document.querySelector('#access-password').value = value.password;
      document.querySelector('#access-confirm-password').value = "not-matching";
      document.querySelector('#browser-access-form').requestSubmit();
    }, credentials);
    await page.waitForFunction(() => document.querySelector('#access-status').textContent.includes('match'));
    assert.equal(await page.evaluate(async () => (await (await fetch('/api/browserAccess')).json()).enabled), false);
    await page.evaluate((value) => {
      document.querySelector('#access-confirm-password').value = value.password;
      document.querySelector('#browser-access-form').requestSubmit();
    }, credentials);
    await page.waitForFunction(() => document.querySelector('#access-current-password') !== null);
    assert.equal(await page.getByRole("checkbox", { name: "Require login" }).isChecked(), true);
    assert.equal(await page.locator('#access-password').inputValue(), "");
    const anonymous = await browser.newPage({ ignoreHTTPSErrors: true });
    assert.equal((await anonymous.request.get(origin)).status(), 401);
    await anonymous.close();
    const persisted = fs.readFileSync(path.join(fixture, "access.json"), "utf8");
    assert.ok(!persisted.includes(credentials.password));
    for (const [name, viewport] of [["desktop", { width: 1440, height: 900 }], ["mobile", { width: 390, height: 844 }]]) {
      await page.setViewportSize(viewport);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      const fieldBounds = await page.locator('#access-username').boundingBox();
      assert.ok(fieldBounds && fieldBounds.x >= 0 && fieldBounds.x + fieldBounds.width <= viewport.width);
      if (process.env.SMOKE_SCREENSHOT_DIR) await page.screenshot({
        path: path.join(process.env.SMOKE_SCREENSHOT_DIR, `browser-access-${name}.png`) });
    }
    await page.evaluate((value) => {
      const toggle = document.querySelector('[aria-label="Require login"]');
      toggle.checked = false; toggle.dispatchEvent(new Event('change', { bubbles: true }));
      document.querySelector('#access-current-password').value = value.password;
      document.querySelector('#browser-access-form').requestSubmit();
    }, credentials);
    await page.waitForFunction(() => document.querySelector('#access-current-password') === null &&
      document.querySelector('[aria-label="Require login"]')?.checked === false);
    const unlocked = await browser.newPage({ ignoreHTTPSErrors: true });
    assert.equal((await unlocked.request.get(origin)).status(), 200);
    await unlocked.close();
    assert.equal(errors.length, 0);
    console.log("Browser settings: default off, confirmation validation, chosen login, disable, desktop/mobile layout passed; no clicks sent");
  } finally {
    if (browser) await browser.close();
    for (const server of [gateway, api]) if (server) await new Promise((resolve) => server.close(resolve));
    assert.ok(fixture.startsWith(root + path.sep) && path.basename(fixture).startsWith("ta-login-ui-"));
    fs.rmSync(fixture, { recursive: true, force: true });
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
