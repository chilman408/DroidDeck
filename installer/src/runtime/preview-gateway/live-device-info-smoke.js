// SPDX-License-Identifier: GPL-3.0-only
const assert = require('node:assert/strict'), path = require('node:path');
const { chromium } = require('playwright-core');
const origin = 'https://teslavm.mondriandsm.synology.me';
const network = process.argv.includes('--network');
const rootSelector = network ? '[data-vm-network-info]' : '[data-vm-device-info]';
async function main() {
  const browser = await chromium.launch({ executablePath: process.env.EDGE_PATH ||
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  const contexts = [], errors = [], writes = [], results = [];
  try {
    const anchor = await browser.newContext(); contexts.push(anchor);
    const response = await anchor.request.get(origin + '/api/deviceInfo'); assert.equal(response.status(), 200);
    const info = await response.json(); assert.equal(info.deviceInfoVersion, 1);
    assert.ok(info.cpu_cores > 0); assert.ok(info.memory_total_bytes > 0); assert.equal(info.cpu_temperature, null);
    if (network) { assert.equal(info.networkInfoVersion, 1); assert.equal(info.lan_stream_origin, 'https://192.168.2.90:8443'); assert.ok(info.android_addresses.length > 0); }
    const created = await anchor.request.post(origin + '/api/displaySession', {
      headers: { Origin: origin, 'Content-Type': 'application/json' }, data: {},
    }); assert.equal(created.status(), 200); const session = await created.json();
    const state = await (await anchor.request.get(origin + '/api/displayState', {
      headers: { 'X-Display-Session': session.token },
    })).json();
    for (const [name, width, height] of [['desktop', state.width, state.height], ['mobile', 320, 740]]) {
      const context = name === 'desktop' ? anchor : await browser.newContext();
      if (context !== anchor) contexts.push(context);
      const page = await context.newPage(); await page.setViewportSize({ width, height });
      page.on('pageerror', error => errors.push(error.message));
      await page.route(origin + '/api/**', async route => {
        const request = route.request();
        if (request.method() === 'POST' && new URL(request.url()).pathname !== '/api/displaySession') {
          writes.push(new URL(request.url()).pathname); await route.abort();
        } else await route.continue();
      });
      await page.goto(origin + '/', { waitUntil: 'domcontentloaded' });
      await page.waitForFunction(() => document.querySelector('#canvas')?.dataset.videoState === 'ready', undefined, { timeout: 30000 });
      await page.locator('#home-settings-button').click(); await page.locator('[data-page="settings"]').click();
      await page.locator('#settings-sidebar button').filter({ hasText: network ? 'Network' : 'Device', exact: true }).click();
      await page.locator(rootSelector).waitFor();
      const row = title => page.locator(rootSelector + ' .settings-tile').filter({
        has: page.locator('.settings-tile-title', { hasText: new RegExp('^' + title + '$') }),
      }).locator('.settings-trailing-text');
      if (network) {
        assert.equal(await row('Browser connection').textContent(), origin);
        assert.equal(await row('LAN streaming').textContent(), info.lan_stream_origin);
        assert.equal(await row('Android network').textContent(), 'Waydroid NAT');
        assert.equal(await row('Offline routing').textContent(), 'Unavailable');
        for (const entry of info.android_addresses) assert.equal(await row('Android IPv4 \\(' + entry.interface + '\\)').textContent(), entry.address);
        assert.equal(await page.locator(rootSelector + ' input, ' + rootSelector + ' button').count(), 0);
      } else {
        assert.equal(await row('CPU cores').textContent(), String(info.cpu_cores));
        assert.equal(await row('CPU Temperature').textContent(), 'Unavailable');
        assert.equal(await row('Host kernel').textContent(), info.kernel_release);
      }
      const overflow = await page.locator(rootSelector).evaluate(root => Array.from(
        root.querySelectorAll('.settings-tile-content, .settings-tile-trailing')).filter(element =>
        element.scrollWidth > element.clientWidth + 1).map(element => element.textContent));
      assert.deepEqual(overflow, []);
      if (process.env.SMOKE_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.SMOKE_SCREENSHOT_DIR, (network ? 'network-info-' : 'device-info-') + name + '.png') });
      await page.locator('[data-page="home"]').click();
      await page.waitForFunction(() => document.querySelector('#canvas')?.dataset.videoState === 'ready', undefined, { timeout: 30000 });
      results.push({ name, width, height, overflow });
    }
    assert.deepEqual(errors, []); assert.deepEqual(writes, []);
    console.log(JSON.stringify({ views: results, cpuCores: info.cpu_cores, memoryBytes: info.memory_total_bytes,
      kernel: info.kernel_release, unknownSensorsHonest: true, errors, writes }));
  } finally { for (const context of contexts) await context.close(); await browser.close(); }
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
