// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), { EventEmitter } = require('node:events');
const { createRequire } = require('node:module');
const { loadWorkerConfiguration } = require('../portable/worker-config');
function harness(rejectAccount = false, socketGroup = 1201) {
  const filename = './helper', localRequire = createRequire(require.resolve(filename)), calls = [];
  const server = new EventEmitter(); server.listen = (path, callback) => { calls.push(['listen', path]); callback(); };
  const modules = {
    '../portable/worker-config': { loadWorkerConfiguration: options => loadWorkerConfiguration({ ...options, uid: 0,
      validateAccount: async config => { calls.push(['account', config]); if (rejectAccount) throw new Error('account mismatch'); } }) },
    'node:fs/promises': { mkdir: async (path, options) => calls.push(['mkdir', path, options.mode]),
      lstat: async path => {
        calls.push(['stat', path]);
        if (path === '/etc/droiddeck/proxy-origin') throw Object.assign(new Error(), { code: 'ENOENT' });
        return { uid: 0, gid: socketGroup, mode: path === '/run/droiddeck' ? 0o40750 : 0o100644, isFile: () => path.endsWith('.js'), isDirectory: () => !path.endsWith('.js') };
      }, chown: async (...args) => calls.push(['chown', ...args]), chmod: async (...args) => calls.push(['chmod', ...args]) },
    'node:net': { createServer: () => server },
    './proxy-store': { ...localRequire('./proxy-store'), createProxyStore: (path, options) => { calls.push(['store', path, options]); return {}; } },
    './socket-startup': { startProtectedConnectionServer: async ({ server, filename, gid }) => {
      calls.push(['protected-startup', filename, gid]);
      await new Promise(resolve => server.listen(filename, resolve));
      calls.push(['chown', filename, 0, gid], ['chmod', filename, 0o660]);
    } },
  };
  const module = { exports: {} };
  vm.runInNewContext(fs.readFileSync(require.resolve(filename), 'utf8'), { module,
    require: name => modules[name] || localRequire(name), Buffer, console, URL,
    process: { platform: 'linux', getuid: () => 0, argv: ['node', 'worker-entry.js', 'connection'], env: {}, once() {} } });
  return { main: options => module.exports.main({ args: [], ...options }), calls };
}
test('portable connection helper uses configured metadata roots, group and initial hostname', async () => {
  const fixture = harness(), configuration = { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40', hostname: 'deck.example.com' };
  const pending = fixture.main({ configuration }); configuration.hostname = 'changed.example.com'; await pending;
  assert.equal(fixture.calls[0][0], 'account');
  assert.ok(fixture.calls.some(row => row[0] === 'stat' && row[1] === '/opt/droiddeck/runtime/browser-connection/helper.js'));
  assert.ok(fixture.calls.some(row => row[0] === 'stat' && row[1] === '/etc/droiddeck/proxy-origin'));
  const store = fixture.calls.find(row => row[0] === 'store');
  assert.equal(store[1], '/var/lib/droiddeck/browser-connection/profile.json'); assert.equal(store[2].initialHostname, 'deck.example.com'); assert.equal(store[2].uid, 0);
  assert.ok(fixture.calls.some(row => row[0] === 'listen' && row[1] === '/run/droiddeck/connection.sock'));
  assert.ok(fixture.calls.some(row => row[0] === 'protected-startup' && row[1] === '/run/droiddeck/connection.sock' && row[2] === 1201));
  assert.ok(fixture.calls.some(row => row[0] === 'chown' && row[2] === 0 && row[3] === 1201));
  assert.ok(fixture.calls.some(row => row[0] === 'chmod' && row[2] === 0o660));
  assert.equal(fixture.calls.some(row => /key\.pem|cert\.pem/.test(row[1])), false);
});
test('portable connection helper rejects wrong account and socket group before binding', async () => {
  const configuration = { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40' };
  const rejected = harness(true); await assert.rejects(rejected.main({ configuration }), /account mismatch/); assert.equal(rejected.calls.length, 1);
  const wrongGroup = harness(false, 1000); await assert.rejects(wrongGroup.main({ configuration }), /socket directory/);
  assert.equal(wrongGroup.calls.some(row => row[0] === 'listen'), false);
});
