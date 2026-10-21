// SPDX-License-Identifier: GPL-3.0-only
// Live served UI, isolated view-only authority; no settings or Android input writes.
const { chromium } = require('../runtime/preview-gateway/node_modules/playwright-core');
async function main() {
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const page = await browser.newPage({ ignoreHTTPSErrors: true, viewport: { width: 800, height: 360 } });
    const errors = []; let settingsWrites = 0;
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/api/**', async route => {
      const request = route.request(), endpoint = new URL(request.url()).pathname;
      if (request.method() === 'GET' && endpoint === '/api/displayState') {
        const response = await route.fetch(), state = await response.json();
        return route.fulfill({ response, json: { ...state, canControl: false, canResize: false } });
      }
      if (request.method() !== 'GET' && !['/api/displaySession', '/api/displayRole'].includes(endpoint)) {
        settingsWrites++; return route.abort();
      }
      return route.continue();
    });
    await page.goto('https://192.168.2.90:8443/?video=legacy', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => Number(document.querySelector('#canvas')?.dataset.videoFrames) > 0);
    await page.locator('#home-settings-button').click();
    await page.locator('[data-page="settings"]').click();
    await page.locator('#settings-sidebar button').filter({ hasText: 'Browser', exact: true }).click();
    await page.locator('#connection-hostname').waitFor(); await page.locator('#browser-access-form').waitFor();
    const labels = await page.locator('.settings-menu-label').allTextContents();
    if (labels.join(',') !== 'Display,Audio,GPS,Browser,Network,Device') throw Error('Settings menu order mismatch');
    for (const viewport of [{ width: 1280, height: 720 }, { width: 800, height: 360 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(viewport);
      const fits = await page.locator('#settings-sidebar button').evaluateAll(buttons => buttons.every(button => {
        const r = button.getBoundingClientRect(), target = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
        return r.top >= 0 && r.bottom <= innerHeight && r.left >= 0 && r.right <= innerWidth && target?.closest('button') === button;
      }));
      if (!fits) throw Error('Live menu is not visible/hit-testable');
    }
    const loginRequired = await page.getByRole('checkbox', { name: 'Require login' }).isChecked();
    if (!await page.locator('#connection-hostname').isDisabled()) throw Error('View-only connection edit enabled');
    if (await page.locator('#browser-access-form button[type=submit]').isDisabled()) throw Error('Login control incorrectly shares connection gate');
    await page.locator('#settings-sidebar button').filter({ hasText: 'Display', exact: true }).click();
    if (await page.getByText('Secondary Display Priority', { exact: true }).count()) throw Error('Removed priority control remains');
    await page.locator('[data-page="home"]').click();
    await page.waitForFunction(() => Number(document.querySelector('#canvas')?.dataset.videoFrames) > 0);
    if (errors.length || settingsWrites) throw Error('Live settings check encountered errors/writes');
    console.log(JSON.stringify({ labels, loginRequired, mergedForms: true, menuFit: true,
      picture: await page.locator('#canvas').evaluate(c => ({ frames: Number(c.dataset.videoFrames), decoder: c.dataset.videoDecoder })),
      isolatedViewOnly: true, settingsWrites, androidInputInjected: false, pageErrors: errors.length }));
  } finally { await browser.close(); }
}
if (require.main === module) main().catch(e => { console.error(e.message); process.exitCode = 1; });
