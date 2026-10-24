// SPDX-License-Identifier: GPL-3.0-only
const { test } = require('node:test'), assert = require('node:assert/strict');
const { sampleAppleTransition } = require('./playback-monitor');

test('bounded sampler coalesces unchanged metadata and finishes without Android changes', async () => {
  const records = [], waits = []; let time = 0, samples = 0;
  const result = await sampleAppleTransition({
    inspect: async () => { samples++; return { version: 1, events: [], formatEvents: [] }; },
    write: async text => records.push(JSON.parse(text)), now: () => time,
    wait: async ms => { waits.push(ms); time += 20000; }
  });
  assert.equal(samples, 9); assert.equal(records.length, 2);
  assert.deepEqual(records[0].metadata, { version: 1, events: [], formatEvents: [] });
  assert.equal(records[1].phase, 'complete'); assert.equal(result.samples, 9);
  assert.ok(waits.every(ms => ms === 2000));
});

test('sampler never persists exceptions, stops at the sample bound even if clock does not advance', async () => {
  const records = [];
  const result = await sampleAppleTransition({ inspect: async () => { throw new Error('token=secret private.example'); },
    write: async text => records.push(text), now: () => 0, wait: async () => {} });
  assert.equal(result.samples, 90);
  assert.equal(records.length, 2);
  assert.doesNotMatch(records.join(''), /secret|token|private/);
  assert.equal(JSON.parse(records[0]).available, false);
});

test('sampler bounds persisted metadata and does not disguise a failed write as completion', async () => {
  const records = []; let counter = 0;
  const result = await sampleAppleTransition({ inspect: async () => ({ version: 1, bounded: 'x'.repeat(200000), counter: counter++ }),
    write: async text => records.push(text), now: () => 0, wait: async () => {} });
  assert.equal(result.truncated, true);
  assert.ok(Buffer.byteLength(records.join('')) <= 2 * 1024 * 1024);
  assert.equal(JSON.parse(records.at(-1)).phase, 'truncated');
  await assert.rejects(sampleAppleTransition({ inspect: async () => ({}),
    write: async () => { throw new Error('disk unavailable'); }, now: () => 0, wait: async () => {} }), /disk unavailable/);
});
