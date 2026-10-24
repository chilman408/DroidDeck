// SPDX-License-Identifier: GPL-3.0-only
// Acceptance probe for an already-enabled rear display; restore its preferences.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { X509Certificate, createHash } = require('node:crypto');
const { chromium } = require('../runtime/preview-gateway/node_modules/playwright-core');
const { PNG } = require('../runtime/waydroid-display/node_modules/pngjs');
const ORIGIN = 'https://192.168.2.215:8443';
const FIELDS = ['width', 'height', 'density', 'resolutionPreset', 'isResponsive'];

async function main([certPath]) {
  assert.equal(process.argv.length, 3);
  assert.equal(process.env.NODE_EXTRA_CA_CERTS, certPath);
  const cert = new X509Certificate(fs.readFileSync(certPath));
  assert.equal(cert.checkIP('192.168.2.215'), '192.168.2.215');
  const pin = createHash('sha256').update(cert.publicKey.export({ type: 'spki', format: 'der' })).digest('base64');
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless: true, args: ['--ignore-certificate-errors-spki-list=' + pin, '--autoplay-policy=no-user-gesture-required'] });
  const peers = [], errors = [];
  let original, profile, changed = false, restored = false;
  const headers = peer => ({ Origin: ORIGIN, 'Content-Type': 'application/json',
    'X-Display-Session': peer.session.token, 'X-Display-CSRF': peer.session.csrfToken });
  async function state(peer) {
    const response = await peer.context.request.get(ORIGIN + '/api/displayState', { headers: headers(peer) });
    assert.equal(response.status(), 200); return response.json();
  }
  async function idle(peer, predicate = () => true) {
    const end = Date.now() + 35000;
    while (Date.now() < end) {
      const value = await state(peer);
      assert.ok(!['authority_failed', 'recovery_required'].includes(value.transactionStatus));
      if (value.transactionStatus === 'idle' && predicate(value)) return value;
      await new Promise(resolve => setTimeout(resolve, 200));
    }
    throw Error('Configured handoff did not settle');
  }
  async function owner() {
    for (const peer of peers) if ((await idle(peer)).canControl) return peer;
    throw Error('No admitted controller; do not steal another browser session');
  }
  async function picture(peer) {
    await peer.page.waitForFunction(() => Number(document.querySelector('#canvas')?.dataset.videoFrames) > 0,
      undefined, { timeout: 35000 });
    const png = PNG.sync.read(await peer.page.locator('#canvas').screenshot());
    let nonblack = 0;
    for (let i = 0; i < png.data.length; i += 4) if (Math.max(...png.data.subarray(i, i + 3)) > 8) nonblack++;
    assert.ok(nonblack > png.width * png.height * .01);
  }
  async function update(peer, enabled, prioritised) {
    const current = await idle(peer);
    const response = await peer.context.request.post(ORIGIN + '/api/rearDisplayState', {
      headers: headers(peer), data: { revision: current.rearDisplayRevision, enabled, prioritised } });
    assert.equal(response.status(), 200);
  }
  try {
    const probe = await browser.newContext();
    const admission = await probe.request.post(ORIGIN + '/api/displaySession', {
      headers: { Origin: ORIGIN, 'Content-Type': 'application/json' }, data: {} });
    assert.equal(admission.status(), 200);
    original = await state({ context: probe, session: await admission.json() });
    for (const role of ['primary', 'rear']) {
      const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
      const page = await context.newPage(), peer = { context, page, role }; peers.push(peer);
      page.on('pageerror', error => errors.push(error.name));
      page.on('response', async response => {
        if (new URL(response.url()).pathname === '/api/displaySession' && response.ok()) peer.session = await response.json();
      });
      await page.addInitScript(role => {
        for (const key of ['DisplayRepository_isPrimaryDisplaySharedPreferencesKey',
          'flutter.DisplayRepository_isPrimaryDisplaySharedPreferencesKey']) localStorage.setItem(key, String(role === 'primary'));
        window.VideoDecoder = undefined; window.probeDisplayCloses = 0;
        window.WebSocket = new Proxy(WebSocket, { construct(target, args) {
          const socket = new target(...args);
          if (new URL(args[0]).pathname === '/sockets/display') socket.addEventListener('close', () => window.probeDisplayCloses++);
          return socket;
        } });
      }, role);
      await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
      await picture(peer); assert.ok(peer.session);
      await page.waitForFunction(() => typeof window.startAudioFromGesture === 'function');
      await page.evaluate(() => {
        const button = document.createElement('button'); button.id = 'handoff-probe'; button.textContent = 'Audio test';
        button.style.cssText = 'position:fixed;top:0;right:0;z-index:2147483647';
        button.onclick = () => window.startAudioFromGesture(); document.body.appendChild(button);
      });
      await page.locator('#handoff-probe').click(); await page.locator('#handoff-probe').evaluate(node => node.remove());
      await page.waitForFunction(() => [...document.querySelectorAll('audio')].some(a => !a.paused && a.currentTime > 1),
        undefined, { timeout: 30000 });
    }
    const initial = await owner();
    const response = await initial.context.request.get(ORIGIN + '/api/rearDisplayState', { headers: headers(initial) });
    assert.equal(response.status(), 200); profile = await response.json();
    assert.equal(profile.enabled, true, 'This probe never enables or disables a configured rear display');
    const metrics = peer => peer.page.evaluate(() => ({ closes: window.probeDisplayCloses,
      time: document.querySelector('audio').currentTime, paused: document.querySelector('audio').paused,
      error: document.querySelector('audio').error?.code ?? null }));
    const before = await Promise.all(peers.map(metrics));
    changed = true; await update(initial, true, !profile.prioritised);
    const expected = peers.find(peer => peer.role === (!profile.prioritised ? 'rear' : 'primary'));
    await idle(expected, value => value.canControl && value.canResize);
    await new Promise(resolve => setTimeout(resolve, 1500));
    assert.equal((await state(initial)).canControl, false);
    for (const [index, peer] of peers.entries()) {
      const after = await metrics(peer);
      assert.equal(after.closes, before[index].closes, 'Handoff closed video');
      assert.ok(after.time > before[index].time); assert.equal(after.paused, false); assert.equal(after.error, null);
      await picture(peer);
    }
    assert.deepEqual(errors, []);
  } finally {
    try {
      if (changed) {
        await update(await owner(), profile.enabled, profile.prioritised);
        const currentOwner = await owner();
        const response = await currentOwner.context.request.get(ORIGIN + '/api/rearDisplayState', { headers: headers(currentOwner) });
        const current = await response.json();
        assert.equal(current.enabled, profile.enabled); assert.equal(current.prioritised, profile.prioritised);
        const geometry = await idle(currentOwner);
        if (FIELDS.some(key => geometry[key] !== original[key])) {
          const reply = await currentOwner.context.request.post(ORIGIN + '/api/displayState', {
            headers: headers(currentOwner), data: { ...Object.fromEntries(FIELDS.map(key => [key, original[key]])), generation: geometry.generation } });
          assert.equal(reply.status(), 200); await idle(currentOwner);
        }
        restored = true;
      }
    } finally { await browser.close(); }
  }
  assert.equal(restored, true);
  console.log(JSON.stringify({ nativeAcknowledged: true, dualH264: true, dualAudioContinuous: true,
    enabledPreferenceUnchanged: true, originalPriorityRestored: restored, pageErrors: errors, recordingsSaved: false }));
}
main(process.argv.slice(2)).catch(error => { console.error(error); process.exitCode = 1; });
