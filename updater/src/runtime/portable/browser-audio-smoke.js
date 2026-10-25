// SPDX-License-Identifier: GPL-3.0-only
// Local forwarded generated fixture only, with no credentials or TLS bypass.
const ORIGIN = 'http://127.0.0.1:18188';
function summarize(samples, bytes) {
  if (!Number.isSafeInteger(bytes) || bytes < 1024 || bytes > 16 * 1024 * 1024 ||
      !Array.isArray(samples) || samples.length < 8 || samples.length > 20) throw Error('Generated browser observation bounds');
  let matching = 0;
  for (const value of samples) {
    if (!value || !['time', 'sampleRate', 'peakHz', 'peakDb'].every(key => Number.isFinite(value[key])) ||
        value.time < 0 || value.time > 60 || value.sampleRate !== 48000 || value.paused !== false ||
        value.error !== null || value.outputMuted !== true || value.savedAudio !== false) throw Error('Generated browser playback invalid');
    if (value.toneMatches === true && Math.abs(value.peakHz - 1000) <= 35 && value.peakDb > -75 && value.peakDb <= 0) matching++;
  }
  const advance = samples.at(-1).time - samples[0].time;
  if (advance < 1 || matching < 8 || matching < samples.length * .8) throw Error('Generated browser tone not verified');
  return { kind: 'generated-browser-audio', bytes, samples: samples.length, matchingSamples: matching,
    mediaAdvanceSeconds: advance, generatedBrowserAudioVerified: true, androidAudioVerified: false,
    publicTlsVerified: false, syncVerified: false, outputMuted: true, savedAudio: false, installable: false };
}
async function main(args) {
  if (process.platform !== 'win32' || args.length) throw Error('Fixed Windows generated browser check required');
  const { chromium } = require('../preview-gateway/node_modules/playwright-core');
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext(), page = await context.newPage();
    const faults = []; let bytes = 0;
    page.on('pageerror', () => faults.push('page'));
    page.on('websocket', socket => {
      if (socket.url() !== 'ws://127.0.0.1:18188/tone') { faults.push('socket'); return; }
      socket.on('framereceived', frame => {
        if (!Buffer.isBuffer(frame.payload)) { faults.push('text'); return; }
        bytes += frame.payload.length;
      });
    });
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.origin === ORIGIN && ['/', '/fixture.js', '/js/audio/audioplayback.js', '/js/audio/audio-ws-worker.js'].includes(url.pathname) && !url.search) return route.continue();
      return route.abort();
    });
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded', timeout: 10000 });
    await page.locator('#play').click();
    await page.waitForFunction(() => window.generatedAudioFacts?.().toneMatches === true &&
      window.generatedAudioFacts().time > .1, undefined, { timeout: 15000 });
    const samples = [];
    for (let i = 0; i < 10; i++) {
      samples.push(await page.evaluate(() => window.generatedAudioFacts()));
      await page.waitForTimeout(200);
    }
    await page.evaluate(() => window.stopAudioPlayback());
    if (faults.length) throw Error('Generated browser faults');
    console.log(JSON.stringify(summarize(samples, bytes)));
  } finally { await browser.close(); }
}
if (require.main === module) main(process.argv.slice(2)).catch(() => { console.error('Generated browser audio verification incomplete'); process.exitCode = 1; });
module.exports = { summarize, main };
