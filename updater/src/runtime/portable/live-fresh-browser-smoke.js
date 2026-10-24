// SPDX-License-Identifier: GPL-3.0-only
// Explicitly pin the disposable server's public certificate; never disable TLS validation globally.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const crypto = require('node:crypto');
const { chromium } = require('playwright-core');
const { PNG } = require('pngjs');

async function main(args) {
  assert.ok(args.length === 2 || args.length === 3 && args[2] === '--generated', 'Public certificate and local screenshot directory required');
  const generated = args[2] === '--generated';
  const certificate = new crypto.X509Certificate(fs.readFileSync(args[0]));
  assert.equal(certificate.checkIP('192.168.2.215'), '192.168.2.215');
  const pin = crypto.createHash('sha256').update(certificate.publicKey.export({ type: 'spki', format: 'der' })).digest('base64');
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless: true, args: ['--ignore-certificate-errors-spki-list=' + pin, '--autoplay-policy=no-user-gesture-required'] });
  try {
    for (const [label, width, height, renderer] of (generated ? [['generated', 1280, 800, 'legacy']] : [['desktop', 1280, 800, 'auto'], ['mobile-legacy', 740, 820, 'legacy']])) {
      const context = await browser.newContext({ viewport: { width, height } });
      try {
        const page = await context.newPage(), errors = [], streams = {};
        page.on('pageerror', e => errors.push(e.message));
        page.on('websocket', socket => {
          const route = new URL(socket.url()).pathname;
          streams[route] ||= { messages: 0, bytes: 0 };
          socket.on('framereceived', event => {
            streams[route].messages++;
            streams[route].bytes += Buffer.byteLength(event.payload);
          });
        });
        await page.addInitScript(() => {
          for (const key of ['DisplayRepository_isPrimaryDisplaySharedPreferencesKey', 'flutter.DisplayRepository_isPrimaryDisplaySharedPreferencesKey']) localStorage.setItem(key, 'true');
        });
        const response = await page.goto('https://192.168.2.215:8443/' + (renderer === 'legacy' ? '?video=legacy' : ''),
          { waitUntil: 'domcontentloaded', timeout: 20000 });
        assert.equal(response.status(), 200);
        if (await page.locator('#main-display-button').isVisible()) await page.locator('#main-display-button').click();
        await page.waitForFunction(() => Number(document.querySelector('#canvas')?.dataset.videoFrames) > 0,
          undefined, { timeout: 60000 }).catch(async error => {
          await page.screenshot({ path: args[1] + '/' + label + '-failure.png' });
          console.error(JSON.stringify({ label, errors, streams, state: await page.evaluate(() => ({
            canvas: document.querySelector('#canvas') ? { ...document.querySelector('#canvas').dataset } : null,
            buttons: [...document.querySelectorAll('button')].map(button => ({ id: button.id, text: button.textContent.trim().slice(0, 64) })),
            secure: isSecureContext })) }));
          throw error;
        });
        const layer = await page.locator('#touch-layer').boundingBox();
        if (layer && !generated) {
          await page.mouse.move(layer.x + layer.width / 2, layer.y + 4);
          await page.mouse.down();
          await page.mouse.move(layer.x + layer.width / 2, layer.y + layer.height * .7, { steps: 16 });
          await page.mouse.up();
        }
        if (await page.locator('#home-audio-button').isVisible()) await page.locator('#home-audio-button').click();
        await page.evaluate(() => { window.primeAudioFromGesture?.(); window.startAudioPlayback?.(); if (window.__liveAudioEl) window.__liveAudioEl.muted = true; });
        await page.waitForTimeout(3000);
        let tone = null, changedPixels = null;
        if (generated) {
          const box = await page.locator('#canvas').boundingBox();
          await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
          await page.evaluate(async () => {
            const element = window.__liveAudioEl;
            if (!element) throw Error('Live audio element unavailable');
            const context = new AudioContext({ sampleRate: 48000 }), source = context.createMediaElementSource(element);
            const analyser = context.createAnalyser(), mute = context.createGain();
            analyser.fftSize = 4096; analyser.smoothingTimeConstant = 0; mute.gain.value = 0;
            source.connect(analyser); analyser.connect(mute); mute.connect(context.destination);
            element.muted = false; element.volume = 1; await context.resume();
            window.__freshTone = { context, analyser };
          });
          await page.waitForTimeout(1200);
          let matches = 0;
          const samples = [];
          for (let i = 0; i < 10; i++) {
            await page.waitForTimeout(200);
            const value = await page.evaluate(() => {
              const { context, analyser } = window.__freshTone, bins = new Float32Array(analyser.frequencyBinCount);
              analyser.getFloatFrequencyData(bins);
              let index = 1;
              for (let i = 2; i < bins.length; i++) if (bins[i] > bins[index]) index = i;
              return { hz: index * context.sampleRate / analyser.fftSize, db: Number.isFinite(bins[index]) ? bins[index] : -200 };
            });
            if (Math.abs(value.hz - 1000) < 35 && value.db > -75) matches++;
            samples.push(value);
          }
          tone = { matches, samples, savedAudio: false, outputMuted: true };
          assert.ok(matches >= 8, 'Generated Android-to-browser 1000 Hz tone not verified: ' + JSON.stringify(tone));
          assert.ok(Number(await page.locator('#canvas').getAttribute('data-video-frames')) > 8, 'Generated video did not advance');
          const before = PNG.sync.read(await page.locator('#canvas').screenshot());
          await page.waitForTimeout(400);
          const after = PNG.sync.read(await page.locator('#canvas').screenshot());
          assert.equal(before.data.length, after.data.length);
          changedPixels = 0;
          for (let i = 0; i < before.data.length; i += 4) {
            if (Math.abs(before.data[i] - after.data[i]) + Math.abs(before.data[i + 1] - after.data[i + 1]) +
                Math.abs(before.data[i + 2] - after.data[i + 2]) > 75) changedPixels++;
          }
          assert.ok(changedPixels > 1000, 'Generated Android picture did not visibly move');
        }
        const pixels = PNG.sync.read(await page.locator('#canvas').screenshot());
        let nonblack = 0;
        for (let i = 0; i < pixels.data.length; i += 4) if (pixels.data[i] > 8 || pixels.data[i + 1] > 8 || pixels.data[i + 2] > 8) nonblack++;
        assert.ok(nonblack > pixels.width * pixels.height * .01, 'Blank decoded Android picture');
        const facts = await page.evaluate(() => ({ video: { ...document.querySelector('#canvas').dataset },
          audio: window.__liveAudioEl ? { time: window.__liveAudioEl.currentTime, ready: window.__liveAudioEl.readyState,
            paused: window.__liveAudioEl.paused, error: window.__liveAudioEl.error?.code ?? null } : null,
          overflow: document.documentElement.scrollWidth > innerWidth, secure: isSecureContext }));
        assert.equal(facts.secure, true);
        assert.equal(facts.overflow, false);
        assert.deepEqual(errors, []);
        await page.screenshot({ path: args[1] + '/' + label + '.png' });
        console.log(JSON.stringify({ label, width: pixels.width, height: pixels.height, nonblack, ...facts, tone, changedPixels, streams,
          certificatePinned: true, publicBrowserTrustClaimed: false, pngFallback: false }));
      } finally { await context.close(); }
    }
  } finally { await browser.close(); }
}
if (require.main === module) main(process.argv.slice(2)).catch(e => { console.error(e.message); process.exitCode = 1; });
