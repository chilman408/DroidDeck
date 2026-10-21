// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const { ScrcpyStream, MAX_PACKET } = require("./scrcpy-stream");
function session(width = 390, height = 844) {
  const b = Buffer.alloc(12); b.writeUInt32BE(0x80000000); b.writeUInt32BE(width, 4); b.writeUInt32BE(height, 8); return b;
}
function packet(flags = 0x20, payload = Buffer.from([0, 0, 0, 1, 0x65, 1])) {
  const b = Buffer.alloc(12 + payload.length); b[0] = flags; b.writeUInt32BE(payload.length, 8); payload.copy(b, 12); return b;
}
test("pinned scrcpy v4 protocol survives byte-wise fragmentation and successive resize sessions", () => {
  const events = [], stream = new ScrcpyStream(event => events.push(event));
  const bytes = Buffer.concat([Buffer.from("h264"), session(), packet(0x40), packet(), session(960, 640), packet()]);
  for (const byte of bytes) stream.push(Buffer.from([byte]));
  assert.deepEqual(events.map(e => e.type), ["session", "frame", "frame", "session", "frame"]);
  assert.equal(events[0].height, 844); assert.equal(events[3].width, 960);
  assert.equal(events[1].config, true); assert.equal(events[2].key, true);
  assert.deepEqual(events[2].packet, packet()); stream.end();
});
test("rejects foreign codec, unsafe dimensions, frame-before-session, oversized and truncated packets", () => {
  for (const bytes of [Buffer.from("h265"), Buffer.concat([Buffer.from("h264"), session(1920, 1920)]),
    Buffer.concat([Buffer.from("h264"), packet()])]) {
    assert.throws(() => new ScrcpyStream(() => {}).push(bytes));
  }
  const bad = packet(); bad.writeUInt32BE(MAX_PACKET + 1, 8);
  assert.throws(() => new ScrcpyStream(() => {}).push(Buffer.concat([Buffer.from("h264"), session(), bad])), /size/i);
  const stream = new ScrcpyStream(() => {}); stream.push(Buffer.concat([Buffer.from("h264"), session(), packet().subarray(0, 14)]));
  assert.throws(() => stream.end(), /truncated/i);
});
module.exports = { session, packet };
