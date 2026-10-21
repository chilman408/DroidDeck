// SPDX-License-Identifier: GPL-3.0-only
const { test } = require('node:test'), assert = require('node:assert/strict');
const { regions, unobstructed, transportCounters, videoMetadata, browserMetadata, requestCounters } = require('./live-capture-region-probe');

test('startup requests retain fixed endpoints and HTTP codes only', () => {
  const counters = requestCounters();
  counters.receive('/api/displaySession?token=secret', 429);
  counters.receive('/api/displayState', 200);
  counters.receive('/api/private-account', 403);
  counters.receive('/api/displayState', NaN);
  assert.deepEqual(counters.snapshot(), { '/api/displaySession': { 429: 1 }, '/api/displayState': { 200: 1 } });
  assert.doesNotMatch(JSON.stringify(counters.snapshot()), /secret|token|private/);
});

test('startup state excludes text, asset URLs and arbitrary values', () => {
  assert.deepEqual(browserMetadata({ readyState: 'complete', hasCanvas: true, hasRendererScript: false,
    hasRoleModal: false, scriptCount: 6, title: 'secret', url: 'https://private/token' }),
  { readyState: 'complete', hasCanvas: true, hasRendererScript: false, hasRoleModal: false, scriptCount: 6 });
  assert.deepEqual(browserMetadata({ readyState: 'secret', hasCanvas: 'secret', hasRendererScript: 1,
    hasRoleModal: null, scriptCount: -1 }),
  { readyState: 'unknown', hasCanvas: false, hasRendererScript: false, hasRoleModal: false, scriptCount: 0 });
});

test('transport diagnostics retain counts, never video or session payloads', () => {
  const counters = transportCounters();
  counters.receive('private-token=https://secret.invalid');
  const packet = Buffer.alloc(16); packet.writeUInt32BE(4, 8);
  packet[0] = 0x40; counters.receive(packet);
  packet[0] = 0x20; counters.receive(packet);
  packet[0] = 0; counters.receive(packet);
  counters.receive(Buffer.alloc(2));
  assert.deepEqual(counters.snapshot(), { textMessages: 1, configPackets: 1,
    keyframes: 1, deltaPackets: 1, invalidPackets: 1 });
  assert.equal(JSON.stringify(counters).includes('secret.invalid'), false);
});

test('video metadata is bounded and excludes arbitrary dataset fields', () => {
  assert.deepEqual(videoMetadata({ videoState: 'ready', videoFrames: '7', videoDecoder: 'legacy',
    videoWidth: '762', videoHeight: '720', privateToken: 'secret' }),
  { state: 'ready', frames: 7, decoder: 'legacy', width: 762, height: 720 });
  assert.deepEqual(videoMetadata({ videoState: 'private-token', videoFrames: '-1',
    videoDecoder: 'https://secret.invalid', videoWidth: '999999', videoHeight: 'NaN' }),
  { state: 'unknown', frames: 0, decoder: 'unknown', width: 0, height: 0 });
});
test('browser role chooser cannot be counted as nonblack Android content', () => {
  const stack = [{ tag: 'DIV', id: 'touch-layer' }, { tag: 'DIV', id: 'display-root' }, { tag: 'BODY', id: '' }];
  assert.equal(unobstructed(stack), true);
  assert.equal(unobstructed([{ tag: 'DIV', id: 'role-modal' }, ...stack]), false);
  assert.equal(unobstructed([{ tag: 'SPAN', id: '' }, ...stack]), false);
  assert.equal(unobstructed([]), false);
});
test('visible navigation does not turn a black app center into a picture pass', () => {
  const data = Buffer.alloc(100 * 100 * 4);
  for (let i = 0; i < 100 * 10; i++) data.fill(255, i * 4, i * 4 + 4);
  const result = regions({ width: 100, height: 100, data });
  assert.deepEqual(result, { whole: .1, center: 0, top: 1, bottom: 0 });
});
test('transparent and near-black pixels remain black; opaque image is nonblack', () => {
  assert.equal(regions({ width: 1, height: 1, data: Buffer.from([255, 255, 255, 0]) }).center, 0);
  assert.equal(regions({ width: 1, height: 1, data: Buffer.from([8, 8, 8, 255]) }).center, 0);
  assert.equal(regions({ width: 1, height: 1, data: Buffer.from([9, 0, 0, 255]) }).center, 1);
});
test('frame metadata and buffer lengths are bounded before analysis', () => {
  for (const value of [null, { width: 1921, height: 1, data: Buffer.alloc(7684) },
    { width: 1, height: 1, data: Buffer.alloc(3) }, { width: -1, height: 1, data: Buffer.alloc(0) }]) {
    assert.throws(() => regions(value), /Invalid decoded/);
  }
});
