// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), { EventEmitter } = require('node:events');
const { createRequire } = require('node:module');
const { loadWorkerConfiguration } = require('../portable/worker-config');
function harness(filename, rejectAccount = false) {
  const localRequire = createRequire(require.resolve(filename)), calls = [];
  const server = new EventEmitter(); server.listen = (...args) => calls.push(['listen', ...args]);
  const modules = {
    '../portable/worker-config': { loadWorkerConfiguration: options => loadWorkerConfiguration({ ...options, uid: 1200,
      validateAccount: async config => { calls.push(['account', config]); if (rejectAccount) throw new Error('account mismatch'); } }) },
    'node:http': { createServer: () => { calls.push(['server']); return server; } },
    ws: { WebSocketServer: class extends EventEmitter { constructor() { super(); this.clients = new Set(); } }, WebSocket: { OPEN: 1 } },
    './audio-gain': { ...localRequire('./audio-gain'), createVolumeProvider: options => { calls.push(['volume', options]); return () => 100; } },
  };
  const module = { exports: {} };
  vm.runInNewContext(fs.readFileSync(require.resolve(filename), 'utf8'), { module,
    require: name => modules[name] || localRequire(name), Buffer, console,
    process: { argv: ['node', 'worker-entry.js', 'worker'], env: { TESLA_WAYDROID_AUDIO_PCM_GAIN: '1' }, once() {}, exit() {} } });
  return { main: options => module.exports.main({ args: [], ...options }), calls, server };
}
test('portable relays check the account before binding and audio uses its immutable preference path', async () => {
  for (const filename of ['./h264-relay', './audio-relay']) {
    const fixture = harness(filename), configuration = { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40', home: '/home/display' };
    const pending = fixture.main({ configuration }); configuration.home = '/home/changed'; await pending;
    assert.equal(fixture.calls[0][0], 'account'); assert.equal(Object.isFrozen(fixture.calls[0][1]), true);
    assert.ok(fixture.calls.some(row => row[0] === 'server'));
    assert.deepEqual(fixture.calls.find(row => row[0] === 'listen'), ['listen', filename.includes('h264') ? 9092 : 9093, '127.0.0.1']);
    if (filename.includes('audio')) assert.equal(fixture.calls.find(row => row[0] === 'volume')[1].filename, '/home/display/.config/droiddeck/browser-audio.json');
  }
});
test('portable relay account failure cannot create or bind a network server', async () => {
  for (const filename of ['./h264-relay', './audio-relay']) {
    const fixture = harness(filename, true);
    await assert.rejects(fixture.main({ configuration: { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40' } }), /account mismatch/);
    assert.equal(fixture.calls.length, 1);
  }
});
