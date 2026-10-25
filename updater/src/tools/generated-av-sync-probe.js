// SPDX-License-Identifier: GPL-3.0-only
// Generated flashes/tones only; temporary instrumentation never changes live frontend files.
const crypto = require('node:crypto');
const fs = require('node:fs/promises');
const ORIGIN = 'https://teslavm.mondriandsm.synology.me';
const WORKER_SHA = 'f34045ad9d0c10aed19bb9c5d2e621dd96779d56c74e5040e338a09409acb280';
let probeStage = 'initialization';
function instrumentWorker(bytes) {
  if (!Buffer.isBuffer(bytes) || crypto.createHash('sha256').update(bytes).digest('hex') !== WORKER_SHA) {
    throw new Error('Reviewed legacy worker required');
  }
  const source = bytes.toString(), anchor = '  self.postMessage({ frameRendered: true, width, height });';
  if (source.split(anchor).length !== 2) throw new Error('Fixed render hook unavailable');
  return source.replace(anchor, `  let syncSum = 0, syncCount = 0;
  for (let row = Math.floor(image.height * .30); row < image.height * .55; row += 8) {
    for (let col = Math.floor(image.width * .30); col < image.width * .70; col += 8) {
      syncSum += data[row * image.width + col]; syncCount++;
    }
  }
  self.postMessage({ syncFlash: syncCount > 0 && syncSum / syncCount > 190 });
${anchor}`);
}
function syncFacts(audio, video, latency = {}) {
  const valid = values => Array.isArray(values) && values.length === 6 && values.every((value, index) =>
    typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 35000 &&
    (!index || value - values[index - 1] >= 700 && value - values[index - 1] <= 1400));
  if (!valid(audio) || !valid(video)) throw new Error('Exactly six unambiguous generated events required');
  const deltas = video.map((value, index) => value - audio[index]);
  if (deltas.some(value => Math.abs(value) > 500)) throw new Error('Generated event pairing ambiguous');
  const round = value => Math.round(value * 10) / 10;
  const ordered = [...deltas].sort((a, b) => a - b);
  const bounded = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1000 ? round(value) : null;
  return { matchedEvents: 6, videoMinusAudioMs: deltas.map(round),
    medianAudioLeadMs: round((ordered[2] + ordered[3]) / 2), spreadMs: round(ordered.at(-1) - ordered[0]),
    contextBaseLatencyMs: bounded(latency.base), contextOutputLatencyMs: bounded(latency.output),
    savedPixels: false, savedAudio: false, productionAudioGraphChanged: false,
    scope: 'Generated Edge decoder-draw versus analyser observation, not physical Tesla lip-sync calibration' };
}
async function main() {
  const candidate = process.argv[2] === '--delay-candidate';
  if (process.platform !== 'win32' || (process.argv.length !== 2 && !(candidate && process.argv.length === 3))) throw new Error('Fixed Windows generated probe required');
  const { chromium } = require('playwright-core');
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 904, height: 678 } });
    probeStage = 'configuration';
    const config = await (await context.request.get(ORIGIN + '/api/configuration')).json();
    if (config['persist.tesla-android.browser_audio.is_enabled'] !== 1) throw new Error('Do not change disabled audio preference');
    const page = await context.newPage();
    let errors = 0, blockedWrites = 0;
    page.on('pageerror', () => { errors++; });
    if (candidate) {
      const root = 'C:/Users/Hilman/Documents/Codex/.codex-runner-temp/frontend-audio-delay-candidate-20261011s';
      for (const [relative, expected] of [
        ['js/audio/audioplayback.js', '4c18cf01f9d7a1d85b99cb9d2aa326a9ec649e3af57d057db7ec00f548c4e7a7'],
        ['js/settings/settings-controller.js', 'a939a150665faa7cbc730b88a7b949946d6adca6e259ee64b9b5ef801c16567d'],
      ]) {
        const bytes = await fs.readFile(root + '/' + relative);
        if (crypto.createHash('sha256').update(bytes).digest('hex') !== expected) throw new Error('Fixed candidate bytes required');
        await page.route(ORIGIN + '/' + relative, route => route.fulfill({ status: 200, contentType: 'text/javascript',
          headers: { 'Cache-Control': 'no-store' }, body: bytes }));
      }
      await page.addInitScript(() => { localStorage.setItem('TAHtml_audioDelayMs', '200'); });
    }
    await page.route(ORIGIN + '/api/**', async route => {
      const request = route.request(), path = new URL(request.url()).pathname;
      if (request.method() !== 'GET' && !['/api/displaySession', '/api/displayRole'].includes(path)) {
        blockedWrites++; await route.abort();
      } else await route.continue();
    });
    await page.route(ORIGIN + '/js/display/renderers/h264-broadway/h264BrodwayWorker.js*', async route => {
      const response = await route.fetch();
      if (response.status() !== 200) throw new Error('Worker unavailable');
      await route.fulfill({ response, body: instrumentWorker(await response.body()), headers: { 'Cache-Control': 'no-store' } });
    });
    await page.addInitScript(() => {
      for (const key of ['DisplayRepository_isPrimaryDisplaySharedPreferencesKey', 'flutter.DisplayRepository_isPrimaryDisplaySharedPreferencesKey']) localStorage.setItem(key, 'true');
      const state = window.__AVSync = { active: false, audio: [], video: [], base: null, output: null };
      const NativeWorker = window.Worker, NativeContext = window.AudioContext;
      window.AudioContext = class extends NativeContext { constructor(...args) { super(...args); window.__AVTestContext = this; } };
      let white = false;
      window.Worker = class extends NativeWorker {
        constructor(url, ...args) {
          super(url, ...args);
          if (!new URL(url, location.href).pathname.endsWith('/h264BrodwayWorker.js')) return;
          this.addEventListener('message', event => {
            if (typeof event.data?.syncFlash !== 'boolean') return;
            const next = event.data.syncFlash;
            if (state.active && next && !white && state.video.length < 8) state.video.push(performance.now() - state.start);
            white = next;
          });
        }
      };
    });
    probeStage = 'page';
    await page.goto(ORIGIN, { waitUntil: 'domcontentloaded', timeout: 20000 });
    probeStage = 'picture';
    await page.waitForFunction(() => Number(document.querySelector('#canvas')?.dataset.videoFrames) > 0, undefined, { timeout: 30000 });
    probeStage = 'audio';
    await page.locator('#home-audio-button').click();
    await page.waitForFunction(() => window.__liveAudioEl && !window.__liveAudioEl.paused && window.__liveAudioEl.currentTime > .1,
      undefined, { timeout: 20000 });
    await page.evaluate(async () => {
      const state = window.__AVSync, ctx = window.__AVTestContext;
      if (!ctx) throw new Error('Test audio context unavailable');
      await ctx.resume();
      // This routing exists only in the isolated diagnostic browser, never on Tesla.
      const source = ctx.createMediaElementSource(window.__liveAudioEl), analyser = ctx.createAnalyser();
      analyser.fftSize = 256; source.connect(analyser); analyser.connect(ctx.destination);
      state.base = Number.isFinite(ctx.baseLatency) ? ctx.baseLatency * 1000 : null;
      state.output = Number.isFinite(ctx.outputLatency) ? ctx.outputLatency * 1000 : null;
      const samples = new Float32Array(256);
      let sounding = false;
      state.start = performance.now(); state.active = true;
      state.timer = setInterval(() => {
        if (!state.active) return;
        analyser.getFloatTimeDomainData(samples);
        let peak = 0;
        for (const value of samples) peak = Math.max(peak, Math.abs(value));
        const next = peak > .003;
        if (next && !sounding && state.audio.length < 8) state.audio.push(performance.now() - state.start);
        sounding = next;
      }, 10);
    });
    console.log(JSON.stringify({ avSyncProbeReady: true, generatedOnly: true, savedPixels: false, savedAudio: false }));
    await page.waitForTimeout(30000);
    const value = await page.evaluate(() => {
      const state = window.__AVSync; state.active = false; clearInterval(state.timer);
      return { audio: state.audio, video: state.video, base: state.base, output: state.output };
    });
    console.log(JSON.stringify({ observedAudioEvents: value.audio.length, observedVideoEvents: value.video.length, errors, blockedWrites,
      audioOnsetsMs: value.audio.map(value => Math.round(value * 10) / 10),
      videoOnsetsMs: value.video.map(value => Math.round(value * 10) / 10),
      scope: 'Generated events only; no recording or physical-output timing claim' }));
    if (errors) throw new Error('Diagnostic browser error');
    if (candidate) {
      probeStage = 'settings-layout';
      await page.locator('#home-settings-button').click();
      await page.locator('[data-page="settings"]').click();
      await page.locator('#settings-sidebar button').filter({ hasText: 'Audio', exact: true }).click();
      const slider = page.locator('.settings-audio-delay-slider');
      if (await slider.inputValue() !== '200') throw new Error('Local delay settings mismatch');
      const layouts = [];
      for (const viewport of [{ width: 904, height: 678 }, { width: 390, height: 844 }]) {
        await page.setViewportSize(viewport);
        const facts = await slider.evaluate(input => {
          const box = input.getBoundingClientRect();
          return { x: Math.round(box.x), width: Math.round(box.width),
            withinViewport: box.x >= 0 && box.right <= innerWidth, value: input.value };
        });
        layouts.push({ viewport: viewport.width, ...facts });
        await slider.screenshot({ path: 'C:/Users/Hilman/Documents/Codex/.codex-runner-temp/audio-delay-control-' + viewport.width + '-20261011s.png' });
        if (!facts.withinViewport) throw new Error('Audio delay layout exceeds viewport');
      }
      console.log(JSON.stringify({ localDelayControl: true, layouts, savedGeneratedUiOnly: true, productionFilesChanged: false }));
    }
    probeStage = 'event-pairing';
    console.log(JSON.stringify({ ...syncFacts(value.audio, value.video, value), candidateOnly: candidate, audioDelayMs: candidate ? 200 : 0 }));
  } finally { await browser.close(); }
}
if (require.main === module) main().catch(() => {
  console.error(JSON.stringify({ generatedMeasurementUnavailable: true, stage: probeStage, productionPlaybackChanged: false }));
  process.exitCode = 1;
});
module.exports = { instrumentWorker, syncFacts };
