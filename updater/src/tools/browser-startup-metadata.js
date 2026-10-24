// SPDX-License-Identifier: GPL-3.0-only
// Socket readiness is diagnostic evidence, not proof that Android handled a tap.
function startupFacts(value = {}) {
  const time = key => typeof value[key] === 'number' && Number.isFinite(value[key]) &&
    value[key] >= 0 && value[key] <= 60000 ? Math.round(value[key]) : null;
  const count = key => Number.isSafeInteger(value[key]) && value[key] >= 0 && value[key] <= 10000 ? value[key] : 0;
  const touch = time('touchOpen'), rendered = time('rendered');
  return { displayOpenMs: time('displayOpen'), touchOpenMs: touch, firstRenderedMs: rendered,
    controlGrantedMs: time('controlGranted'), pictureToTouchOpenMs: touch === null || rendered === null ? null : touch - rendered,
    pageErrors: count('pageErrors'), blockedWrites: count('blockedWrites') };
}

async function main() {
  if (process.platform !== 'win32' || process.argv.length !== 2) throw new Error('Fixed Windows browser probe required');
  const { chromium } = require('playwright-core');
  const origin = 'https://teslavm.mondriandsm.synology.me';
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 904, height: 678 } });
    const page = await context.newPage();
    let pageErrors = 0, blockedWrites = 0;
    page.on('pageerror', () => { pageErrors++; });
    await page.route(origin + '/api/**', async route => {
      const request = route.request(), endpoint = new URL(request.url()).pathname;
      if (request.method() !== 'GET' && !['/api/displaySession', '/api/displayRole'].includes(endpoint)) {
        blockedWrites++; await route.abort();
      } else await route.continue();
    });
    await page.addInitScript(() => {
      for (const key of ['DisplayRepository_isPrimaryDisplaySharedPreferencesKey', 'flutter.DisplayRepository_isPrimaryDisplaySharedPreferencesKey']) {
        localStorage.setItem(key, 'true');
      }
      const start = performance.now();
      window.__TAStartup = {};
      window.__TAMarkStartup = key => { if (window.__TAStartup[key] === undefined) window.__TAStartup[key] = performance.now() - start; };
      const NativeSocket = window.WebSocket;
      window.WebSocket = class extends NativeSocket {
        constructor(url, ...args) {
          super(url, ...args);
          const path = new URL(url, location.href).pathname;
          const key = path === '/sockets/display' ? 'displayOpen' : path === '/sockets/touchscreen' ? 'touchOpen' : null;
          if (key) this.addEventListener('open', () => window.__TAMarkStartup(key), { once: true });
        }
      };
      const observer = new MutationObserver(() => {
        if (Number(document.querySelector('#canvas')?.dataset.videoFrames) > 0) {
          window.__TAMarkStartup('rendered'); observer.disconnect();
        }
      });
      observer.observe(document, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-video-frames'] });
    });
    page.on('response', async response => {
      if (!['/api/displayState', '/api/displayRole'].includes(new URL(response.url()).pathname)) return;
      try {
        if ((await response.json()).canControl === true) await page.evaluate(() => window.__TAMarkStartup?.('controlGranted'));
      } catch { /* Navigation or closure can invalidate an otherwise harmless read. */ }
    });
    await page.goto(origin, { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForFunction(() => window.__TAStartup?.rendered !== undefined, undefined, { timeout: 30000 });
    await page.waitForFunction(() => window.__TAStartup?.touchOpen !== undefined, undefined, { timeout: 15000 }).catch(() => {});
    const facts = startupFacts({ ...await page.evaluate(() => window.__TAStartup), pageErrors, blockedWrites });
    console.log(JSON.stringify({ ...facts, inputInjected: false, savedPixels: false, globalSettingsWrites: false,
      scope: 'One Edge startup with shared writes blocked, not a Tesla/GPU reproduction or end-to-end input latency proof.' }));
  } finally { await browser.close(); }
}
if (require.main === module) main().catch(() => { console.error('Browser startup metadata unavailable; no input or shared settings changed'); process.exitCode = 1; });
module.exports = { startupFacts };
