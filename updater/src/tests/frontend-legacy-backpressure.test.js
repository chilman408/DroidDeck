// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const frontend = process.env.TA_FRONTEND_DIR;
if (!frontend) throw new Error('Set TA_FRONTEND_DIR to the patched frontend');
function packet(flag, nal = 0x41) {
  const buffer = new ArrayBuffer(18), bytes = new Uint8Array(buffer);
  bytes[0] = flag; new DataView(buffer).setUint32(8, 6);
  bytes.set([0, 0, 0, 1, nal, 1], 12); return buffer;
}
function fixture(ready = true, href = 'https://example.test/') {
  const timers = [], reloads = [];
  const messages = [], canvas = { style: {}, dataset: {}, clientWidth: 960, clientHeight: 720, transferControlToOffscreen: () => ({}) };
  const context = vm.createContext({ Uint8Array, ArrayBuffer, DataView, console, URL,
    setTimeout: fn => { const timer = { fn, active: true }; timers.push(timer); return timer; },
    clearTimeout: timer => { if (timer) timer.active = false; },
    window: { TAHtmlVideoPacketFormat: 'scrcpy', innerWidth: 960, innerHeight: 720, addEventListener() {}, location: { protocol: 'https:', href, replace: value => reloads.push(value) } },
    document: { getElementById: name => name === 'canvas' ? canvas : { style: {} } },
    Worker: function(url) { this.url = url; this.postMessage = value => messages.push(value); this.terminate = () => { this.terminated = true; }; } });
  vm.runInContext(fs.readFileSync(path.join(frontend, 'js/display/renderers/h264-broadway/h264Brodway.js'), 'utf8'), context);
  const start = () => { context.drawDisplayFrame('{"type":"scrcpy-session","width":960,"height":720}'); context.drawDisplayFrame(packet(0x40, 0x67)); };
  start(); if (ready) context.worker.onmessage({ data: { h264QueueReady: 2 } });
  return { context, messages, start, reloads, tick: () => { const timer = timers.find(value => value.active); if (timer) { timer.active = false; timer.fn(); } }, frames: () => messages.filter(value => value.h264Data),
    send: key => context.drawDisplayFrame(packet(key ? 0x20 : 0, key ? 0x65 : 0x41)),
    ack: frame => context.worker.onmessage({ data: { h264Processed: true, h264Epoch: frame.h264Epoch, h264Ticket: frame.h264Ticket } }) };
}
test('stalled legacy decoder receives at most two encoded packets', () => {
  const f = fixture(); f.send(true); for (let i = 0; i < 40; i++) f.send(false);
  assert.equal(f.frames().length, 2, 'Render-queue limits must also bound the worker inbox');
});
test('a complete idle cached GOP drains even without future frame arrivals', () => {
  const f = fixture(); f.send(true); for (let i = 1; i < 17; i++) f.send(false);
  let consumed = 0;
  while (consumed < f.frames().length) f.ack(f.frames()[consumed++]);
  assert.equal(consumed, 17, 'The final static picture must not depend on a future IDR');
  assert.equal(new Uint8Array(f.frames()[0].h264Data).length, 12);
});
test('duplicate acknowledgements never create extra worker capacity', () => {
  const f = fixture(); f.send(true); f.send(false); const old = f.frames().slice();
  f.ack(old[0]); f.ack(old[0]); f.send(false); f.send(false);
  assert.equal(f.frames().length, 3, 'A duplicate ACK cannot release two slots');
  f.ack(old[0]); assert.equal(f.frames().length, 3);
});
test('twenty session resets cannot exceed the aggregate physical inbox cap', () => {
  const f = fixture(); f.send(true); f.send(false);
  for (let i = 0; i < 20; i++) { f.start(); f.send(true); f.send(false); }
  assert.equal(f.frames().length, 2);
  const first = f.frames().slice(); first.forEach(f.ack);
  assert.equal(f.frames().length, 4, 'Only the newest complete session is retained');
  first.forEach(f.ack); assert.equal(f.frames().length, 4, 'Duplicate old ACK cannot release new packets');
});
test('worker pairing uses a versioned URL', () => {
  const f = fixture(); assert.match(f.context.worker.url, /[?]taQueue=2$/);
});
test('a mismatched worker receives no encoded data and recovery cannot reload-loop', () => {
  const f = fixture(false); f.send(true); f.send(false); assert.equal(f.frames().length, 0);
  f.tick(); assert.equal(f.reloads.length, 1); assert.match(f.reloads[0], /taLegacyQueue=2/);
  const retried = fixture(false, 'https://example.test/?taLegacyQueue=2'); retried.tick();
  assert.equal(retried.reloads.length, 0);
});
test('the maximum relay cached GOP completes without future motion', () => {
  const f = fixture(); f.send(true); for (let i = 1; i < 120; i++) f.send(false);
  let consumed = 0; while (consumed < f.frames().length) f.ack(f.frames()[consumed++]);
  assert.equal(consumed, 120);
});
test('new live keyframes replace unsent stale GOPs without losing decoder configuration', () => {
  const f = fixture(); f.send(true); for (let i = 0; i < 20; i++) f.send(false);
  f.send(true); f.send(false);
  let consumed = 0; while (consumed < f.frames().length) f.ack(f.frames()[consumed++]);
  assert.equal(consumed, 4);
  assert.equal(new Uint8Array(f.frames()[2].h264Data)[4], 0x67);
});
test('the worker acknowledges consumed packets even when no picture is emitted', () => {
  const messages = [];
  const context = vm.createContext({ console, Uint8Array, importScripts() {}, self: { postMessage: value => messages.push(value), setTimeout() {} },
    Decoder: function() { this.decode = () => {}; }, YUVCanvas: function() {} });
  vm.runInContext(fs.readFileSync(path.join(frontend, 'js/display/renderers/h264-broadway/h264BrodwayWorker.js'), 'utf8'), context);
  context.self.onmessage({ data: { h264Data: packet(0, 0x41), h264Epoch: 3, h264Ticket: 7 } });
  assert.equal(messages.length, 1); assert.equal(messages[0].h264Processed, true);
  assert.equal(messages[0].h264Epoch, 3); assert.equal(messages[0].h264Ticket, 7);
});

