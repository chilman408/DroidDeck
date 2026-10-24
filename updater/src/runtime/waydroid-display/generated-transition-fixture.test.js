// SPDX-License-Identifier: GPL-3.0-only
const { test } = require('node:test'), assert = require('node:assert/strict');
const http = require('node:http');
const { frameFacts, av1Facts, reportFact, rangeFact, createFixtureServer, fixtureDirectory, fixtureProfile } = require('./generated-transition-fixture');

test('GPU boot repair keeps generated evidence separate from old accepted reports', () => {
  const directory = fixtureDirectory('--serve-av1-gpu-boot-repair');
  assert.equal(directory, '/home/hilman/teslaandroid-waydroid/generated-av1-gpu-boot-repair-20261017dv2');
  assert.notEqual(directory, fixtureDirectory('--serve-av1-gpu-health'));
  assert.equal(fixtureProfile(directory), 'av1');
});

test('GPU generated playback uses a separate fixed directory, never overwrites earlier evidence', () => {
  assert.equal(fixtureDirectory('--serve-gpu-render'), '/home/hilman/teslaandroid-waydroid/gpu-render-generated-20261010b');
  assert.notEqual(fixtureDirectory('--serve'), fixtureDirectory('--serve-gpu-render'));
  for (const profile of ['--generate', '--serve-private', '/tmp/foreign']) assert.throws(() => fixtureDirectory(profile));
});

test('legacy backpressure browser validation preserves earlier GPU fixture reports', () => {
  const directory = fixtureDirectory('--serve-av1-legacy-backpressure');
  assert.equal(directory, '/home/hilman/teslaandroid-waydroid/generated-av1-legacy-backpressure-20261011p2');
  assert.equal(fixtureProfile(directory), 'av1');
  assert.notEqual(directory, fixtureDirectory('--serve-av1-mesa-boot'));
});

test('stream cadence validation has a fresh generated-only report directory', () => {
  const directory = fixtureDirectory('--serve-av1-stream-cadence');
  assert.equal(directory, '/home/hilman/teslaandroid-waydroid/generated-av1-stream-cadence-20261011q');
  assert.equal(fixtureProfile(directory), 'av1');
  assert.notEqual(directory, fixtureDirectory('--serve-av1-legacy-backpressure'));
  const retest = fixtureDirectory('--serve-av1-stream-cadence-retest');
  assert.notEqual(directory, retest);
  assert.equal(fixtureProfile(retest), 'av1');
  const final = fixtureDirectory('--serve-av1-stream-cadence-final');
  assert.notEqual(final, retest);
  assert.equal(fixtureProfile(final), 'av1');
  const gpu = fixtureDirectory('--serve-av1-gpu-cadence');
  assert.notEqual(gpu, final);
  assert.equal(fixtureProfile(gpu), 'av1');
  const rate = fixtureDirectory('--serve-av1-rate-limit');
  assert.notEqual(rate, gpu);
  assert.equal(fixtureProfile(rate), 'av1');
});
test('every fixed AV1 profile is accepted by the serving gate, not just directory lookup', () => {
  for (const profile of ['--serve-av1', '--serve-av1-gpu', '--serve-av1-layers', '--serve-av1-offline-layers',
    '--serve-av1-software-control', '--serve-av1-gpu-control', '--serve-av1-gpu-motion', '--serve-av1-software-motion', '--serve-av1-verified-gpu-motion',
    '--serve-av1-source-software', '--serve-av1-source-gpu', '--serve-av1-source-software-retest', '--serve-av1-source-gpu-retest']) {
    assert.equal(fixtureProfile(fixtureDirectory(profile)), 'av1');
  }
  for (const profile of ['--serve', '--serve-gpu-render']) assert.equal(fixtureProfile(fixtureDirectory(profile)), 'avc');
  for (const directory of ['/tmp/foreign', fixtureDirectory('--serve-av1') + '/..', undefined, '__proto__']) {
    assert.throws(() => fixtureProfile(directory));
  }
  assert.throws(() => fixtureDirectory('__proto__'));
});

