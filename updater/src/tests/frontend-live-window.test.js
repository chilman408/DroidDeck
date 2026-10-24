// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const frontend = process.env.TA_FRONTEND_DIR;
if (!frontend) throw Error('Set TA_FRONTEND_DIR to reconstructed frontend');
async function fixture() {
  const sockets = [], frames = [];
  const window = { location: { href: 'https://example.test/', search: '' }, localStorage: { getItem: () => null },
    TAHtml: { log() {}, utils: {} }, drawDisplayFrame: data => frames.push(data) };
  const context = vm.createContext({ window, URL, URLSearchParams, ArrayBuffer,
    document: { getElementById: () => ({ transferControlToOffscreen() {} }) },
    ReconnectingWebSocket: function(url, protocols) {
      this.url = url; this.protocols = protocols; this.readyState = 1; this.sent = [];
      this.send = value => this.sent.push(JSON.parse(value)); this.close = () => { this.readyState = 3; };
      sockets.push(this);
    } });
  vm.runInContext(fs.readFileSync(path.join(frontend, 'js/display/display-renderer-manager.js'), 'utf8'), context);
  const manager = new window.TAHtml.DisplayRendererManager({ displayRoot: {} });
  manager._ensureRendererScript = async () => {};
  const open = async url => manager.ensureDisplaySocket({ displayWebSocket: url || 'wss://example.test/websocket/display',
    remoteDisplayState: { renderer: 0, streamFormats: ['h264'] } });
  await open(); const socket = sockets[0]; socket.onopen();
  return { manager, sockets, socket, frames, open, window };
}
test('H264 negotiates receipt-bounded transport and acknowledges only complete binary messages', async () => {
  const f = await fixture();
  assert.equal(f.socket.protocols, 'droiddeck-live-v1');
  f.socket.onmessage({ data: JSON.stringify({ type: 'scrcpy-flow-frame', id: 7 }) });
  assert.equal(f.frames.length, 0); assert.equal(f.socket.sent.length, 0);
  const data = new ArrayBuffer(17); f.socket.onmessage({ data });
  assert.equal(f.frames[0], data);
  assert.deepEqual(f.socket.sent, [{ type: 'scrcpy-flow-ack', id: 7 }]);
  f.socket.onmessage({ data }); assert.equal(f.socket.sent.length, 1);
});
test('reconnect and replacement cannot reuse a prior packet receipt', async () => {
  const f = await fixture();
  const marker = JSON.stringify({ type: 'scrcpy-flow-frame', id: 4 });
  f.socket.onmessage({ data: marker }); f.socket.onclose(); f.socket.readyState = 1; f.socket.onopen();
  f.socket.onmessage({ data: new ArrayBuffer(17) }); assert.equal(f.socket.sent.length, 0);
  f.socket.onmessage({ data: marker }); await f.open('wss://example.test/other');
  f.socket.onmessage({ data: new ArrayBuffer(17) }); assert.equal(f.socket.sent.length, 0);
  assert.equal(f.frames.length, 1, 'Retired socket cannot draw obsolete video');
});
test('session metadata and untagged legacy packets remain compatible', async () => {
  const f = await fixture(), session = '{"type":"scrcpy-session","width":960,"height":720}';
  f.socket.onmessage({ data: session }); f.socket.onmessage({ data: new ArrayBuffer(17) });
  assert.equal(f.frames.length, 2); assert.equal(f.frames[0], session); assert.equal(f.socket.sent.length, 0);
});
