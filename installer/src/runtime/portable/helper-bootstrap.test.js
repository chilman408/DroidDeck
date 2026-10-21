// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), { EventEmitter } = require('node:events');
const { createRequire } = require('node:module');
const { loadWorkerConfiguration } = require('./worker-config');
function harness(filename, rejectAccount = false) {
  const localRequire = createRequire(require.resolve(filename)), calls = [], paths = [];
  const nativeServer = () => { const server = new EventEmitter(); server.listen = (path, callback) => { calls.push(['listen', path]); callback(); }; server.close = callback => callback?.(); return server; };
  const service = { reconcile: async () => calls.push(['reconcile']), sweep: async () => {}, shutdown: async () => {} };
  const modules = {
    '../portable/worker-config': { loadWorkerConfiguration: options => loadWorkerConfiguration({ ...options, uid: 0,
      validateAccount: async config => { calls.push(['account', config]); if (rejectAccount) throw new Error('account mismatch'); } }) },
    'node:fs/promises': { mkdir: async (path, options) => calls.push(['mkdir', path, options.mode]),
      lstat: async path => { if (path.endsWith('.sock')) throw Object.assign(new Error(), { code: 'ENOENT' });
        return { isDirectory: () => true, uid: 0, mode: path === '/run/droiddeck' ? 0o40750 : 0o40700 }; },
      chown: async (...args) => calls.push(['chown', ...args]), chmod: async (...args) => calls.push(['chmod', ...args]) },
    'node:net': { createServer: nativeServer },
    './host-adapter': { createHostAdapter: options => { calls.push(['adapter', options]); return { guest() {}, calibrate() {} }; } },
    './touch-adapter': { createTouchAdapter: options => { calls.push(['touch', options]); return {}; } },
    './location-adapter': { createLocationAdapter: () => ({}) },
    './transaction': { ...localRequire('../waydroid-display/transaction'), createTransactions: () => service },
    './store': { createStore: path => paths.push(path) },
    './gps-lifecycle': { createGpsLifecycle: () => service },
    './gps-store': { createGpsStore: path => paths.push(path) },
  };
  const module = { exports: {} };
  vm.runInNewContext(fs.readFileSync(require.resolve(filename), 'utf8'), { module,
    require: name => modules[name] || localRequire(name), Buffer, console: { log() {} }, AbortController,
    process: { platform: 'linux', getuid: () => 0, env: { TESLA_WAYDROID_SUPERVISED: '1' }, once() {}, umask() {} },
    setInterval: () => 1, clearInterval() {} });
  return { main: module.exports.main, calls, paths };
}
test('portable display/GPS helpers use configured root-private state and group-shared sockets', async () => {
  for (const [filename, name] of [['../waydroid-display/helper', 'display'], ['../waydroid-display/gps-helper', 'gps']]) {
    const fixture = harness(filename), configuration = { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40' };
    const pending = fixture.main({ configuration }); configuration.gid = 0; await pending;
    assert.equal(fixture.calls[0][0], 'account'); assert.equal(Object.isFrozen(fixture.calls[0][1]), true);
    assert.ok(fixture.calls.some(row => row[0] === 'mkdir' && row[1] === '/var/lib/droiddeck' && row[2] === 0o700));
    assert.ok(fixture.calls.some(row => row[0] === 'mkdir' && row[1] === '/run/droiddeck' && row[2] === 0o750));
    assert.ok(fixture.calls.some(row => row[0] === 'listen' && row[1] === '/run/droiddeck/' + name + '.sock'));
    assert.ok(fixture.calls.filter(row => row[0] === 'chown').every(row => row[2] === 0 && row[3] === 1201));
    assert.ok(fixture.calls.some(row => row[0] === 'chmod' && row[2] === 0o660));
    assert.equal(fixture.calls.find(row => row[0] === 'adapter')[1].hostLibs, '/opt/droiddeck/libgbinder');
    assert.ok(fixture.paths.includes('/var/lib/droiddeck/' + name + '.json'));
    if (name === 'gps') assert.ok(fixture.paths.includes('/run/droiddeck/gps/lease.json'));
  }
});
test('helper account rejection occurs before locks, directories, state or sockets', async () => {
  for (const filename of ['../waydroid-display/helper', '../waydroid-display/gps-helper']) {
    const fixture = harness(filename, true);
    await assert.rejects(fixture.main({ configuration: { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40' } }), /account mismatch/);
    assert.equal(fixture.calls.length, 1); assert.deepEqual(fixture.paths, []);
  }
});
