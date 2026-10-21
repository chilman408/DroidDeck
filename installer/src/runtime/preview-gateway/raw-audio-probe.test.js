// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
function probe() {
  assert.ok(fs.existsSync(path.join(__dirname, "raw-audio-probe.js")), "bounded raw audio probe is not implemented");
  return require("./raw-audio-probe");
}
test("raw codec and stereo PCM tolerate fragmentation without retaining sound", () => {
  const { RawAudioStats } = probe();
  const stats = new RawAudioStats();
  const bytes = Buffer.from([0, 0x72, 0x61, 0x77, 1, 0, 0, 0x80, 0, 0, 0xff, 0x7f]);
  for (const byte of bytes) stats.push(Buffer.from([byte]));
  assert.deepEqual(stats.finish(), { bytes: 8, frames: 2, seconds: 2 / 48000, nonzeroSamples: 3, peak: 32768 });
});
test('optional observer receives complete stereo frames across arbitrary transport splits', () => {
  const { RawAudioStats } = probe();
  const bytes = Buffer.from([0, 0x72, 0x61, 0x77, 1, 0, 0, 0x80, 0, 0, 0xff, 0x7f]);
  for (const size of [1, 3, 4, 5, 12]) {
    const frames = [], stats = new RawAudioStats((left, right) => frames.push([left, right]));
    for (let i = 0; i < bytes.length; i += size) stats.push(bytes.subarray(i, i + size));
    assert.deepEqual(frames, [[1, -32768], [0, 32767]]); assert.equal(stats.finish().frames, 2);
  }
  assert.throws(() => new RawAudioStats('not a callback'));
});
test("disabled, foreign, truncated and excessive streams fail instead of reporting silence", () => {
  const { RawAudioStats } = probe();
  for (const header of [Buffer.alloc(4), Buffer.from([0, 0, 0, 1]), Buffer.from("opus")]) {
    assert.throws(() => new RawAudioStats().push(header), /codec|capture/i);
  }
  const empty = new RawAudioStats();
  assert.throws(() => empty.finish(), /empty|truncated/i);
  const odd = new RawAudioStats(); odd.push(Buffer.from([0, 0x72, 0x61, 0x77, 0]));
  assert.throws(() => odd.finish(), /truncated/i);
  const huge = new RawAudioStats();
  assert.throws(() => huge.push(Buffer.alloc(2 * 1024 * 1024 + 5)), /limit/i);
});
test("probe closes capture at its deadline and rejects early EOF or socket failure", async () => {
  const { PassThrough } = require("node:stream");
  const { runProbe } = probe();
  const socket = new PassThrough();
  const done = runProbe(() => socket, 20);
  socket.write(Buffer.from([0, 0x72, 0x61, 0x77, 1, 0, 0, 0]));
  assert.equal((await done).frames, 1);
  assert.equal(socket.destroyed, true);
  const early = new PassThrough(), eof = runProbe(() => early, 20);
  early.end(Buffer.from([0, 0x72, 0x61, 0x77, 0, 0, 0, 0]));
  await assert.rejects(eof, /ended|closed/i);
  const failed = new PassThrough(), failure = runProbe(() => failed, 20);
  failed.destroy(new Error("socket failure"));
  await assert.rejects(failure, /socket failure/);
});
test('bounded probe can add aggregate analysis without changing the normal wire parser', async () => {
  const { PassThrough } = require('node:stream'), { RawAudioStats, runProbe } = probe();
  let observed = 0; const stats = new RawAudioStats(() => observed++), socket = new PassThrough();
  const done = runProbe(() => socket, 20, stats);
  socket.write(Buffer.from([0, 0x72, 0x61, 0x77, 1, 0, 0, 0]));
  assert.equal((await done).frames, 1); assert.equal(observed, 1); assert.equal(socket.destroyed, true);
});