test('AV1 fixture is separate and must verify codec, bit depth, dimensions, duration and actual frame count', () => {
  const directory = fixtureDirectory('--serve-av1');
  assert.equal(directory, '/home/hilman/teslaandroid-waydroid/generated-av1-surface-20261010d');
  assert.notEqual(directory, fixtureDirectory('--serve'));
  assert.notEqual(directory, fixtureDirectory('--serve-gpu-render'));
  assert.equal(fixtureDirectory('--serve-av1-gpu'), '/home/hilman/teslaandroid-waydroid/generated-av1-gpu-rgbx-20261010e');
  assert.notEqual(directory, fixtureDirectory('--serve-av1-gpu'));
  assert.equal(fixtureDirectory('--serve-av1-layers'), '/home/hilman/teslaandroid-waydroid/generated-av1-gpu-layers-20261010g');
  assert.notEqual(fixtureDirectory('--serve-av1-layers'), directory);
  assert.notEqual(fixtureDirectory('--serve-av1-layers'), fixtureDirectory('--serve-av1-gpu'));
  assert.equal(fixtureDirectory('--serve-av1-offline-layers'), '/home/hilman/teslaandroid-waydroid/generated-av1-offline-layers-20261010g4');
  assert.notEqual(fixtureDirectory('--serve-av1-offline-layers'), fixtureDirectory('--serve-av1-layers'));
  assert.equal(fixtureDirectory('--serve-av1-software-control'), '/home/hilman/teslaandroid-waydroid/generated-av1-software-control-20261011h');
  assert.notEqual(fixtureDirectory('--serve-av1-software-control'), fixtureDirectory('--serve-av1-offline-layers'));
  assert.equal(fixtureDirectory('--serve-av1-gpu-control'), '/home/hilman/teslaandroid-waydroid/generated-av1-gpu-control-20261011h');
  assert.notEqual(fixtureDirectory('--serve-av1-gpu-control'), fixtureDirectory('--serve-av1-software-control'));
  assert.equal(fixtureDirectory('--serve-av1-gpu-motion'), '/home/hilman/teslaandroid-waydroid/generated-av1-gpu-motion-20261011h');
  assert.equal(fixtureDirectory('--serve-av1-software-motion'), '/home/hilman/teslaandroid-waydroid/generated-av1-software-motion-20261011h');
  assert.equal(fixtureDirectory('--serve-av1-verified-gpu-motion'), '/home/hilman/teslaandroid-waydroid/generated-av1-gpu-motion-20261011i');
  assert.notEqual(fixtureDirectory('--serve-av1-verified-gpu-motion'), fixtureDirectory('--serve-av1-gpu-motion'));
  assert.equal(new Set(['--serve-av1-gpu-motion', '--serve-av1-software-motion', '--serve-av1-gpu-control', '--serve-av1-software-control']
    .map(fixtureDirectory)).size, 4);
  const stream = { codec_name: 'av1', width: 640, height: 360, pix_fmt: 'yuv420p',
    nb_read_frames: '240', r_frame_rate: '30/1', duration: '8.000000' };
  assert.deepEqual(av1Facts({ streams: [stream] }), { codec: 'av1', frames: 240, sizes: ['640x360'] });
  for (const change of [{ codec_name: 'h264' }, { width: 864 }, { height: 432 },
    { pix_fmt: 'yuv420p10le' }, { nb_read_frames: '239' }, { nb_read_frames: 240 },
    { r_frame_rate: '60/1' }, { duration: '80.0' }, { duration: 'private' }]) {
    assert.throws(() => av1Facts({ streams: [{ ...stream, ...change }] }));
  }
  for (const streams of [[], [stream, stream], undefined]) assert.throws(() => av1Facts({ streams }));
});

