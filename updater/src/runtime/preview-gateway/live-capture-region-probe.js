// SPDX-License-Identifier: GPL-3.0-only
const assert = require('node:assert/strict');
function transportCounters() {
  const counts = { textMessages: 0, configPackets: 0, keyframes: 0, deltaPackets: 0, invalidPackets: 0 };
  return {
    receive(payload) {
      let kind = 'invalidPackets';
      if (typeof payload === 'string') kind = 'textMessages';
      else if (Buffer.isBuffer(payload) && payload.length >= 13 && payload.length <= 2097164 &&
        payload.readUInt32BE(8) === payload.length - 12) {
        kind = payload[0] & 0x40 ? 'configPackets' : payload[0] & 0x20 ? 'keyframes' : 'deltaPackets';
      }
      counts[kind] = Math.min(1000000, counts[kind] + 1);
    },
    snapshot: () => ({ ...counts })
  };
}
function videoMetadata(dataset = {}) {
  const bounded = (value, max) => /^\d{1,9}$/.test(String(value)) && Number(value) <= max ? Number(value) : 0;
  return { state: ['ready', 'reconnecting', 'loading', 'error'].includes(dataset.videoState) ? dataset.videoState : 'unknown',
    frames: bounded(dataset.videoFrames, 100000000),
    decoder: ['legacy', 'webcodecs'].includes(dataset.videoDecoder) ? dataset.videoDecoder : 'unknown',
    width: bounded(dataset.videoWidth, 1920), height: bounded(dataset.videoHeight, 1920) };
}
function browserMetadata(value = {}) {
  return { readyState: ['loading', 'interactive', 'complete'].includes(value.readyState) ? value.readyState : 'unknown',
    hasCanvas: value.hasCanvas === true, hasRendererScript: value.hasRendererScript === true,
    hasRoleModal: value.hasRoleModal === true,
    scriptCount: Number.isInteger(value.scriptCount) && value.scriptCount >= 0 && value.scriptCount <= 128 ? value.scriptCount : 0 };
}
function requestCounters() {
  const counts = {};
  const allowed = new Set(['/', '/api/displaySession', '/api/displayState', '/api/displayRole', '/api/videoState']);
  return {
    receive(url, status) {
      const path = new URL(url, 'https://diagnostic.invalid').pathname;
      if (!allowed.has(path) || !Number.isInteger(status) || status < 100 || status > 599) return;
      const values = counts[path] || (counts[path] = {});
      values[status] = Math.min(1000000, (values[status] || 0) + 1);
    },
    snapshot: () => JSON.parse(JSON.stringify(counts))
  };
}
function unobstructed(elements) {
  const allowed = new Set(['canvas', 'touch-layer', 'display-root', 'home-stage', 'home-shell']);
  return Array.isArray(elements) && elements.some(element => element.id === 'touch-layer') &&
    elements.every(element => allowed.has(element.id) || (!element.id && ['BODY', 'HTML'].includes(element.tag)));
}
function regions(png) {
  if (!png || !Number.isInteger(png.width) || !Number.isInteger(png.height) || png.width < 1 || png.height < 1 ||
      png.width > 1920 || png.height > 1920 || !Buffer.isBuffer(png.data) || png.data.length !== png.width * png.height * 4) {
    throw new Error('Invalid decoded diagnostic frame');
  }
  const values = {};
  for (const [name, left, top, right, bottom] of [['whole', 0, 0, 1, 1], ['center', .2, .2, .8, .8],
    ['top', 0, 0, 1, .1], ['bottom', 0, .9, 1, 1]]) {
    let samples = 0, nonblack = 0;
    for (let y = Math.floor(top * png.height); y < Math.ceil(bottom * png.height); y++) {
      for (let x = Math.floor(left * png.width); x < Math.ceil(right * png.width); x++) {
        const i = (y * png.width + x) * 4; samples++;
        if (png.data[i + 3] && Math.max(png.data[i], png.data[i + 1], png.data[i + 2]) > 8) nonblack++;
      }
    }
    values[name] = Number((nonblack / samples).toFixed(5));
  }
  return values;
}
async function main() {
  if (process.argv.length > 3 || (process.argv[2] && process.argv[2] !== '--legacy')) throw new Error('Only optional --legacy is supported');
  const { chromium } = require('playwright-core');
  const { PNG } = require('../waydroid-display/node_modules/pngjs');
  const origin = 'https://teslavm.mondriandsm.synology.me';
  const browser = await chromium.launch({ executablePath: process.env.EDGE_PATH ||
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext(), errors = [], blocked = [];
    const created = await context.request.post(origin + '/api/displaySession', {
      headers: { Origin: origin, 'Content-Type': 'application/json' }, data: {}, timeout: 10000 });
    assert.equal(created.status(), 200); const session = await created.json();
    const reply = await context.request.get(origin + '/api/displayState', { headers: { 'X-Display-Session': session.token }, timeout: 10000 });
    assert.equal(reply.status(), 200); const state = await reply.json();
    assert.ok([state.width, state.height].every(value => Number.isInteger(value) && value >= 96 && value <= 1920));
    const page = await context.newPage(); await page.setViewportSize({ width: state.width, height: state.height });
    const transport = transportCounters();
    const requests = requestCounters();
    page.on('response', response => { requests.receive(response.url(), response.status()); });
    const sockets = { opened: 0, closed: 0, errors: 0 };
    page.on('websocket', socket => {
      if (new URL(socket.url()).pathname !== '/sockets/display') return;
      sockets.opened++;
      socket.on('framereceived', frame => transport.receive(frame.payload));
      socket.on('close', () => { sockets.closed++; });
      socket.on('socketerror', () => { sockets.errors++; });
    });
    await page.addInitScript(() => {
      for (const key of ['DisplayRepository_isPrimaryDisplaySharedPreferencesKey', 'flutter.DisplayRepository_isPrimaryDisplaySharedPreferencesKey']) {
        localStorage.setItem(key, 'true');
      }
    });
    page.on('pageerror', error => errors.push(error.message));
    await page.route(origin + '/api/**', async route => {
      const request = route.request(), endpoint = new URL(request.url()).pathname;
      if (request.method() !== 'GET' && !['/api/displaySession', '/api/displayRole'].includes(endpoint)) {
        blocked.push(endpoint); await route.abort();
      } else await route.continue();
    });
    if (process.argv[2] === '--legacy') await page.addInitScript(() => { window.VideoDecoder = undefined; });
    await page.goto(origin + '/', { waitUntil: 'domcontentloaded', timeout: 30000 });
    try {
      await page.waitForFunction(() => document.querySelector('#canvas')?.dataset.videoState === 'ready', undefined, { timeout: 35000 });
    } catch {
      const dataset = await page.evaluate(() => {
        const data = document.querySelector('#canvas')?.dataset || {};
        return { videoState: data.videoState, videoFrames: data.videoFrames, videoDecoder: data.videoDecoder,
          videoWidth: data.videoWidth, videoHeight: data.videoHeight };
      });
      const startup = await page.evaluate(() => ({ readyState: document.readyState,
        hasCanvas: Boolean(document.querySelector('#canvas')),
        hasRendererScript: [...document.scripts].some(script => /\/renderers\/h264-/.test(new URL(script.src || location.href).pathname)),
        hasRoleModal: Boolean(document.querySelector('#role-modal')), scriptCount: document.scripts.length }));
      console.error(JSON.stringify({ readinessTimeout: true, renderer: process.argv[2] === '--legacy' ? 'legacy' : 'automatic',
        video: videoMetadata(dataset), startup: browserMetadata(startup), transport: transport.snapshot(), sockets, pageErrorCount: errors.length,
        requests: requests.snapshot(), blockedWrites: [...new Set(blocked)], scope: 'Counts and allowlisted state only; no payloads, images or settings writes' }));
      throw new Error('H264 browser readiness deadline exceeded');
    }
    const samples = [];
    for (let i = 0; i < 3; i++) {
      const png = PNG.sync.read(await page.locator('#canvas').screenshot());
      const metadata = await page.locator('#canvas').evaluate(canvas => ({ state: canvas.dataset.videoState,
        frames: Number(canvas.dataset.videoFrames), width: canvas.width, height: canvas.height,
        centerElements: (() => { const bounds = canvas.getBoundingClientRect(); return document.elementsFromPoint(
          bounds.x + bounds.width / 2, bounds.y + bounds.height / 2).slice(0, 8).map(element => ({ tag: element.tagName, id: element.id })); })() }));
      assert.ok(unobstructed(metadata.centerElements), 'Browser UI obscures the app center; this is not capture evidence');
      samples.push({ ...metadata, nonblackFraction: regions(png) });
      if (i < 2) await page.waitForTimeout(1000);
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ renderer: process.argv[2] === '--legacy' ? 'legacy' : 'automatic',
      samples, transport: transport.snapshot(), sockets, requests: requests.snapshot(), blockedWrites: [...new Set(blocked)], errors,
      scope: 'In-memory H264 frame statistics only; no saved images, video recording, input or settings changes' }));
  } finally { await browser.close(); }
}
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
module.exports = { regions, unobstructed, transportCounters, videoMetadata, browserMetadata, requestCounters };
