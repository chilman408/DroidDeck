// SPDX-License-Identifier: GPL-3.0-only
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const https = require("node:https");
const net = require("node:net");
const path = require("node:path");
const { chromium } = require("playwright-core");

async function main() {
  const url = new URL(process.argv[2]);
  const octets = url.hostname.split(".").map(Number);
  assert.ok(url.protocol === "https:" && net.isIP(url.hostname) === 4 &&
    (octets[0] === 10 || (octets[0] === 192 && octets[1] === 168) ||
      (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31)), "Private LAN HTTPS only");
  assert.ok(!url.username && !url.password && url.pathname === "/");
  const origin = url.origin;
  const credentials = { username: "preview-check", password: crypto.randomBytes(24).toString("hex") };
  const authorization = "Basic " + Buffer.from(`${credentials.username}:${credentials.password}`).toString("base64");
  const request = (route, headers = {}, payload) => new Promise((resolve, reject) => {
    const req = https.request(origin + route, { rejectUnauthorized: false,
      method: payload ? "POST" : "GET", headers }, (res) => {
      let body = "";
      res.on("data", (data) => { body += data; });
      res.on("end", () => resolve({ status: res.statusCode, body }));
    });
    req.on("error", reject);
    req.end(payload ? JSON.stringify(payload) : undefined);
  });
  const initial = await request("/api/browserAccess");
  assert.equal(initial.status, 200, "This probe only starts when login is off");
  assert.equal(JSON.parse(initial.body).enabled, false);
  const browser = await chromium.launch({ executablePath: process.env.EDGE_PATH ||
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", headless: true });
  try {
    const page = await browser.newPage({ ignoreHTTPSErrors: true,
      httpCredentials: { origin, ...credentials }, viewport: { width: 1440, height: 900 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error));
    for (const [file, name] of [["settings/settings-controller.js", "SettingsController"], ["shell/shell-controller.js", "ShellController"]]) {
      await page.route(`**/js/${file}`, async (route) => {
        const response = await route.fetch();
        await route.fulfill({ response, body: (await response.text()) + `\n(function () {
          const Original = TAHtml.${name}; TAHtml.${name} = class extends Original {
            constructor(options) { super(options); window.probe${name} = this; }
          };
        })();` });
      });
    }
    await page.goto(url.href, { waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => document.querySelector('#image').naturalWidth > 0 && window.probeShellController);
    await page.evaluate(async () => {
      probeShellController.setPage('settings');
      await probeSettingsController.setActiveSection('browserAccess');
    });
    assert.equal(await page.getByRole('checkbox', { name: 'Require login' }).isChecked(), false);
    await page.evaluate((value) => {
      const toggle = document.querySelector('[aria-label="Require login"]');
      toggle.checked = true; toggle.dispatchEvent(new Event('change', { bubbles: true }));
      document.querySelector('#access-username').value = value.username;
      document.querySelector('#access-password').value = value.password;
      document.querySelector('#access-confirm-password').value = value.password;
      document.querySelector('#browser-access-form').requestSubmit();
    }, credentials);
    await page.waitForFunction(() => window.probeShellController && document.querySelector('#image').naturalWidth > 0);
    await page.waitForFunction(() => document.querySelector('#access-password') === null);
    assert.equal((await request('/')).status, 401);
    assert.equal((await request('/api/health', { Authorization: authorization })).status, 200);
    await page.evaluate(async () => {
      probeShellController.setPage('settings');
      await probeSettingsController.setActiveSection('browserAccess');
    });
    assert.equal(await page.getByRole('checkbox', { name: 'Require login' }).isChecked(), true);
    await page.evaluate((value) => {
      const toggle = document.querySelector('[aria-label="Require login"]');
      toggle.checked = false; toggle.dispatchEvent(new Event('change', { bubbles: true }));
      document.querySelector('#access-current-password').value = value.password;
      document.querySelector('#browser-access-form').requestSubmit();
    }, credentials);
    await page.waitForFunction(() => document.querySelector('#access-current-password') === null &&
      document.querySelector('[aria-label="Require login"]')?.checked === false);
    assert.equal((await request('/')).status, 200);
    for (const [name, viewport] of [["desktop", { width: 1440, height: 900 }], ["mobile", { width: 390, height: 844 }]]) {
      await page.setViewportSize(viewport);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      if (process.env.SMOKE_SCREENSHOT_DIR) await page.screenshot({ path:
        path.join(process.env.SMOKE_SCREENSHOT_DIR, `live-browser-access-${name}.png`) });
    }
    assert.equal(errors.length, 0);
    console.log("Live browser settings: streaming, chosen login enabled, anonymous denied, login disabled, desktop/mobile verified; no clicks or Android input sent");
  } finally {
    // Do not leave temporary credentials enabled after a failing live check.
    try {
      const response = await request('/api/browserAccess', { Authorization: authorization });
      if (response.status === 200) {
        const state = JSON.parse(response.body);
        if (state.enabled) {
          const disabled = await request('/api/browserAccess', { Authorization: authorization,
            Origin: origin, 'Content-Type': 'application/json', 'X-CSRF-Token': state.csrfToken }, { enabled: false });
          assert.equal(disabled.status, 200, "Could not restore default-off login");
        }
      } else throw new Error("Could not verify login cleanup; check VM state before proceeding");
    } finally { await browser.close(); }
  }
}

main().catch((error) => { console.error(error.message); process.exitCode = 1; });
