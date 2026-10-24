// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test');
const assert = require('node:assert/strict');
const { inputFacts, socketFacts, inspect } = require('./touch-path-metadata');

test('dispatcher facts omit app names, coordinates and raw input records', () => {
  const result = inputFacts('secret.app\nDispatchEnabled: true\nDispatchFrozen: false\nPendingEvent: <none>\nInboundQueue: <empty>\n');
  assert.deepEqual(result, { enabled: true, frozen: false, pending: false, inboundLength: 0 });
  assert.ok(!JSON.stringify(result).includes('secret'));
});
test('pending input and a nonempty queue are visible without event content', () => {
  assert.deepEqual(inputFacts('DispatchEnabled: true\nDispatchFrozen: false\nPendingEvent: MotionEvent(x=123)\nInboundQueue: length=4\n'),
    { enabled: true, frozen: false, pending: true, inboundLength: 4 });
});
test('ambiguous dispatcher and unknown queue formats are not reported as healthy', () => {
  assert.throws(() => inputFacts('DispatchEnabled: true\nDispatchEnabled: false\nDispatchFrozen: false'), /dispatcher/);
  assert.equal(inputFacts('DispatchEnabled: true\nDispatchFrozen: false\nInboundQueue: new format').inboundLength, null);
});
test('socket counters omit endpoints and separate local service boundaries', () => {
  const result = socketFacts('ESTAB 0 64 127.0.0.1:9999 127.0.0.1:45000\nESTAB 3 0 192.168.240.112:8443 192.168.240.1:52000\n');
  assert.deepEqual(result.touch, { connections: 1, receiveBytes: 0, sendBytes: 64 });
  assert.deepEqual(result.gateway, { connections: 1, receiveBytes: 3, sendBytes: 0 });
  assert.ok(!JSON.stringify(result).includes('192.168'));
});
test('malformed and oversized socket responses fail closed', () => {
  assert.throws(() => socketFacts('ESTAB nope 0 host:9999 peer:2'), /socket/);
  assert.throws(() => socketFacts('x'.repeat(1048577)), /socket/);
});
test('absence of clients is explicit, not proof of input responsiveness', () => {
  assert.equal(socketFacts('').touch.connections, 0);
});
test('valid disconnect states and IPv6 endpoints retain reconnect diagnostics', () => {
  const result = socketFacts('FIN-WAIT-1 0 0 [::1]:9999 [::1]:45000\nFIN-WAIT-2 0 0 [::1]:8443 [::1]:52000');
  assert.equal(result.touch.connections, 1); assert.equal(result.gateway.connections, 1);
});
test('container replacement refuses a mixed-identity result', async () => {
  let identities = 0;
  const run = async (file, args) => Buffer.from(file.endsWith('lxc-info') ? '42' :
    args.includes('input') ? 'DispatchEnabled: true\nDispatchFrozen: false\nPendingEvent: <none>\nInboundQueue: <empty>' : '');
  await assert.rejects(inspect({ run, identity: async () => String(++identities), androidIdentity: async () => 'stable' }), /identity changed/);
});
test('Android service replacement within the same container also refuses results', async () => {
  let identities = 0;
  const run = async (file, args) => Buffer.from(file.endsWith('lxc-info') ? '42' :
    args.includes('input') ? 'DispatchEnabled: true\nDispatchFrozen: false\nPendingEvent: <none>\nInboundQueue: <empty>' : '');
  await assert.rejects(inspect({ run, identity: async () => 'stable', androidIdentity: async () => String(++identities) }), /identity changed/);
});
test('a stable read-only inspection returns numeric timing and fixed scope only', async () => {
  const commands = [];
  const run = async (file, args) => {
    commands.push([file, ...args]);
    return Buffer.from(file.endsWith('lxc-info') ? '42' : args.includes('input') ?
      'DispatchEnabled: true\nDispatchFrozen: false\nPendingEvent: <none>\nInboundQueue: <empty>' : '');
  };
  const result = await inspect({ run, identity: async () => 'stable', androidIdentity: async () => 'stable' });
  assert.equal(result.guestPid, 42); assert.equal(result.inputInjected, false);
  assert.ok(result.inputInspectMs >= 0);
  assert.ok(commands.every(row => row[0] === '/usr/bin/lxc-info' || row.includes('input') || row.includes('/usr/bin/ss')));
});
