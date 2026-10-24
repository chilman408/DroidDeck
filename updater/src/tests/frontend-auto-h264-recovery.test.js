// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const frontend = process.env.TA_FRONTEND_DIR;
if (!frontend) throw new Error('Set TA_FRONTEND_DIR to the patched frontend');

function fixture({ recover = true, workerFails = false } = {}) {
  const timers = new Map(); let timerId = 0, recoveries = 0, reconnects = 0;
  const canvas = { style: {}, dataset: {}, clientWidth: 980, clientHeight: 720,
    transferControlToOffscreen: () => ({}) };
  const window = { TAHtmlVideoPacketFormat: 'scrcpy', innerWidth: 980, innerHeight: 720,
    addEventListener() {}, location: { protocol: 'https:' },
    TAHtml: { browserRenderer: { recover() { recoveries++; return recover; } } },
    requestDisplayStreamReconnect() { reconnects++; } };
  const context = vm.createContext({ window, console: { error() {}, warn() {} }, URL, Number, ArrayBuffer,
    document: { getElementById: id => id === 'canvas' ? canvas : { style: {} } },
    clearTimeout: id => timers.delete(id), setTimeout: (callback, delay) => {
      timers.set(++timerId, { callback, delay }); return timerId;
    }, Worker: function() { if (workerFails) throw Error('Worker unavailable'); this.postMessage = () => {}; } });
  vm.runInContext(fs.readFileSync(path.join(frontend,
    'js/display/renderers/h264-webcodecs/h264WebCodecs.js'), 'utf8'), context);
  function tick(delay) {
    for (const [id, timer] of [...timers]) if (timer.delay === delay) { timers.delete(id); timer.callback(); }
  }
  return { context, canvas, timers, tick, recoveries: () => recoveries, reconnects: () => reconnects };
}

test('automatic WebCodecs worker errors recover H264 instead of reconnecting the broken decoder', () => {
  for (const error of ['message', 'worker']) {
    const f = fixture();
    if (error === 'message') f.context.worker.onmessage({ data: { error: 'Decoder rejected configuration' } });
    else f.context.worker.onerror({ message: 'Worker failed' });
    assert.equal(f.recoveries(), 1);
    f.tick(1000);
    assert.equal(f.reconnects(), 0);
    assert.equal(f.canvas.dataset.videoState, 'recovering-legacy');
  }
});

test('worker construction failure is handled after renderer initialization', () => {
  const f = fixture({ workerFails: true }); f.tick(0);
  assert.equal(f.recoveries(), 1);
  assert.equal(f.canvas.dataset.videoState, 'recovering-legacy');
});

test('startup fallback requires H264 packets and no decoded output, not a missing transport', () => {
  const offline = fixture(); offline.tick(30000);
  assert.equal(offline.recoveries(), 0, 'No transport is not evidence of decoder incompatibility');
  offline.tick(1000); assert.equal(offline.reconnects(), 1);
  const active = fixture(); active.context.drawDisplayFrame(new ArrayBuffer(13)); active.tick(30000);
  assert.equal(active.recoveries(), 1);
  active.tick(1000); assert.equal(active.reconnects(), 0);
});

test('a previously rendered screen retains ordinary H264 stall recovery, including resize grace', () => {
  const f = fixture();
  f.context.worker.onmessage({ data: { frameRendered: true, width: 980, height: 720 } });
  f.context.drawDisplayFrame(new ArrayBuffer(13));
  assert.ok([...f.timers.values()].some(timer => timer.delay > 20000));
  f.tick(30000); assert.equal(f.recoveries(), 0);
  f.tick(1000); assert.equal(f.reconnects(), 1);
});

test('explicit WebCodecs retains reconnect behavior when browser policy declines fallback', () => {
  const f = fixture({ recover: false });
  f.context.worker.onmessage({ data: { error: 'Decoder failed' } });
  f.tick(1000); assert.equal(f.reconnects(), 1);
  assert.equal(f.canvas.dataset.videoState, 'reconnecting');
});

test('invalid metadata cannot trigger an automatic renderer change', () => {
  const f = fixture(); f.context.drawDisplayFrame('{"type":"scrcpy-session","width":0}');
  assert.equal(f.recoveries(), 0);
  f.tick(1000); assert.equal(f.reconnects(), 1);
});