test('AV1 frame reports accept only their fixed dimensions and never broaden the AVC report profile', () => {
  const av1 = { type: 'ended', time: 8, width: 640, height: 360, frames: 238, callback: true, code: 0 };
  assert.deepEqual(reportFact(av1, 'av1'), av1);
  assert.equal(reportFact(av1), null);
  assert.equal(reportFact({ ...av1, width: 864, height: 432 }, 'av1'), null);
  assert.equal(reportFact({ ...av1, token: 'secret' }, 'av1'), null);
  assert.throws(() => reportFact(av1, 'foreign'));
  assert.throws(() => createFixtureServer(Buffer.alloc(1), () => {}, 'foreign'));
});

test('clear fixture must actually decode both observed dimensions in order', () => {
  const frames = Array.from({ length: 240 }, (_, n) => ({ width: 864, height: n < 90 ? 486 : 432 }));
  assert.deepEqual(frameFacts({ frames }), { frames: 240, sizes: ['864x486', '864x432'] });
  assert.throws(() => frameFacts({ frames: frames.slice(0, 190) }));
  assert.throws(() => frameFacts({ frames: frames.toReversed() }));
  assert.throws(() => frameFacts({ frames: frames.map(() => ({ width: 864, height: 486 })) }));
});

test('fixture browser reports retain only bounded frame, time and dimension facts', () => {
  const input = { type: 'frame', time: 3.0011, width: 864, height: 432, frames: 91, callback: true, code: 0 };
  assert.equal(reportFact(input).time, 3.001);
  for (const extra of [{ token: 'secret' }, { time: Infinity }, { height: 720 }, { frames: 10001 }, { type: 'private-error' }]) {
    assert.equal(reportFact({ ...input, ...extra }), null);
  }
});

test('video range requests are bounded single byte ranges', () => {
  assert.deepEqual(rangeFact(undefined, 100), { start: 0, end: 99, partial: false });
  assert.deepEqual(rangeFact('bytes=20-', 100), { start: 20, end: 99, partial: true });
  for (const value of ['bytes=-10', 'bytes=0-100', 'bytes=99-0', 'bytes=0-1,3-4', 'private']) assert.equal(rangeFact(value, 100), null);
});

test('fixture server confines routes, ranges and reports to its fixed origin', async () => {
  const records = [], server = createFixtureServer(Buffer.from('generated-video'), async value => records.push(value));
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const send = (path, method = 'GET', body, headers = {}) => new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port: server.address().port, path, method,
      headers: { Host: '127.0.0.1:18185', ...headers } }, res => {
      let result = ''; res.on('data', c => { result += c; }); res.on('end', () => resolve({ status: res.statusCode, body: result }));
    });
    req.on('error', reject); req.end(body);
  });
  try {
    assert.equal((await send('/')).status, 200);
    assert.equal((await send('/private')).status, 404);
    assert.equal((await send('/', 'GET', undefined, { Host: 'foreign.test' })).status, 403);
    assert.deepEqual(await send('/fixture.mp4', 'GET', undefined, { Range: 'bytes=0-8' }), { status: 206, body: 'generated' });
    assert.equal((await send('/fixture.mp4', 'GET', undefined, { Range: 'bytes=100-' })).status, 416);
    const fact = { type: 'ended', time: 8, width: 864, height: 432, frames: 240, callback: true, code: 0 };
    const headers = { Origin: 'http://127.0.0.1:18185', 'Content-Type': 'application/json' };
    assert.equal((await send('/report', 'POST', JSON.stringify(fact), headers)).status, 204);
    assert.deepEqual(records, [fact]);
    assert.equal((await send('/report', 'POST', JSON.stringify(fact))).status, 403);
    assert.equal((await send('/report', 'POST', JSON.stringify({ ...fact, token: 'secret' }), headers)).status, 400);
    assert.equal((await send('/report', 'POST', 'x'.repeat(2049), headers)).status, 413);
    for (let n = 1; n < 64; n++) await send('/report', 'POST', JSON.stringify(fact), headers);
    assert.equal((await send('/report', 'POST', JSON.stringify(fact), headers)).status, 429);
  } finally { await new Promise(resolve => server.close(resolve)); }
});
