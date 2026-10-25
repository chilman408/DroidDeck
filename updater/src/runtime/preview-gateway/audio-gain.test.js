// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test');
const assert = require('node:assert/strict');
function bytes(values) { const out = Buffer.alloc(values.length * 2); values.forEach((value, i) => out.writeInt16LE(value, i * 2)); return out; }
async function run(volume, input, splits = [input.length]) {
  const gain = new (require('./audio-gain').PcmGainStream)(() => volume), out = [];
  gain.on('data', chunk => out.push(chunk));
  const ended = new Promise((resolve, reject) => { gain.once('end', resolve); gain.once('error', reject); });
  let offset = 0;
  for (const size of splits) { gain.write(input.subarray(offset, offset + size)); offset += size; }
  gain.end(input.subarray(offset)); await ended; return Buffer.concat(out);
}
test('PCM gain handles stereo byte fragmentation, exact unity, zero and saturating boost without wraparound', async () => {
  const input = bytes([0, 10000, -10000, 30000, -30000, 32767, -32768, 2222]);
  assert.deepEqual(await run(100, input, [1, 3, 5, 2]), input);
  assert.deepEqual(await run(0, input, [1, 1, 1]), Buffer.alloc(input.length));
  assert.deepEqual(await run(50, bytes([10000, -10000, 20000, -20000])), bytes([5000, -5000, 10000, -10000]));
  assert.deepEqual(await run(150, input, [3, 2, 7]), bytes([0, 15000, -15000, 32767, -32768, 32767, -32768, 3333]));
  assert.deepEqual(input, bytes([0, 10000, -10000, 30000, -30000, 32767, -32768, 2222]));
});
test('live gain changes preserve frame order; invalid preferences or incomplete frames fail closed', async () => {
  const { PcmGainStream } = require('./audio-gain'); let volume = 50;
  const stream = new PcmGainStream(() => volume), out = []; stream.on('data', chunk => out.push(chunk));
  stream.write(bytes([10000, -10000])); volume = 150; stream.write(bytes([10000, -10000]));
  stream.end(); await new Promise(resolve => stream.once('end', resolve));
  assert.deepEqual(Buffer.concat(out), bytes([5000, -5000, 15000, -15000]));
  for (const value of [-1, 151, '100', NaN, Infinity]) await assert.rejects(run(value, bytes([1, 2])), /volume/i);
  await assert.rejects(run(100, Buffer.alloc(3)), /frame/i);
  await assert.rejects(run(100, Buffer.alloc(1024 * 1024 + 1)), /limit/i);
});
test('volume provider refreshes privately without restarting capture and mutes corrupt preferences', () => {
  const { createVolumeProvider } = require('./audio-gain'); let now = 0, volume = 150, fail = false, reads = 0;
  const errors = [], provider = createVolumeProvider({ filename: 'fixed', clock: () => now,
    readSettings: () => { reads++; if (fail) throw new Error('private content'); return { enabled: true, volume }; },
    onError: message => errors.push(message) });
  assert.equal(provider(), 150); volume = 50; now = 100; assert.equal(provider(), 150); assert.equal(reads, 1);
  now = 500; assert.equal(provider(), 50); fail = true; now = 1000; assert.equal(provider(), 0);
  assert.equal(errors.length, 1); assert.equal(errors[0].includes('private content'), false);
  fail = false; volume = 100; now = 1500; assert.equal(provider(), 100);
});

test('disabled preferences mute while invalid CLI flags fail closed', () => {
  const { createVolumeProvider, gainEnabled } = require('./audio-gain');
  assert.equal(createVolumeProvider({ readSettings: () => ({ enabled: false, volume: 150 }) })(), 0);
  assert.equal(createVolumeProvider({ readSettings: () => ({ enabled: true, volume: '150' }) })(), 0);
  assert.equal(gainEnabled({}), false); assert.equal(gainEnabled({ TESLA_WAYDROID_AUDIO_PCM_GAIN: '1' }), true);
  for (const value of ['0', 'true', 'yes', '']) assert.throws(() => gainEnabled({ TESLA_WAYDROID_AUDIO_PCM_GAIN: value }), /flag/);
});
