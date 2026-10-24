// SPDX-License-Identifier: GPL-3.0-only
// Numeric timing only. Worker draw notifications are not physical display presentation.
class TimingSeries {
  constructor() { this.values = []; }
  add(value) {
    if (typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 60000 && this.values.length < 4096) {
      this.values.push(value);
    }
  }
  snapshot() {
    const values = [...this.values].sort((a, b) => a - b), round = value => Math.round(value * 10) / 10;
    return { samples: values.length, meanMs: values.length ? round(values.reduce((a, b) => a + b, 0) / values.length) : null,
      p95Ms: values.length ? round(values[Math.ceil(values.length * 0.95) - 1]) : null,
      maxMs: values.length ? round(values.at(-1)) : null, gapsOver100Ms: values.filter(value => value > 100).length };
  }
}
class StreamCadence {
  constructor() {
    this.arrivals = new TimingSeries(); this.timestamps = new TimingSeries();
    this.frames = 0; this.configurations = 0; this.sessions = 0; this.invalid = 0;
    this.timestampDiscontinuities = 0; this.previousArrival = null; this.previousPts = null;
  }
  receive(payload, now) {
    if (typeof now !== 'number' || !Number.isFinite(now) || now < 0 || now > 60000) { this.invalid++; return; }
    if (typeof payload === 'string') {
      try {
        if (JSON.parse(payload).type === 'scrcpy-session') {
          this.sessions++; this.previousArrival = null; this.previousPts = null;
        }
      } catch { this.invalid++; }
      return;
    }
    if (!Buffer.isBuffer(payload) || payload.length < 13 || payload.length > 2097164 ||
        payload.readUInt32BE(8) !== payload.length - 12 || (payload[0] & 0x80)) { this.invalid++; return; }
    if (payload[0] & 0x40) { this.configurations++; return; }
    const ptsBig = payload.readBigUInt64BE(0) & ((1n << 61n) - 1n);
    if (ptsBig > BigInt(Number.MAX_SAFE_INTEGER)) { this.invalid++; return; }
    const pts = Number(ptsBig) / 1000;
    if (this.previousArrival !== null) this.arrivals.add(now - this.previousArrival);
    if (this.previousPts !== null) {
      if (pts <= this.previousPts) this.timestampDiscontinuities++;
      else this.timestamps.add(pts - this.previousPts);
    }
    this.frames++; this.previousArrival = now; this.previousPts = pts;
  }
  snapshot() {
    return { frames: this.frames, configurations: this.configurations, sessions: this.sessions, invalid: this.invalid,
      timestampDiscontinuities: this.timestampDiscontinuities, arrival: this.arrivals.snapshot(), sourcePts: this.timestamps.snapshot() };
  }
}
function workerFacts(value = {}) {
  const series = key => {
    const result = new TimingSeries();
    if (Array.isArray(value[key])) for (const number of value[key].slice(0, 4096)) result.add(number);
    return result.snapshot();
  };
  const count = key => Number.isSafeInteger(value[key]) && value[key] >= 0 && value[key] <= 10000 ? value[key] : 0;
  return { decodeAck: series('ack'), workerDraw: series('draw'), mainAnimation: series('animation'),
    maximumWorkerPackets: count('maximumWorkerPackets'), maximumBrowserFrames: count('maximumBrowserFrames'),
    draws: count('draws'), failed: value.failed === true };
}
async function main() {
  if (process.platform !== 'win32' || process.argv.length !== 2) throw new Error('Fixed Windows cadence probe required');
  const { chromium } = require('playwright-core');
  const origin = 'https://teslavm.mondriandsm.synology.me';
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 904, height: 678 } });
    const page = await context.newPage(), transport = new StreamCadence();
    let started = null, blockedWrites = 0, pageErrors = 0;
    await page.route(origin + '/api/**', async route => {
      const request = route.request(), endpoint = new URL(request.url()).pathname;
      if (request.method() !== 'GET' && !['/api/displaySession', '/api/displayRole'].includes(endpoint)) {
        blockedWrites++; await route.abort();
      } else await route.continue();
    });
    page.on('pageerror', () => { pageErrors++; });
    page.on('websocket', socket => {
      if (new URL(socket.url()).pathname === '/sockets/display') socket.on('framereceived', frame => {
        if (started !== null) transport.receive(frame.payload, performance.now() - started);
      });
    });
    await page.addInitScript(() => {
      for (const key of ['DisplayRepository_isPrimaryDisplaySharedPreferencesKey', 'flutter.DisplayRepository_isPrimaryDisplaySharedPreferencesKey']) localStorage.setItem(key, 'true');
      window.__TACadence = { active: false, ack: [], draw: [], animation: [], draws: 0,
        maximumWorkerPackets: 0, maximumBrowserFrames: 0, failed: false };
      const state = window.__TACadence, pending = new Map(), NativeWorker = window.Worker;
      let lastDraw = null, lastAnimation = null;
      const add = (key, value) => { if (state[key].length < 4096) state[key].push(value); };
      window.Worker = class extends NativeWorker {
        constructor(url, ...args) {
          super(url, ...args);
          if (!new URL(url, location.href).pathname.endsWith('/h264BrodwayWorker.js')) return;
          const post = this.postMessage.bind(this);
          this.postMessage = (message, ...rest) => {
            if (state.active && Number.isSafeInteger(message?.h264Ticket) && Number.isSafeInteger(message?.h264Epoch)) {
              const key = message.h264Epoch + ':' + message.h264Ticket;
              if (pending.size < 2) pending.set(key, performance.now());
            }
            return post(message, ...rest);
          };
          this.addEventListener('message', event => {
            if (!state.active) return;
            const data = event.data, now = performance.now();
            if (data?.h264Processed === true) {
              const key = data.h264Epoch + ':' + data.h264Ticket;
              if (pending.has(key)) { add('ack', now - pending.get(key)); pending.delete(key); }
            }
            if (data?.frameRendered === true) {
              if (lastDraw !== null) add('draw', now - lastDraw);
              lastDraw = now; state.draws++;
            }
          });
        }
      };
      function animate(now) {
        if (state.active) {
          if (lastAnimation !== null) add('animation', now - lastAnimation);
          lastAnimation = now;
          if (typeof legacyInFlight !== 'undefined') state.maximumWorkerPackets = Math.max(state.maximumWorkerPackets, legacyInFlight.size);
          if (typeof legacyQueue !== 'undefined') state.maximumBrowserFrames = Math.max(state.maximumBrowserFrames, legacyQueue.length);
          if (typeof legacyFailed !== 'undefined' && legacyFailed) state.failed = true;
        }
        requestAnimationFrame(animate);
      }
      requestAnimationFrame(animate);
    });
    await page.goto(origin, { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForFunction(() => Number(document.querySelector('#canvas')?.dataset.videoFrames) > 0, undefined, { timeout: 30000 });
    if (await page.locator('#canvas').getAttribute('data-video-decoder') !== 'legacy') throw new Error('Legacy decoder required');
    await page.waitForTimeout(2000);
    await page.evaluate(() => { window.__TACadence.active = true; });
    started = performance.now();
    console.log(JSON.stringify({ cadenceProbeReady: true, durationSeconds: 45, savedPixels: false }));
    await page.waitForTimeout(45000);
    started = null;
    const state = await page.evaluate(() => { window.__TACadence.active = false; return window.__TACadence; });
    console.log(JSON.stringify({ transport: transport.snapshot(), worker: workerFacts(state), pageErrors, blockedWrites,
      savedPixels: false, inputInjected: false, globalSettingsWrites: false,
      scope: '45-second Edge timing sample; idle screens are not playback evidence, worker draws are not physical presentation.' }));
  } finally { await browser.close(); }
}
if (require.main === module) main().catch(() => { console.error('Stream cadence metadata unavailable; no input or shared settings changed'); process.exitCode = 1; });
module.exports = { TimingSeries, StreamCadence, workerFacts };
