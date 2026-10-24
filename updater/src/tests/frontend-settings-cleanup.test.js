// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const { chromium } = require('../runtime/preview-gateway/node_modules/playwright-core');
const frontend = process.env.TA_FRONTEND_DIR;
if (!frontend) throw Error('Set TA_FRONTEND_DIR to patched frontend');

test('settings navigation and merged browser controls remain usable in short Tesla and phone viewports', async t => {
  const browser = await chromium.launch({ executablePath: process.env.EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage({ userAgent: 'Mozilla/5.0 AppleWebKit/537.36 Chrome/109.0.0.0 Safari/537.36 Tesla' });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('http://droiddeck.test/**', route => {
    const relative = new URL(route.request().url()).pathname.slice(1);
    if (!/^(css|fonts|assets)\/[A-Za-z0-9./_-]+$/.test(relative) || relative.includes('..')) return route.abort();
    const file = path.join(frontend, relative);
    if (!fs.existsSync(file)) return route.abort();
    return route.fulfill({ body: fs.readFileSync(file), contentType: relative.endsWith('.css') ? 'text/css' : undefined });
  });
  await page.evaluate(html => {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    for (const script of doc.querySelectorAll('script')) script.remove();
    const base = doc.createElement('base'); base.href = 'http://droiddeck.test/'; doc.head.prepend(base);
    document.open(); document.write(doc.documentElement.outerHTML); document.close();
  }, fs.readFileSync(path.join(frontend, 'index.html'), 'utf8'));
  for (const file of ['js/core/shared.js', 'js/display/display-renderer-manager.js', 'js/settings/settings-controller.js']) await page.addScriptTag({ path: path.join(frontend, file) });
  await page.evaluate(async () => {
    await document.fonts.ready;
    document.querySelector('#home-shell').hidden = true;
    document.querySelector('#panel-shell').hidden = false;
    for (const p of document.querySelectorAll('.panel-page')) p.hidden = p.id !== 'page-settings';
    document.querySelector('#app-title').textContent = 'Settings';
    window.state = { width: 960, height: 720, density: 180, generation: 1, resolutionPreset: 1,
      isResponsive: 1, displayControlSupported: 1, canControl: true, canResize: true,
      transactionStatus: 'idle', rearDisplayControlSupported: 1, isRearDisplayEnabled: 1,
      isRearDisplayPrioritised: 0, rearDisplayRevision: 4, displayRole: 'primary', streamFormats: ['h264'] };
    window.calls = []; window.connectionFails = false; window.accessFails = false;
    const api = { fetchDisplayState: async () => state,
      fetchSystemConfiguration: async () => ({}), fetchDeviceInfo: async () => ({ networkInfoVersion: 1, deviceInfoVersion: 1, android_addresses: [] }),
      fetchBrowserConnection: async () => {
        if (connectionFails) throw Error('generated connection failure');
        return { revision: 0, hostname: 'device.teslaandroid.com', origin: 'https://device.teslaandroid.com', defaultOrigin: 'https://device.teslaandroid.com' };
      },
      fetchBrowserAccess: async () => {
        if (accessFails) throw Error('generated access failure');
        return { enabled: false, username: '', csrfToken: 'generated-test-csrf' };
      },
      saveBrowserConnection: async payload => { calls.push({ type: 'connection', payload }); },
      saveBrowserAccess: async payload => { calls.push({ type: 'access', payload }); } };
    window.controller = { remoteDisplayState: state, controlRequestInFlight: false };
    window.settings = new TAHtml.SettingsController({ apiClient: api, displayController: controller,
      sidebar: document.querySelector('#settings-sidebar'), content: document.querySelector('#settings-content'),
      banner: document.querySelector('#settings-banner'), bannerText: document.querySelector('#settings-banner-text'),
      bannerAction: document.querySelector('#settings-banner-action') });
    await settings.initialize();
  });
  const labels = ['Display', 'Audio', 'GPS', 'Browser', 'Network', 'Device'];
  assert.deepEqual(await page.locator('.settings-menu-label').allTextContents(), labels);
  for (const viewport of [{ width: 1280, height: 720 }, { width: 1024, height: 384 },
    { width: 800, height: 360 }, { width: 390, height: 844 }, { width: 320, height: 568 }]) {
    await page.setViewportSize(viewport);
    await page.evaluate(() => settings.setActiveSection('display'));
    assert.equal(await page.getByText('Secondary Display Priority', { exact: true }).count(), 0);
    assert.equal(await page.locator('[data-rear-role] input').count(), 2);
    for (const label of labels) {
      const button = page.locator('#settings-sidebar button').filter({ hasText: label, exact: true });
      await button.scrollIntoViewIfNeeded();
      const usable = await button.evaluate(el => {
        const r = el.getBoundingClientRect(), target = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
        return r.width > 80 && r.height >= 44 && r.top >= 0 && r.bottom <= innerHeight &&
          r.left >= 0 && r.right <= innerWidth && target?.closest('button') === el;
      });
      assert.equal(usable, true, `${viewport.width}x${viewport.height}: ${label} must be visible and hit-testable`);
      await button.click();
      await page.waitForFunction(() => !document.querySelector('.settings-loading'));
    }
    await page.evaluate(() => settings.setActiveSection('browserAccess'));
    assert.equal(await page.evaluate(() => settings.activeSectionId), 'browserConnection');
    assert.equal(await page.locator('#connection-hostname').count(), 1);
    assert.equal(await page.locator('#browser-access-form').count(), 1);
    assert.equal(await page.getByRole('checkbox', { name: 'Require login' }).isChecked(), false);
    const fit = await page.evaluate(() => {
      const sidebar = document.querySelector('#settings-sidebar').getBoundingClientRect();
      const content = document.querySelector('#settings-content').getBoundingClientRect();
      return content.height > 40 && (innerWidth > 820 ? content.left >= sidebar.right : content.top >= sidebar.bottom) &&
        document.documentElement.scrollWidth <= innerWidth;
    });
    assert.equal(fit, true, 'Menu must not collapse or overlap the content');
    if (process.env.SETTINGS_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.SETTINGS_SCREENSHOT_DIR, `settings-${viewport.width}x${viewport.height}.png`) });
  }
  await page.evaluate(() => { state.canControl = false; settings._refreshConnectionAuthority(); });
  assert.equal(await page.locator('#connection-hostname').isDisabled(), true);
  assert.equal(await page.locator('#browser-access-form button[type=submit]').isDisabled(), false);
  await page.evaluate(() => { state.canControl = true; settings._refreshConnectionAuthority(); });
  await page.locator('#connection-hostname').fill('deck.example');
  await page.locator('[data-browser-connection] button[type=submit]').click();
  await page.waitForFunction(() => calls.length === 1 && !settings.connectionSettingsPending);
  assert.deepEqual(await page.evaluate(() => calls[0]), { type: 'connection', payload: { revision: 0, hostname: 'deck.example' } });
  await page.locator('#browser-access-form button[type=submit]').click();
  await page.waitForFunction(() => calls.length === 2);
  assert.deepEqual(await page.evaluate(() => calls[1]), { type: 'access', payload: { enabled: false } });
  await page.evaluate(async () => { connectionFails = true; await settings._renderActiveSection(); });
  assert.equal(await page.getByText('Browser connection unavailable', { exact: true }).count(), 1);
  assert.equal(await page.locator('#browser-access-form').count(), 1);
  await page.evaluate(async () => { connectionFails = false; accessFails = true; await settings._renderActiveSection(); });
  assert.equal(await page.getByText('Browser access settings unavailable', { exact: true }).count(), 1);
  assert.equal(await page.locator('#connection-hostname').count(), 1);
  assert.deepEqual(errors, []);
});
