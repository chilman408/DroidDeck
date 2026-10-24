// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const policy = fs.readFileSync(path.join(__dirname, 'webcodecs-worker-policy.js'), 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));
function fixture() {
  const requests = [], configs = [], decoded = [], errors = [];
  const context = vm.createContext({ configs, decoded, Promise,
    VideoDecoder: { isConfigSupported: config => new Promise((resolve, reject) => requests.push({ config, resolve, reject })) },
    self: { postMessage: value => { if (value.error) errors.push(value); } } });
  vm.runInContext(`
    let decoder = { state: 'unconfigured' }, spsPps = null;
    function buildDecoderConfig() { return { codec:'avc1.42c028', hardwareAcceleration:'prefer-hardware', optimizeForLatency:true }; }
    function configureDecoderFromSpsPps() { configs.push(buildDecoderConfig(spsPps)); decoder.state='configured'; }
    self.onmessage = event => {
      if (event.data.scrcpySession) { decoder.state='unconfigured'; spsPps=null; }
      if (event.data.configure) { spsPps=[1]; configureDecoderFromSpsPps(); }
      if (event.data.scrcpyPacket && decoder.state==='configured') decoded.push(event.data.scrcpyPacket);
    };`, context);
  vm.runInContext(policy, context);
  return { context, requests, configs, decoded, errors, send: data => context.self.onmessage({ data }) };
}
test('no-preference is checked before configure, with keyframes retained until admission', async () => {
  const f = fixture();
  f.send({ configure: true }); f.send({ scrcpyPacket: new ArrayBuffer(16) });
  await settle();
  assert.equal(f.configs.length, 0); assert.equal(f.decoded.length, 0);
  assert.equal(f.requests[0].config.hardwareAcceleration, 'no-preference');
  f.requests[0].resolve({ supported: true }); await settle();
  assert.equal(f.configs[0].hardwareAcceleration, 'no-preference');
  assert.equal(f.decoded.length, 1); assert.deepEqual(f.errors, []);
});
test('unsupported or rejected configurations report a bounded legacy recovery signal', async () => {
  for (const reject of [false, true]) {
    const f = fixture(); f.send({ configure: true }); await settle();
    if (reject) f.requests[0].reject(new Error('generated capability failure'));
    else f.requests[0].resolve({ supported: false });
    await settle(); f.send({ scrcpyPacket: new ArrayBuffer(16) });
    assert.equal(f.configs.length, 0); assert.equal(f.decoded.length, 0);
    assert.equal(f.errors.length, 1); assert.match(f.errors[0].error, /legacy H264/);
  }
});
test('new stream session invalidates old capability promises and queued video', async () => {
  const f = fixture(); f.send({ configure: true }); f.send({ scrcpyPacket: new ArrayBuffer(16) }); await settle();
  f.send({ scrcpySession: { width: 720, height: 480 } });
  f.requests[0].resolve({ supported: true }); await settle();
  assert.equal(f.configs.length, 0); assert.equal(f.decoded.length, 0);
  f.send({ configure: true }); await settle(); f.requests[1].resolve({ supported: true }); await settle();
  assert.equal(f.configs.length, 1); assert.deepEqual(f.errors, []);
});
test('pending capability check cannot retain unbounded packets or bytes', async () => {
  for (const packet of [new ArrayBuffer(2097153), null]) {
    const f = fixture(); f.send({ configure: true }); await settle();
    if (packet) f.send({ scrcpyPacket: packet });
    else for (let i = 0; i < 121; i++) f.send({ scrcpyPacket: new ArrayBuffer(16) });
    assert.equal(f.errors.length, 1);
    f.requests[0].resolve({ supported: true }); await settle();
    assert.equal(f.configs.length, 0); assert.equal(f.decoded.length, 0);
  }
});