test('lost ACK after readiness cannot hold a reconnect behind an infinite reset barrier', () => {
  const f = fixture(); f.send(true); f.send(false);
  f.start(); f.send(true); f.tick(); f.tick();
  assert.equal(f.reloads.length, 1);
  assert.equal(f.context.worker.terminated, true);
  assert.equal(f.context.canvas.dataset.videoState, 'worker-stalled');
  f.context.worker.onmessage({ data: { h264QueueReady: 2 } }); f.send(true);
  assert.equal(f.frames().length, 2, 'A failed worker cannot consume further packets');
});

test('a permanently lost old ACK expires even if newer packets keep acknowledging', () => {
  const f = fixture(); f.send(true); f.send(false);
  for (let i = 0; i < 20; i++) { f.ack(f.frames().at(-1)); f.send(false); }
  f.tick(); f.tick();
  assert.equal(f.reloads.length, 1, 'New ACKs must not renew the oldest lost ticket');
});

test('fatal worker errors use the same bounded recovery and never reload-loop', () => {
  const f = fixture(); f.send(true); f.context.worker.onerror?.({ preventDefault() {} });
  assert.equal(f.reloads.length, 1); assert.equal(f.context.worker.terminated, true);
  const retried = fixture(true, 'https://example.test/?taLegacyQueue=2');
  retried.context.worker.onmessageerror?.({ preventDefault() {} });
  assert.equal(retried.reloads.length, 0);
  assert.equal(retried.context.canvas.dataset.videoState, 'worker-error');
});
