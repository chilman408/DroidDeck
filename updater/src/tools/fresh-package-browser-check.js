// SPDX-License-Identifier: GPL-3.0-only
// New disposable guest only; native-touch mode presses the generated fixture.
const fs = require('node:fs');
const { X509Certificate, createHash } = require('node:crypto');
const assert = require('node:assert/strict');
const { chromium } = require('../runtime/preview-gateway/node_modules/playwright-core');
const { PNG } = require('../runtime/waydroid-display/node_modules/pngjs');

async function main(args) {
  if (args.length !== 1 && !(args.length === 2 && ['--generated-media', '--native-touch'].includes(args[1]))) throw Error('PUBLIC_CERT [--generated-media|--native-touch] required');
  const generated = args[1] === '--generated-media', touch = args[1] === '--native-touch';
  const certificate = new X509Certificate(fs.readFileSync(args[0]));
  assert.ok(certificate.checkIP('192.168.2.215'));
  const spki = createHash('sha256').update(certificate.publicKey.export({ type: 'spki', format: 'der' })).digest('base64');
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless: true, args: ['--ignore-certificate-errors-spki-list=' + spki, '--autoplay-policy=no-user-gesture-required'] });
  try {
    const results = [];
    for (const viewport of (generated || touch ? [{ width: 1280, height: 800 }] : [{ width: 1280, height: 800 }, { width: 740, height: 820 }])) {
      const context = await browser.newContext({ viewport });
      try {
        const page = await context.newPage(), errors = [], sockets = [];
        page.on('pageerror', error => errors.push(error.message));
        page.on('websocket', socket => {
          const item = { path: new URL(socket.url()).pathname, messages: 0, bytes: 0, closed: false };
          sockets.push(item);
          socket.on('framereceived', ({ payload }) => { item.messages++; item.bytes += Buffer.byteLength(payload); });
          socket.on('close', () => { item.closed = true; });
        });
        const response = await page.goto('https://192.168.2.215:8443/?video=legacy', { waitUntil: 'domcontentloaded' });
        assert.equal(response.status(), 200);
        await page.waitForFunction(() => typeof window.startAudioFromGesture === 'function');
        // A trusted browser gesture is required by the product audio contract.
        // Use a test-only DOM target so nothing is injected into Android.
        await page.evaluate(() => {
          const button = document.createElement('button');
          button.id = 'droiddeck-test-gesture'; button.textContent = 'Audio test';
          button.addEventListener('click', () => window.startAudioFromGesture());
          button.style.cssText = 'position:fixed;top:0;right:0;z-index:2147483647';
          document.body.appendChild(button);
        });
        await page.locator('#droiddeck-test-gesture').click();
        await page.locator('#droiddeck-test-gesture').evaluate(element => element.remove());
        await page.waitForFunction(() => Number(document.getElementById('canvas')?.dataset.videoFrames || 0) > 0,
          undefined, { timeout: 60000 });
        await page.waitForTimeout(2500);
        await page.waitForFunction(() => [...document.querySelectorAll('audio')].some(element =>
          element.readyState >= 3 && !element.paused && !element.error), undefined, { timeout: 30000 });
        let media;
        if (touch) {
          console.error('Disposable native-touch probe ready; open ONLY the generated fixture in Jelly without pressing Play. Input idle for 75 seconds');
          await page.waitForTimeout(75000);
          const layer = await page.locator('#touch-layer').boundingBox();
          assert.ok(layer && layer.width > 100 && layer.height > 100);
          await page.mouse.click(layer.x + layer.width / 2, layer.y + layer.height / 2);
          assert.ok(sockets.some(socket => socket.path.includes('touchscreen') && !socket.closed), 'Input socket did not survive idle');
          console.error('Generated Play pressed through browser after 75 seconds idle; independently inspect bridge fixture gesture/playback now');
          await page.waitForTimeout(15000);
        }
        if (generated) {
          await page.evaluate(async () => {
            const audio = document.querySelector('audio'), ctx = new AudioContext();
            const source = ctx.createMediaElementSource(audio), analyser = ctx.createAnalyser(), mute = ctx.createGain();
            analyser.fftSize = 2048; mute.gain.value = 0;
            source.connect(analyser); analyser.connect(mute); mute.connect(ctx.destination);
            window.__droiddeckToneAnalyser = analyser;
            await ctx.resume();
          });
          // Open the generated Jelly page AFTER role/geometry startup settles.
          // A first connection can legitimately return Android to its home UI.
          console.error('Generated browser ready; launch and play ONLY the bridge-only fixture in Jelly now');
          await page.waitForTimeout(20000);
          const first = Number(await page.locator('#canvas').getAttribute('data-video-frames'));
          await page.waitForFunction(before => Number(document.getElementById('canvas').dataset.videoFrames) > before + 8,
            first, { timeout: 15000 });
          const a = PNG.sync.read(await page.locator('#canvas').screenshot());
          await page.waitForTimeout(400);
          const b = PNG.sync.read(await page.locator('#canvas').screenshot());
          assert.equal(a.data.length, b.data.length);
          let changed = 0;
          for (let i = 0; i < a.data.length; i += 4) {
            if (Math.max(...[0, 1, 2].map(offset => Math.abs(a.data[i + offset] - b.data[i + offset]))) > 24) changed++;
          }
          const tone = await page.evaluate(async () => {
            const analyser = window.__droiddeckToneAnalyser, samples = [];
            if (!analyser) throw Error('Generated-only analyser missing');
            const bins = new Float32Array(analyser.frequencyBinCount);
            for (let attempt = 0; attempt < 10; attempt++) {
              analyser.getFloatFrequencyData(bins);
              let index = 1;
              for (let i = 2; i < bins.length; i++) if (bins[i] > bins[index]) index = i;
              samples.push({ frequencyHz: index * analyser.context.sampleRate / analyser.fftSize, db: bins[index] });
              await new Promise(resolve => setTimeout(resolve, 100));
            }
            return samples;
          });
          assert.ok(changed > 1000, 'Generated Android motion did not reach browser');
          assert.equal(tone.filter(sample => sample.frequencyHz > 950 && sample.frequencyHz < 1050 && sample.db > -85).length, 10,
            'Generated Android 1000Hz tone did not reach browser: ' + JSON.stringify(tone));
          media = { changedPixels: changed, tone, browserOutputMuted: true, recordedAudioSaved: false };
        }
        // The renderer transfers control to OffscreenCanvas. Inspect composited
        // canvas pixels in memory, not a forbidden second rendering context.
        const picture = PNG.sync.read(await page.locator('#canvas').screenshot());
        let nonblack = 0;
        for (let i = 0; i < picture.data.length; i += 4) {
          if (Math.max(picture.data[i], picture.data[i + 1], picture.data[i + 2]) > 24) nonblack++;
        }
        const state = await page.evaluate(() => {
          const canvas = document.getElementById('canvas');
          const audio = [...document.querySelectorAll('audio')].map(element => ({ ready: element.readyState,
            playing: !element.paused, error: element.error?.code || null, time: element.currentTime }));
          return { frames: Number(canvas.dataset.videoFrames), decoder: canvas.dataset.videoDecoder,
            width: canvas.width, height: canvas.height, audio, secureContext: isSecureContext,
            sourceWidth: Number(canvas.dataset.videoWidth), sourceHeight: Number(canvas.dataset.videoHeight),
            overflow: document.documentElement.scrollWidth > innerWidth };
        });
        state.nonblack = nonblack;
        assert.ok(state.nonblack > 10000, 'Actual decoded Android canvas is blank: ' + JSON.stringify(state));
        assert.equal(state.secureContext, true);
        assert.equal(state.overflow, false);
        assert.ok(state.audio.some(item => item.ready >= 3 && item.playing && item.error === null), 'No playing browser audio route');
        assert.deepEqual(errors, []);
        results.push({ viewport, ...state, sockets, ...(media ? { generatedMedia: media } : {}) });
      } finally { await context.close(); }
    }
    console.log(JSON.stringify({ results, explicitPublicKeyTrust: true, androidInputInjected: touch,
      ...(touch ? { inputIdleSeconds: 75, independentAndroidFixtureInspectionRequired: true } : {}),
      nativeTouchVerified: false,
      screenshotsOrRecordingsSaved: false, numericalAvSyncVerified: false }));
  } finally { await browser.close(); }
}
if (require.main === module) main(process.argv.slice(2)).catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { main };
