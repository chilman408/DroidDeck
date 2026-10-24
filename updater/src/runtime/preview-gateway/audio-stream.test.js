// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
function moduleUnderTest() { assert.ok(fs.existsSync(__dirname + "/audio-stream.js"), "audio stream not implemented"); return require("./audio-stream"); }
function atom(type, payload = Buffer.from([1, 2])) { const b = Buffer.alloc(payload.length + 8); b.writeUInt32BE(b.length); b.write(type, 4); payload.copy(b, 8); return b; }
test("MP4 emits complete initialization and moof/mdat fragments across bytewise splits", () => {
  const { Mp4Fragments } = moduleUnderTest(), output = [];
  const parser = new Mp4Fragments((type, bytes) => output.push([type, bytes]));
  const init = Buffer.concat([atom("ftyp"), atom("moov")]), fragment = Buffer.concat([atom("moof"), atom("mdat")]);
  for (const byte of Buffer.concat([init, fragment, fragment])) parser.push(Buffer.from([byte]));
  assert.deepEqual(output, [["init", init], ["fragment", fragment], ["fragment", fragment]]);
});
test("MP4 rejects oversized, open-ended, out-of-order and duplicate initialization", () => {
  const { Mp4Fragments } = moduleUnderTest();
  for (const bytes of [atom("mdat"), atom("moov"), Buffer.alloc(8),
    Buffer.from([0, 8, 0, 1, 0, 0, 0, 0]), Buffer.concat([atom("ftyp"), atom("ftyp")])]) {
    assert.throws(() => new Mp4Fragments(() => {}).push(bytes), /MP4/);
  }
});
test("continuous raw PCM strips only verified codec metadata and preserves fragmentation", async () => {
  const { RawPcmStream } = moduleUnderTest(), stream = new RawPcmStream(), chunks = [];
  stream.on("data", b => chunks.push(b));
  stream.write(Buffer.from([0, 0x72])); stream.write(Buffer.from([0x61, 0x77, 1, 2])); stream.end(Buffer.from([3, 4]));
  assert.deepEqual(Buffer.concat(chunks), Buffer.from([1, 2, 3, 4]));
  const bad = new RawPcmStream();
  const failed = new Promise(resolve => bad.once("error", resolve)); bad.end(Buffer.alloc(4));
  assert.match((await failed).message, /codec|capture/);
});
module.exports = { atom };
