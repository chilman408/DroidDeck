// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const http = require('node:http');
const { once } = require('node:events');
const { event, createServer, PAGE, DIGEST } = require('./generated-audio-fixture');
const { owner, guestArgs, lease, observation, clean, pcm: capturePcm, AUDIO_UNIT } = require('./generated-audio-check');
const TOKEN = 'a'.repeat(32);
const pcm = () => ({ bytes: 192000, frames: 48000, seconds: 1, nonzeroSamples: 80000, peak: 100,
  tone: { frequencyHz: 1000, sampleRate: 48000, windows: 100, matchingWindows: 100 } });
const sample = () => ({ width: 640, height: 360, time: 1, muted: false, playing: true, code: 0, pageToken: TOKEN });
test('fixed owner rejects arbitrary payload and command content', () => {
  assert.equal(owner(TOKEN), TOKEN);
  for (const value of [null, '', 'a'.repeat(31), 'A'.repeat(32), TOKEN + ';', 1]) assert.throws(() => owner(value));
});
test('guest shell wrappers receive Android PATH even under a clean systemd environment', () => {
  assert.deepEqual(guestArgs('123', ['/system/bin/am', 'start']), ['-t', '123', '-m', '-p', '-r', '--',
    '/system/bin/env', 'PATH=/system/bin:/apex/com.android.runtime/bin', 'ANDROID_ROOT=/system', 'ANDROID_DATA=/data', '/system/bin/am', 'start']);
  for (const value of ['1', '0', '-1', '123;id', 'x', '2147483648']) assert.throws(() => guestArgs(value, []));
});
test('PCM proof requires non-silence, complete frames and bounded consistent metadata', () => {
  const result = observation(pcm()); assert.equal(result.generatedPcmVerified, true);
  assert.equal(result.savedAudio, false); assert.equal(result.browserAudioVerified, false); assert.equal(result.installable, false);
  for (const changed of [{ nonzeroSamples: 0 }, { peak: 0 }, { frames: 47999 }, { seconds: 2 },
    { bytes: 192001 }, { bytes: 2097156 }, { peak: 32769 }, { peak: 1.5 }, { nonzeroSamples: 96001 }, { raw: 'PRIVATE' }]) {
    assert.throws(() => observation({ ...pcm(), ...changed }));
  }
  const silent = observation({ ...pcm(), nonzeroSamples: 0, peak: 0 }, true);
  assert.equal(silent.generatedPcmVerified, false); assert.equal(silent.savedAudio, false);
  assert.throws(() => observation({ ...pcm(), private: 'NO' }, true));
  assert.equal(observation({ ...pcm(), peak: 3 }).generatedPcmVerified, true);
  for (const tone of [{ ...pcm().tone, frequencyHz: 440 }, { ...pcm().tone, matchingWindows: 101 },
    { ...pcm().tone, windows: 99 }]) assert.throws(() => observation({ ...pcm(), tone }, true));
  assert.equal(observation({ ...pcm(), tone: { ...pcm().tone, matchingWindows: 0 } }, true).generatedPcmVerified, false);
});
test('active lease requires exact generated provenance and progressing unmuted page', () => {
  const value = { kind: 'generated-audio-fixture', sha256: DIGEST, pageToken: TOKEN, ready: true, active: true, gestureObserved: true, reports: 2 };
  assert.equal(lease(value), value);
  for (const changed of [{ kind: 'foreign' }, { sha256: '0'.repeat(64) }, { ready: false }, { active: false },
    { reports: 0 }, { reports: 351 }, { active: 'true' }, { gestureObserved: false }, { pageToken: 'foreign' }]) assert.throws(() => lease({ ...value, ...changed }));
  assert.equal(lease({ ...value, active: false }, false).active, false);
});
test('client event contains only numeric generated playback metadata and typed flags', () => {
  assert.deepEqual(event(sample()), sample());
  for (const value of [null, { ...sample(), private: 'NO' }, { ...sample(), time: Infinity },
    { ...sample(), time: -1 }, { ...sample(), muted: 0 }, { ...sample(), playing: 1 },
    { ...sample(), code: 1 }, { ...sample(), width: 1 }, { ...sample(), time: 8.2 }]) assert.equal(event(value), null);
  assert.match(PAGE, /b.onclick=.*v.muted=false;v.play\(\)/);
  assert.doesNotMatch(PAGE, /autoplay|localStorage|document\.cookie|credentials/);
  assert.doesNotThrow(() => new (require('node:vm').Script)(PAGE.match(/<script>([\s\S]*)<\/script>/)[1]));
});
async function fixture(t) {
  const server = createServer(Buffer.from('generated-only'), { origin: 'http://fixture.invalid', clients: ['127.0.0.1'], now: () => fixture.now, pageToken: TOKEN });
  fixture.now = 1000; server.listen(0, '127.0.0.1'); await once(server, 'listening');
  t.after(() => { server.closeAllConnections(); server.close(); });
  const request = (path, options = {}) => new Promise((resolve, reject) => {
    const req = http.request({ hostname: '127.0.0.1', port: server.address().port, path,
      method: options.method || 'GET', headers: { host: 'fixture.invalid', ...options.headers } }, res => {
      let body = ''; res.on('data', chunk => body += chunk); res.once('error', reject);
      res.once('end', () => resolve({ status: res.statusCode, json: async () => JSON.parse(body) }));
    }); req.once('error', reject); req.end(options.body);
  });
  const post = value => request('/event', { method: 'POST', headers: { origin: 'http://fixture.invalid', 'content-type': 'application/json' }, body: JSON.stringify(value) });
  const markReady = () => request('/ready', { method: 'POST', headers: { origin: 'http://fixture.invalid',
    'content-type': 'application/json' }, body: JSON.stringify({ pageToken: TOKEN, gesture: false, playError: 'none' }) });
  return { request, post, markReady };
}
test('HTTP fixture starts inactive and requires media plus advancing audible events', async t => {
  const { request, post, markReady } = await fixture(t);
  assert.equal((await (await request('/status')).json()).active, false);
  await markReady();
  assert.equal((await request('/fixture.mp4', { headers: { range: 'bytes=0-4' } })).status, 206);
  assert.equal((await post(sample())).status, 204);
  assert.equal((await (await request('/status')).json()).active, false);
  await post({ ...sample(), time: 2 }); assert.equal((await (await request('/status')).json()).active, true);
  fixture.now = 3000; assert.equal((await (await request('/status')).json()).active, false);
});
test('muted, paused and unchanging playback never establishes audio progress', async t => {
  const { request, post, markReady } = await fixture(t); await markReady(); await request('/fixture.mp4');
  await post(sample()); await post(sample()); assert.equal((await (await request('/status')).json()).active, false);
  await post({ ...sample(), time: 2, muted: true }); assert.equal((await (await request('/status')).json()).active, false);
  await post({ ...sample(), time: 3, playing: false }); assert.equal((await (await request('/status')).json()).active, false);
});
test('media events cannot substitute for the page paint/readiness handshake', async t => {
  const { request, post, markReady } = await fixture(t); await request('/fixture.mp4');
  await post(sample()); await post({ ...sample(), time: 2 });
  assert.equal((await (await request('/status')).json()).ready, false);
  assert.equal((await (await request('/status')).json()).active, false);
  await markReady(); assert.equal((await (await request('/status')).json()).active, true);
});
test('stale page cannot establish readiness or submit progress to a new invocation', async t => {
  const { request, post } = await fixture(t);
  assert.equal((await request('/?proof=' + TOKEN)).status, 200);
  assert.equal((await request('/?proof=' + 'b'.repeat(32))).status, 404);
  assert.equal((await post({ ...sample(), pageToken: 'b'.repeat(32) })).status, 403);
  assert.equal((await (await request('/status')).json()).ready, false);
});
test('page readiness does not depend on WebView preloading unmuted media before the gesture', async t => {
  const { request } = await fixture(t);
  const ready = value => request('/ready', { method: 'POST', headers: {
    origin: 'http://fixture.invalid', 'content-type': 'application/json' }, body: JSON.stringify(value) });
  const message = { pageToken: TOKEN, gesture: false, playError: 'none' };
  assert.equal((await ready({ ...message, pageToken: 'b'.repeat(32) })).status, 403);
  assert.equal((await ready({ ...message, private: 'NO' })).status, 403);
  assert.equal((await ready({ ...message, playError: 'private arbitrary text' })).status, 403);
  assert.equal((await ready(message)).status, 204);
  const state = await (await request('/status')).json();
  assert.equal(state.ready, true); assert.equal(state.active, false);
  assert.equal(state.gestureObserved, false); assert.equal(state.playError, 'none');
  await ready({ ...message, gesture: true, playError: 'NotAllowedError' });
  const blocked = await (await request('/status')).json();
  assert.equal(blocked.gestureObserved, true); assert.equal(blocked.playError, 'NotAllowedError');
});
test('HTTP fixture rejects host/origin spoofing, malformed range, oversized and private payload', async t => {
  const { request } = await fixture(t);
  assert.equal((await request('/', { headers: { host: 'other.invalid' } })).status, 403);
  assert.equal((await request('/event', { method: 'POST', headers: { origin: 'http://other.invalid' }, body: '{}' })).status, 403);
  assert.equal((await request('/fixture.mp4', { headers: { range: 'bytes=2-1' } })).status, 416);
  assert.equal((await request('/event', { method: 'POST', headers: { origin: 'http://fixture.invalid', 'content-type': 'application/json' }, body: 'x'.repeat(513) })).status, 413);
  assert.equal((await request('/private')).status, 404);
});
function cleanupContext(record = { lease: TOKEN }, description = 'owned') {
  const calls = [];
  return { calls, description: 'owned', prepare: async () => {}, unit: async name => { assert.equal(name, AUDIO_UNIT); return { ActiveState: 'active', MainPID: '123', ControlPID: '0', Description: description }; },
    store: { load: async () => record }, invoke: async (...args) => calls.push(['invoke', ...args]),
    native: { adopt: async token => calls.push(['adopt', token]), finish: async () => calls.push(['finish']) } };
}
test('cleanup stops only its owned unit then retires its own guest lease', async () => {
  const ctx = cleanupContext(); await clean(ctx, TOKEN);
  assert.deepEqual(ctx.calls, [['invoke', '/usr/bin/systemctl', ['stop', AUDIO_UNIT]], ['adopt', TOKEN], ['finish']]);
});
test('foreign unit or lease is never stopped/adopted', async () => {
  for (const ctx of [cleanupContext({ lease: 'b'.repeat(32) }), cleanupContext({ lease: TOKEN }, 'foreign')]) {
    await assert.rejects(clean(ctx, TOKEN)); assert.deepEqual(ctx.calls, []);
  }
});
test('completed cleanup with no unit or lease is idempotent', async () => {
  const ctx = cleanupContext(null); ctx.unit = async () => ({ ActiveState: 'inactive', MainPID: '0', ControlPID: '0' });
  await clean(ctx, TOKEN); assert.deepEqual(ctx.calls, []);
});
test('early failure cleanup prepares its private journal before attempting to load it', async () => {
  const ctx = cleanupContext(null); let prepared = false;
  ctx.prepare = async () => { prepared = true; };
  ctx.store.load = async () => { assert.equal(prepared, true); return null; };
  ctx.unit = async () => ({ ActiveState: 'inactive', MainPID: '0', ControlPID: '0' });
  await clean(ctx, TOKEN); assert.deepEqual(ctx.calls, []);
});
test('PCM worker requires own lease/unit, guest network namespace and active generated page', async () => {
  const ctx = cleanupContext(); ctx.invoke = async () => assert.fail('No host LXC monitor lookup inside guest network namespace');
  const stats = { generatedOnly: true };
  const options = { readlink: async () => 'net:[1]', makeStats: () => stats, readStatus: async () => ({ kind: 'generated-audio-fixture',
    sha256: DIGEST, pageToken: TOKEN, ready: true, active: true, gestureObserved: true, reports: 2 }),
    runProbe: async (connect, duration, actual) => { assert.equal(connect, undefined); assert.equal(duration, 2000); assert.equal(actual, stats); return pcm(); } };
  assert.deepEqual(await capturePcm(ctx, TOKEN, '123', options), pcm());
  for (const change of [
    { ctx: cleanupContext({ lease: 'b'.repeat(32) }) },
    { ctx: cleanupContext({ lease: TOKEN }, 'foreign') },
    { options: { readlink: async path => path.includes('self') ? 'net:[1]' : 'net:[2]' } },
    { options: { readStatus: async () => ({}) } }]) {
    const actual = change.ctx || ctx;
    await assert.rejects(capturePcm(actual, TOKEN, '123', { ...options, ...change.options,
      runProbe: async () => assert.fail('Foreign or inactive capture must not read audio') }));
  }
});
