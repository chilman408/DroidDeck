// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createGateway } = require('./server');
const { createDisplayControl } = require('./display-control');
const listen = s => new Promise(resolve => s.listen(0, '127.0.0.1', resolve));
test('video settings are owner-only, same-origin, CSRF protected and never arbitrary encoder options', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ta-video-routes-')); fs.chmodSync(directory, 0o700);
  fs.writeFileSync(path.join(directory, 'index.html'), 'test');
  const videoSettings = require('./video-settings').createVideoSettings(path.join(directory, 'video.json'));
  const display = createDisplayControl({ helper: { inspect: async () => ({ width: 976, height: 720, density: 180,
    resolutionPreset: 1, isResponsive: 1, recoveryRequired: false }) } }); await display.initialize();
  const gateway = createGateway({ frontendDir: directory, displayControl: display, videoSettings,
    targets: { api: 'http://127.0.0.1:18081', display: 'ws://127.0.0.1:19091', touchscreen: 'ws://127.0.0.1:19999', h264: 'ws://127.0.0.1:19092' } });
  await listen(gateway);
  t.after(async () => { await new Promise(resolve => gateway.close(resolve)); fs.rmSync(directory, { recursive: true, force: true }); });
  const base = `http://127.0.0.1:${gateway.address().port}`;
  const owner = display.createSession(), viewer = display.createSession(); display.connect(owner.token, 'display');
  const payload = { revision: 0, bitRate: 3000000, maxFps: 45 };
  const post = (value, session = owner, headers = {}, route = 'videoState') => fetch(base + '/api/' + route, { method: 'POST',
    headers: { Origin: base, 'Content-Type': 'application/json', 'X-Display-Session': session.token, 'X-Display-CSRF': session.csrfToken, ...headers },
    body: typeof value === 'string' ? value : JSON.stringify(value) });
  assert.equal((await post(payload, viewer)).status, 403);
  assert.equal((await post(payload, owner, { 'X-Display-CSRF': 'wrong' })).status, 403);
  assert.equal((await post(payload, owner, { Origin: 'https://evil.test' })).status, 403);
  assert.equal((await post(payload, owner, { 'Content-Type': 'text/plain' })).status, 415);
  assert.equal((await post(' '.repeat(257))).status, 413);
  assert.equal((await post('{bad')).status, 400);
  assert.equal((await post({ ...payload, video_encoder: 'evil' })).status, 400);
  assert.equal((await post({ ...payload, maxFps: 0 })).status, 400);
  assert.equal((await post(payload, owner, {}, '%76ideoState')).status, 400);
  assert.equal(videoSettings.snapshot().revision, 0);
  assert.equal((await post(payload)).status, 200);
  assert.equal((await post(payload)).status, 409);
  const state = await (await fetch(base + '/api/displayState', { headers: { 'X-Display-Session': viewer.token } })).json();
  assert.equal(state.videoControlSupported, 1); assert.equal(state.videoTargets.bitRate, 3000000);
  const read = await fetch(base + '/api/videoState', { headers: { 'X-Display-Session': viewer.token } });
  assert.equal(read.status, 200); assert.equal((await read.json()).maxFps, 45);
  display.revokeAll(); assert.equal((await post({ ...payload, revision: 1 })).status, 403);
});


test('Automatic quality has exact origin/owner/CSRF/revision and closed JSON requirements; manual changes take over', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'dd-auto-routes-')); fs.chmodSync(directory, 0o700);
  fs.writeFileSync(path.join(directory, 'index.html'), 'test');
  const store = require('./video-settings').createVideoSettings(path.join(directory, 'video.json'));
  const display = createDisplayControl({ helper: { inspect: async () => ({ width: 976, height: 720, density: 180,
    resolutionPreset: 1, isResponsive: 1, recoveryRequired: false }) } }); await display.initialize();
  const gateway = createGateway({ frontendDir: directory, displayControl: display, videoSettings: store,
    targets: { api: 'http://127.0.0.1:18081', display: 'ws://127.0.0.1:19091', touchscreen: 'ws://127.0.0.1:19999', h264: 'ws://127.0.0.1:19092' } });
  await listen(gateway); t.after(async () => { await new Promise(resolve => gateway.close(resolve)); fs.rmSync(directory, { force: true, recursive: true }); });
  const base = `http://127.0.0.1:${gateway.address().port}`, owner = display.createSession(), viewer = display.createSession(); display.connect(owner.token, 'display');
  const body = { revision: 0, profileRevision: 0, enabled: true };
  const post = (value, session = owner, headers = {}, route = '/api/videoAutomatic') => fetch(base + route, { method: 'POST',
    headers: { Origin: base, 'Content-Type': 'application/json', 'X-Display-Session': session.token, 'X-Display-CSRF': session.csrfToken, ...headers },
    body: typeof value === 'string' ? value : JSON.stringify(value) });
  for (const [session, headers] of [[viewer, {}], [owner, { Origin: 'https://evil.test' }], [owner, { 'X-Display-CSRF': 'bad' }]]) {
    assert.equal((await post(body, session, headers)).status, 403);
  }
  assert.equal((await post(body, owner, { 'Content-Type': 'text/plain' })).status, 415);
  assert.equal((await post(' '.repeat(257))).status, 413);
  for (const value of ['{bad', { ...body, telemetry: 100 }, { ...body, enabled: 'yes' }]) assert.equal((await post(value)).status, 400);
  for (const route of ['/api/%76ideoAutomatic','/api/videoAutomatic?x=1']) assert.equal((await post(body, owner, {}, route)).status, 400);
  assert.equal(store.automaticSnapshot().enabled, false);
  const accepted = await post(body); assert.equal(accepted.status, 200); assert.equal((await accepted.json()).policy.enabled, true);
  assert.equal((await post(body)).status, 409);
  const read = await fetch(base + '/api/videoAutomatic', { headers: { 'X-Display-Session': viewer.token } }); assert.equal(read.status, 200);
  const manual = await post({ revision: 0, bitRate: 3000000, maxFps: 30 }, owner, {}, '/api/videoState'); assert.equal(manual.status, 200);
  assert.equal(store.automaticSnapshot().enabled, false); assert.equal(store.snapshot().bitRate, 3000000);
  display.revokeAll(); assert.equal((await post({ ...body, revision: 2, profileRevision: 1 })).status, 403);
});
