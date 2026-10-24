// SPDX-License-Identifier: GPL-3.0-only
// Decode in memory only; never save signed-in Android screens or media.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const crypto = require('node:crypto');
const { chromium } = require('../runtime/preview-gateway/node_modules/playwright-core');
const { PNG } = require('../runtime/waydroid-display/node_modules/pngjs');

async function main([origin, certPath]) {
  const url = new URL(origin);
  assert.equal(url.protocol, 'https:');
  const certificate = new crypto.X509Certificate(fs.readFileSync(certPath));
  assert.equal(certificate.checkIP(url.hostname), url.hostname);
  const pin = crypto.createHash('sha256').update(certificate.publicKey.export({ type: 'spki', format: 'der' })).digest('base64');
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless: true, args: ['--ignore-certificate-errors-spki-list=' + pin] });
  try {
    for (const [label, width, height, suffix] of [['desktop', 1280, 800, ''], ['mobile-legacy', 740, 820, '?video=legacy']]) {
      const context = await browser.newContext({ viewport: { width, height } });
      try {
        const page = await context.newPage(), errors = [];
        page.on('pageerror', error => errors.push(error.name));
        await page.addInitScript(() => {
          for (const key of ['DisplayRepository_isPrimaryDisplaySharedPreferencesKey', 'flutter.DisplayRepository_isPrimaryDisplaySharedPreferencesKey']) localStorage.setItem(key, 'true');
        });
        const response = await page.goto(origin + '/' + suffix, { waitUntil: 'domcontentloaded', timeout: 20000 });
        assert.equal(response.status(), 200);
        if (await page.locator('#main-display-button').isVisible()) await page.locator('#main-display-button').click();
        await page.waitForFunction(() => Number(document.querySelector('#canvas')?.dataset.videoFrames) > 0,
          undefined, { timeout: 60000 });
        if (await page.locator('#home-audio-button').isVisible()) await page.locator('#home-audio-button').click();
        await page.evaluate(() => { window.primeAudioFromGesture?.(); window.startAudioPlayback?.(); if (window.__liveAudioEl) window.__liveAudioEl.muted = true; });
        await page.waitForFunction(() => {
          const audio = window.__liveAudioEl;
          return audio && audio.readyState >= 3 && audio.paused === false && audio.currentTime > 0 && !audio.error;
        }, undefined, { timeout: 15000 });
        const pixels = PNG.sync.read(await page.locator('#canvas').screenshot());
        let nonblack = 0;
        for (let i = 0; i < pixels.data.length; i += 4) if (pixels.data[i] > 8 || pixels.data[i + 1] > 8 || pixels.data[i + 2] > 8) nonblack++;
        const facts = await page.evaluate(() => ({ frames: Number(document.querySelector('#canvas').dataset.videoFrames),
          renderer: document.querySelector('#canvas').dataset.videoRenderer,
          audio: window.__liveAudioEl ? { ready: window.__liveAudioEl.readyState, paused: window.__liveAudioEl.paused,
            time: window.__liveAudioEl.currentTime, error: window.__liveAudioEl.error?.code ?? null } : null,
          secure: isSecureContext, overflow: document.documentElement.scrollWidth > innerWidth }));
        assert.ok(nonblack > pixels.width * pixels.height * .01, 'Blank decoded picture');
        assert.equal(facts.secure, true);
        assert.equal(facts.overflow, false);
        assert.deepEqual(errors, []);
        assert.ok(facts.audio && facts.audio.ready >= 3 && facts.audio.paused === false && facts.audio.time > 0 && facts.audio.error === null,
          JSON.stringify({ label, frames: facts.frames, renderer: facts.renderer, audio: facts.audio }));
        console.log(JSON.stringify({ label, ...facts, nonblack, certificatePinned: true, screenshotsSaved: false, mediaSaved: false }));
      } finally { await context.close(); }
    }
  } finally { await browser.close(); }
}
main(process.argv.slice(2)).catch(error => { console.error(error.message); process.exitCode = 1; });
