// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict'), http = require('node:http');
const { createServer, range, report, generateArgs, mediaFacts, DIRECTORY } = require('./generated-playback');
const { foreground, observation } = require('./generated-playback-check');
test('generation is bounded, exclusive, synthetic AVC/AAC only', () => {
  const args = generateArgs(DIRECTORY);
  assert(args.includes('-n')); assert(!args.includes('-y')); assert(args.includes('libx264'));
  assert(args.includes('aac')); assert(args.some(x => x.startsWith('testsrc2=')));
  assert(args.some(x => x.startsWith('sine='))); assert.equal(args.at(-1), DIRECTORY + '/fixture.mp4');
});
test('range rejects traversal, multipart, suffix, overflow and invalid bounds', () => {
  assert.deepEqual(range('bytes=2-4', 6), { start: 2, end: 4, partial: true });
  assert.deepEqual(range(undefined, 6), { start: 0, end: 5, partial: false });
  for (const header of ['bytes=-2', 'bytes=4-2', 'bytes=6-', 'bytes=0-6', 'bytes=0-1,3-4', '../../file', 'bytes=999999999-']) assert.equal(range(header, 6), null);
});
test('media facts reject different codecs, shape and absent audio', () => {
  const value = { streams: [{ codec_type: 'video', codec_name: 'h264', width: 640, height: 360, pix_fmt: 'yuv420p',
    r_frame_rate: '30/1', duration: '8', nb_read_frames: '240' },
  { codec_type: 'audio', codec_name: 'aac', sample_rate: '48000', channels: 2, duration: '8' }] };
  assert.equal(mediaFacts(value).frames, 240);
  value.streams[0].width = 1280; assert.throws(() => mediaFacts(value));
  assert.throws(() => mediaFacts({ streams: [] }));
});
test('reports admit metadata only and reject private fields/nonfinite progress', () => {
  const value = { width: 640, height: 360, time: 1, frames: 30, playing: true, code: 0 };
  assert(report(value));
  for (const extra of [{ time: NaN }, { code: 4 }, { frames: 1.5 }, { width: 1280 }, { account: 'generated forbidden sentinel' }]) assert.equal(report({ ...value, ...extra }), null);
});
test('foreground fence rejects setup wizard, absent or conflicting foreground', () => {
  assert(foreground(' topResumedActivity=ActivityRecord{abc u0 org.lineageos.jelly/.MainActivity t2}'));
  assert(!foreground(' topResumedActivity=ActivityRecord{abc u0 com.google.android.setupwizard/.MainActivity t2}'));
  assert(!foreground(''));
  assert(!foreground('mResumedActivity: org.lineageos.jelly/.MainActivity\ntopResumedActivity=foreign/.Activity'));
});
test('pixel acceptance requires real moving colorful decoded frames', () => {
  const value = { kind: 'generated-playback-pixels', frames: 20, colorFrames: 18, motionSamples: 10, afterH264Decode: true, savedPixels: false, verified: true };
  assert(observation(value));
  for (const change of [{ savedPixels: true }, { motionSamples: 0 }, { colorFrames: 0 }, { afterH264Decode: false }, { frames: 200 }, { verified: false }]) assert.throws(() => observation({ ...value, ...change }));
  assert.equal(observation({ ...value, frames: 0, colorFrames: 0, motionSamples: 0, verified: false }, true).verified, false);
  assert.throws(() => observation({ ...value, privateField: 'generated forbidden sentinel' }, true));
});
test('private fixture server scopes ranges, reports, expiry and paths', async t => {
  let now = 10000;
  const server = createServer(Buffer.from('generated-public-video'), { origin: 'http://127.0.0.1:18186', clients: ['127.0.0.1'], now: () => now });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve); }));
  const request = (method, url, body, headers = {}) => new Promise((resolve, reject) => {
    const r = http.request({ host: '127.0.0.1', port: server.address().port, path: url, method,
      headers: { Host: '127.0.0.1:18186', ...headers } }, response => {
      let data = ''; response.on('data', b => data += b); response.on('end', () => resolve({ status: response.statusCode, data }));
    }); r.on('error', reject); r.end(body);
  });
  assert.equal((await request('GET', '/../../secret')).status, 404);
  assert.equal((await request('GET', '/fixture.mp4', null, { Range: 'bytes=0-8' })).status, 206);
  assert.equal((await request('GET', '/fixture.mp4', null, { Range: 'bytes=-8' })).status, 416);
  assert.equal((await request('GET', '/status', null, { Host: 'foreign.invalid' })).status, 403);
  const post = frames => request('POST', '/event', JSON.stringify({ width: 640, height: 360, time: frames / 30, frames, code: 0, playing: true }),
    { Origin: 'http://127.0.0.1:18186', 'Content-Type': 'application/json' });
  await post(30); assert.equal(JSON.parse((await request('GET', '/status')).data).active, false);
  await post(60); assert.equal(JSON.parse((await request('GET', '/status')).data).active, true);
  now += 1501; assert.equal(JSON.parse((await request('GET', '/status')).data).active, false);
  assert.equal((await request('POST', '/event', '{}', { 'Content-Type': 'application/json' })).status, 403);
  assert.equal((await request('POST', '/event', 'x'.repeat(513), { Origin: 'http://127.0.0.1:18186', 'Content-Type': 'application/json' })).status, 413);
});
