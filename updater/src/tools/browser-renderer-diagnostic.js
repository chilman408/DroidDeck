// SPDX-License-Identifier: GPL-3.0-only
// Selected browser/decoder metadata only; screenshot pixels remain in RAM.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const crypto = require('node:crypto');
const https = require('node:https');
const { chromium } = require('../runtime/preview-gateway/node_modules/playwright-core');
const { PNG } = require('../runtime/waydroid-display/node_modules/pngjs');

async function main([origin, certPath, mode = 'webcodecs', policyPath]) {
  assert.ok(['webcodecs', 'legacy', 'saved-webcodecs'].includes(mode));
  const certificate = new crypto.X509Certificate(fs.readFileSync(certPath));
  assert.equal(certificate.checkIP(new URL(origin).hostname), new URL(origin).hostname);
  const pin = crypto.createHash('sha256').update(certificate.publicKey.export({ type: 'spki', format: 'der' })).digest('base64');
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless: true, args: ['--ignore-certificate-errors-spki-list=' + pin] });
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const page = await context.newPage();
    if (policyPath) {
      const policy = fs.readFileSync(policyPath);
      const original = await new Promise((resolve, reject) => {
        https.get(origin + '/js/display/display-renderer-manager.js', { ca: fs.readFileSync(certPath) }, response => {
          if (response.statusCode !== 200) { response.resume(); reject(new Error('Renderer asset unavailable')); return; }
          const chunks = []; let size = 0;
          response.on('data', chunk => {
            size += chunk.length;
            if (size > 262144) response.destroy(new Error('Renderer asset exceeded bound'));
            else chunks.push(chunk);
          });
          response.on('error', reject);
          response.on('end', () => resolve(Buffer.concat(chunks)));
        }).on('error', reject);
      });
      await page.route(origin + '/js/display/display-renderer-manager.js', async route => {
        await route.fulfill({ status: 200, body: Buffer.concat([original, Buffer.from('\n;\n'), policy]),
          headers: { 'cache-control': 'no-store', 'content-type': 'text/javascript; charset=utf-8' } });
      });
    }
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.name));
    await page.addInitScript(() => {
      for (const key of ['DisplayRepository_isPrimaryDisplaySharedPreferencesKey',
        'flutter.DisplayRepository_isPrimaryDisplaySharedPreferencesKey']) localStorage.setItem(key, 'true');
      window.__decoderErrors = [];
      const NativeWorker = window.Worker;
      window.Worker = class extends NativeWorker {
        constructor(url, options) {
          super(url, options);
          if (String(url).includes('h264-webcodecs')) {
            this.addEventListener('error', event => window.__decoderErrors.push(String(event.message).slice(0, 200)));
            this.addEventListener('message', event => {
              if (event.data?.error) window.__decoderErrors.push(String(event.data.error).slice(0, 200));
            });
          }
        }
      };
    });
    if (mode === 'saved-webcodecs') await page.addInitScript(() => localStorage.setItem('TeslaAndroid_browserH264Renderer_v1', 'webcodecs'));
    assert.equal((await page.goto(origin + (mode === 'saved-webcodecs' ? '/' : '/?video=' + mode),
      { waitUntil: 'domcontentloaded', timeout: 20000 })).status(), 200);
    if (await page.locator('#main-display-button').isVisible()) await page.locator('#main-display-button').click();
    let received = true;
    try { await page.waitForFunction(() => Number(document.querySelector('#canvas')?.dataset.videoFrames) >= 3,
      undefined, { timeout: 30000 }); }
    catch { received = false; }
    const facts = await page.evaluate(() => {
      const canvas = document.querySelector('#canvas');
      return { secure: isSecureContext, webCodecsAvailable: typeof VideoDecoder === 'function',
        selected: window.TAHtml?.browserRenderer?.choice(), effective: window.TAHtml?.browserRenderer?.effectiveChoice(),
        options: window.TAHtml?.browserRenderer?.options().map(option => option.value),
        frames: Number(canvas?.dataset.videoFrames || 0), state: canvas?.dataset.videoState,
        decoder: canvas?.dataset.videoDecoder, errors: window.__decoderErrors.slice(0, 5) };
    });
    const pixels = PNG.sync.read(await page.locator('#canvas').screenshot());
    let nonblack = 0;
    for (let i = 0; i < pixels.data.length; i += 4) if (pixels.data[i] > 8 || pixels.data[i + 1] > 8 || pixels.data[i + 2] > 8) nonblack++;
    console.log(JSON.stringify({ host: new URL(origin).hostname, mode, received, ...facts, pageErrors,
      nonblack, pictureVisible: nonblack > pixels.width * pixels.height * .01,
      certificatePinned: true, screenshotsSaved: false, mediaSaved: false }));
    if (policyPath) {
      assert.equal(received, true); assert.ok(nonblack > pixels.width * pixels.height * .01);
      assert.equal(facts.effective, 'legacy'); assert.equal(facts.options.includes('webcodecs'), false);
      assert.deepEqual(pageErrors, []);
    }
  } finally { await browser.close(); }
}
main(process.argv.slice(2)).catch(error => { console.error(error.message); process.exitCode = 1; });
