// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const frontend = process.env.TA_FRONTEND_DIR;
if (!frontend) throw new Error('Set TA_FRONTEND_DIR to patched frontend');
function fixture(renderer) {
  let now = 0, id = 0, reconnects = 0;
  const timers = new Map(), listeners = new Map(), messages = [];
  const canvas = { style: {}, dataset: {}, clientWidth: 960, clientHeight: 720, transferControlToOffscreen: () => ({}) };
  const window = { TAHtmlVideoPacketFormat: 'scrcpy', innerWidth: 960, innerHeight: 720,
    location: { protocol: 'https:', href: 'https://example.test/' },
    localStorage: { getItem: () => '-200', setItem() {} },
    addEventListener(name, fn) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(fn); },
    removeEventListener(name, fn) { listeners.get(name)?.delete(fn); },
    dispatchEvent(event) { for (const fn of listeners.get(event.type) || []) fn(event); },
    requestDisplayStreamReconnect() { reconnects++; } };
  const context = vm.createContext({ window, console, URL, ArrayBuffer, Uint8Array, DataView,
    performance: { now: () => now }, CustomEvent: class { constructor(type) { this.type = type; } },
    setTimeout(fn, ms) { timers.set(++id, { fn, due: now + ms }); return id; }, clearTimeout(key) { timers.delete(key); },
    document: { getElementById: name => name === 'canvas' ? canvas : { style: {} } },
    Worker: function() { this.postMessage = value => messages.push(value); this.terminate = () => {}; } });
  for (const file of ['js/audio/audioplayback.js', `js/display/renderers/${renderer}/${renderer === 'h264-broadway' ? 'h264Brodway' : 'h264WebCodecs'}.js`]) {
    vm.runInContext(fs.readFileSync(path.join(frontend, file), 'utf8'), context);
  }
  if (renderer === 'h264-broadway') context.worker.onmessage({ data: { h264QueueReady: 2 } });
  const session = () => context.drawDisplayFrame('{"type":"scrcpy-session","width":960,"height":720}');
  function send(flag) {
    const packet = new ArrayBuffer(18), bytes = new Uint8Array(packet);
    bytes[0] = flag; new DataView(packet).setUint32(8, 6); bytes.set([0, 0, 0, 1, 0x65, 1], 12);
    context.drawDisplayFrame(packet);
  }
  return { context, window, messages, session, send, reconnects: () => reconnects,
    frames: () => messages.filter(value => value.h264Data || value.scrcpyPacket),
    advance(ms) { now += ms; for (;;) { const entry = [...timers].find(([, value]) => value.due <= now); if (!entry) break; timers.delete(entry[0]); entry[1].fn(); } } };
}
for (const renderer of ['h264-broadway', 'h264-webcodecs']) {
  test(`${renderer} applies signed sync to actual encoded packets and cancels old sessions`, () => {
    const f = fixture(renderer); f.session(); f.send(0x40); f.send(0x20);
    assert.equal(f.frames().length, 0); f.advance(199); assert.equal(f.frames().length, 0);
    f.advance(1); assert.equal(f.frames().length, renderer === 'h264-broadway' ? 1 : 2);
    f.send(0); f.session(); f.advance(200);
    assert.equal(f.frames().length, renderer === 'h264-broadway' ? 1 : 2, 'old session must not leak');
    if (renderer === 'h264-broadway') {
      const frame = f.frames()[0];
      f.context.worker.onmessage({ data: { h264Processed: true, h264Epoch: frame.h264Epoch, h264Ticket: frame.h264Ticket } });
    }
    f.window.setAudioDelayMs(0); f.send(0x40); f.send(0x20);
    assert.equal(f.frames().length, renderer === 'h264-broadway' ? 2 : 4);
  });
  test(`${renderer} teardown cancels a pending video packet`, () => {
    const f = fixture(renderer); f.session(); f.send(0x40); f.send(0x20);
    f.window.dispatchEvent({ type: 'beforeunload' }); f.advance(200); assert.equal(f.frames().length, 0);
  });
}
