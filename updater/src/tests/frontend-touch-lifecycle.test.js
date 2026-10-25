// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const frontend = process.env.TA_FRONTEND_DIR;
if (!frontend) throw new Error('Set TA_FRONTEND_DIR to the patched pinned frontend');

function fixture() {
  const handlers = new Map(), messages = [], captured = new Set();
  const layer = {
    addEventListener(name, fn) { handlers.set(name, fn); },
    getBoundingClientRect: () => ({ left: 10, top: 20, width: 400, height: 300 }),
    setPointerCapture(id) { captured.add(id); },
    releasePointerCapture(id) { captured.delete(id); },
  };
  const window = { TAHtml: { constants: { TOUCH_SLOT_COUNT: 10 }, log() {} } };
  const context = vm.createContext({ window, ReconnectingWebSocket: function() {
    this.readyState = 1; this.send = payload => messages.push(payload); this.close = () => {};
  } });
  vm.runInContext(fs.readFileSync(path.join(frontend, 'js/touchscreen/touchscreen-controller.js'), 'utf8'), context);
  const controller = new window.TAHtml.TouchscreenController({ touchLayer: layer,
    apiClient: { displaySession: {}, socketUrl: url => url }, canControl: () => true,
    socketUrl: 'ws://localhost/touch', getTouchscreenSize: () => ({ width: 800, height: 600 }) });
  controller.initialize(); controller.touchScreenSocket.onopen();
  return { controller, messages, captured, emit(name, patch = {}) {
    handlers.get(name)?.({ pointerId: 7, pointerType: 'touch', button: 0,
      clientX: 210, clientY: 170, preventDefault() {}, ...patch });
  } };
}
test('one contact cannot allocate two slots or produce two releases', () => {
  const f = fixture();
  f.emit('pointerdown'); f.emit('pointerdown');
  assert.equal(f.messages.length, 1);
  assert.equal(f.controller.slots.filter(slot => slot.trackingId === 7).length, 1);
  f.emit('pointerup'); f.emit('pointerup'); f.emit('lostpointercapture');
  assert.equal(f.messages.length, 2);
  assert.match(f.messages[1], /T -1\na 0/);
  assert.ok(f.controller.slots.every(slot => slot.trackingId === -1));
});
test('unexpected capture loss cancels exactly the active contact', () => {
  const f = fixture(); f.emit('pointerdown'); f.emit('lostpointercapture');
  assert.equal(f.messages.length, 2);
  assert.ok(f.controller.slots.every(slot => slot.trackingId === -1));
  f.emit('pointerup'); assert.equal(f.messages.length, 2);
});
test('secondary mouse buttons never inject Android taps', () => {
  const f = fixture(); f.emit('pointerdown', { pointerType: 'mouse', button: 2 });
  assert.equal(f.messages.length, 0);
});
test('normal tap sends one down/up pair and captured coordinates stay bounded', () => {
  const f = fixture(); f.emit('pointerdown');
  assert.match(f.messages[0], /X 400\nY 300/);
  f.emit('pointermove', { clientX: 900, clientY: 900 });
  assert.match(f.messages[1], /X 799\nY 599/);
  f.emit('pointerup'); assert.equal(f.messages.length, 3);
});
test('two touch pointers retain separate slots when one capture is lost', () => {
  const f = fixture(); f.emit('pointerdown'); f.emit('pointerdown', { pointerId: 8 });
  f.emit('lostpointercapture');
  assert.deepEqual(Array.from(f.controller.slots.filter(slot => slot.trackingId !== -1), slot => slot.trackingId), [8]);
  f.emit('pointerup', { pointerId: 8 });
  assert.ok(f.controller.slots.every(slot => slot.trackingId === -1));
});
