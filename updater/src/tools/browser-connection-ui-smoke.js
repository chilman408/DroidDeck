// SPDX-License-Identifier: GPL-3.0-only
// Isolated UI responses only: never apply a live hostname or send Android input.
const fs = require('node:fs'), path = require('node:path');
const { publicSnapshot } = require('../runtime/browser-connection/proxy-store');
const ORIGIN = 'https://teslavm.mondriandsm.synology.me';
async function main() {
  if (process.platform !== 'win32' || process.argv.length !== 2) throw new Error('Fixed candidate UI probe required');
  const { chromium } = require('playwright-core');
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 904, height: 844 } });
    const root = 'C:/Users/Hilman/Documents/Codex/.codex-runner-temp/browser-connection-candidate-20261012c';
    for (const file of ['js/core/api-client.js', 'js/settings/settings-controller.js', 'css/app.css']) {
      await context.route(ORIGIN + '/' + file, route => route.fulfill({ status: 200, contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript',
        headers: { 'cache-control': 'no-store' }, body: fs.readFileSync(path.join(root, file)) }));
    }
    const page = await context.newPage(); let errors = 0, writes = 0, owner = true;
    let state = publicSnapshot({ version: 1, revision: 0, hostname: 'teslavm.mondriandsm.synology.me', previousHostname: null });
    page.on('pageerror', () => errors++);
    await page.addInitScript(() => {
      localStorage.setItem('DisplayRepository_isPrimaryDisplaySharedPreferencesKey', 'true');
      localStorage.setItem('flutter.DisplayRepository_isPrimaryDisplaySharedPreferencesKey', 'true');
    });
    await page.route(ORIGIN + '/api/**', async route => {
      const request = route.request(), endpoint = new URL(request.url()).pathname;
      if (endpoint === '/api/browserConnection') {
        if (request.method() === 'POST') {
          const payload = request.postDataJSON();
          if (!owner || payload.revision !== state.revision || Object.keys(payload).sort().join(',') !== 'hostname,revision') throw new Error('Invalid isolated UI save');
          state = publicSnapshot({ version: 1, revision: state.revision + 1, hostname: payload.hostname, previousHostname: state.hostname }); writes++;
        }
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(state) }); return;
      }
      if (endpoint === '/api/displayState' && request.method() === 'GET') {
        const response = await route.fetch(), body = await response.json();
        await route.fulfill({ response, json: { ...body, canControl: owner, transactionStatus: 'idle' } }); return;
      }
      if (request.method() !== 'GET' && !['/api/displaySession', '/api/displayRole'].includes(endpoint)) await route.abort();
      else await route.continue();
    });
    await page.goto(ORIGIN, { waitUntil: 'domcontentloaded' });
    await page.locator('[data-page="home"].active').waitFor({ state: 'attached' });
    await page.locator('#home-settings-button').click(); await page.locator('[data-page="settings"]').click();
    const section = page.locator('#settings-sidebar button').filter({ hasText: 'Browser connection', exact: true });
    await section.click(); const input = page.locator('#connection-hostname'); await input.waitFor();
    await page.waitForFunction(() => !document.querySelector('#connection-hostname').disabled);
    if (await input.inputValue() !== state.hostname) throw new Error('Upgrade hostname not preserved');
    for (const width of [904, 390, 320]) {
      await page.setViewportSize({ width, height: 844 });
      if (await page.locator('#settings-content').evaluate(element => [...element.querySelectorAll('input,button,.settings-tile,.settings-tile-content,.settings-tile-trailing,.settings-trailing-text')].some(child => {
        const box = child.getBoundingClientRect(); return box.x < 0 || box.right > innerWidth || (child.tagName !== 'INPUT' && child.scrollWidth > child.clientWidth + 1);
      }))) throw new Error('Connection layout exceeds viewport');
      if (await page.locator('.settings-connection-actions button').evaluateAll(buttons => buttons.some(button => button.getBoundingClientRect().height < 44))) throw new Error('Connection touch targets too small');
      await page.locator('#settings-content').screenshot({ path: 'C:/Users/Hilman/Documents/Codex/.codex-runner-temp/browser-connection-' + width + '-20261012c.png' });
    }
    await page.locator('#settings-content button').filter({ hasText: 'Use default', exact: true }).click();
    if (await input.inputValue() !== 'device.teslaandroid.com' || writes) throw new Error('Default applied implicitly');
    await input.fill('droiddeck.mondriandsm.synology.me');
    await page.locator('#settings-content button').filter({ hasText: 'Apply', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('#connection-hostname')?.value === 'droiddeck.mondriandsm.synology.me' && !document.querySelector('#connection-hostname').disabled);
    if (writes !== 1 || state.previousOrigin !== ORIGIN) throw new Error('Isolated save failed');
    owner = false;
    await page.locator('#settings-sidebar button').filter({ hasText: 'Network', exact: true }).click();
    await section.click(); await page.waitForFunction(() => document.querySelector('#connection-hostname')?.disabled);
    if (!await page.locator('#settings-content button').filter({ hasText: 'Apply', exact: true }).isDisabled()) throw new Error('Viewer can write');
    await page.locator('[data-page="home"]').click();
    await page.waitForFunction(() => Number(document.querySelector('#canvas')?.dataset.videoFrames) > 0);
    if (errors || writes !== 1) throw new Error('UI errors or duplicate save');
    console.log(JSON.stringify({ candidateUiOnly: true, defaultPresetNoImplicitSave: true, isolatedSave: 1,
      retainedPreviousAddress: true, viewerDisabled: true, desktopMobileFit: true, pageErrors: errors, liveHostnameWrites: 0 }));
  } finally { await browser.close(); }
}
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
