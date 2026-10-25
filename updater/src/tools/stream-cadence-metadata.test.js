// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { TimingSeries, StreamCadence, workerFacts } = require('./stream-cadence-metadata');
function packet(pts, flag = 0) {
  const data = Buffer.alloc(13); data.writeBigUInt64BE(BigInt(pts), 0); data[0] |= flag; data.writeUInt32BE(1, 8); return data;
}
test('cadence separates arrival gaps from source timestamps and ignores configuration', () => {
  const cadence = new StreamCadence();
  cadence.receive(packet(0, 0x40), 0);
  cadence.receive(packet(0, 0x20), 10); cadence.receive(packet(33333), 11); cadence.receive(packet(66666), 200);
  const facts = cadence.snapshot();
  assert.equal(facts.frames, 3); assert.equal(facts.configurations, 1);
  assert.equal(facts.arrival.maxMs, 189); assert.equal(facts.arrival.gapsOver100Ms, 1);
  assert.equal(facts.sourcePts.maxMs, 33.3); assert.equal(facts.sourcePts.samples, 2);
});
test('session reset never measures a cross-stream source delta', () => {
  const cadence = new StreamCadence();
  cadence.receive(packet(999999), 5); cadence.receive('{"type":"scrcpy-session","private":"secret"}', 10);
  cadence.receive(packet(0), 20); cadence.receive(packet(0), 30);
  assert.equal(cadence.snapshot().timestampDiscontinuities, 1);
  assert.equal(cadence.snapshot().sourcePts.samples, 0);
  assert.equal(JSON.stringify(cadence.snapshot()).includes('secret'), false);
});
test('malformed or unsafe timestamps never enter timing series', () => {
  const cadence = new StreamCadence();
  for (const value of [null, Buffer.alloc(0), Buffer.alloc(13), packet(Number.MAX_SAFE_INTEGER + 1)]) cadence.receive(value, 10);
  cadence.receive(packet(1), NaN);
  assert.equal(cadence.snapshot().invalid, 5); assert.equal(cadence.snapshot().frames, 0);
});
test('timing storage is bounded and reports numeric percentiles only', () => {
  const series = new TimingSeries();
  for (const value of [-1, NaN, Infinity, 'secret', 60001]) series.add(value);
  for (let i = 0; i < 5000; i++) series.add(i);
  assert.equal(series.snapshot().samples, 4096);
  assert.equal(series.snapshot().maxMs, 4095);
  assert.equal(series.snapshot().p95Ms, 3891);
  const facts = workerFacts({ ack: [2, 9, 'secret'], draw: [33.33], animation: [16.67], maximumWorkerPackets: 2,
    maximumBrowserFrames: -1, draws: 3, failed: false, url: 'secret', token: 'secret' });
  assert.equal(facts.decodeAck.samples, 2); assert.equal(facts.maximumBrowserFrames, 0);
  assert.equal(JSON.stringify(facts).includes('secret'), false);
});
