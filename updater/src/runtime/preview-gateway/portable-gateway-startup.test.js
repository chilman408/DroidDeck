// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), { createRequire } = require('node:module');
const { loadWorkerConfiguration } = require('../portable/worker-config');
function harness({ rejectAccount = false, environment = {} } = {}) {
  const localRequire = createRequire(require.resolve('./server')), calls = [], stores = [], reads = [];
  const control = { initialize: async () => {}, close() {} };
  const modules = {
    '../portable/worker-config': { loadWorkerConfiguration: options => loadWorkerConfiguration({ ...options, uid: 1200,
      validateAccount: async config => { calls.push(['account', config]); if (rejectAccount) throw new Error('account mismatch'); } }) },
    'node:os': { homedir: () => '/home/wrong' },
    'node:fs': { readFileSync: file => { reads.push(file); return Buffer.from('generated fixture'); },
      existsSync: file => { reads.push(file); return file === '/run/droiddeck/connection.sock'; },
      lstatSync: file => { reads.push(file); if (file === '/etc/droiddeck/proxy-origin') return undefined;
        return { isSocket: () => true, uid: 0, gid: 1201, mode: 0o140660 }; } },
    './rear-display-settings': { createRearDisplaySettings: file => { stores.push(file); return {}; } },
    './audio-settings': { createAudioSettings: file => { stores.push(file); return {}; } },
    './video-settings': { ...localRequire('./video-settings'), createVideoSettings: file => { stores.push(file); return {}; } },
    './display-helper-client': { ...localRequire('./display-helper-client'), createHelperClient: file => { stores.push(file); return { retireTouch() {} }; } },
    './display-control': { createDisplayControl: () => control },
    './gps-helper-client': { createGpsHelperClient: file => { stores.push(file); return {}; } },
    './gps-control': { createGpsControl: () => control },
    '../browser-connection/helper': { createConnectionClient: file => { stores.push(file); return control; } },
  };
  const module = { exports: {} };
  const env = { GATEWAY_ORIGIN: 'https://10.20.30.40:9443', GATEWAY_BIND: '192.168.240.112',
    GATEWAY_ANDROID_LOOPBACK: '1', GATEWAY_H264_STREAM: '1', GATEWAY_AUDIO_STREAM: '1', GATEWAY_REAR_DISPLAY: '1',
    GATEWAY_VIDEO_SETTINGS: '1', GATEWAY_AUDIO_PCM_GAIN: '1', ...environment };
  vm.runInNewContext(fs.readFileSync(require.resolve('./server'), 'utf8'), { module,
    require: name => modules[name] || localRequire(name), Buffer, URL, console,
    process: { argv: ['node', 'worker-entry.js', 'gateway'], env, once() {}, exit() {} } });
  const create = options => { calls.push(['gateway', options]); return { listen: (...args) => calls.push(['listen', ...args]), retireDisplayAuthority() {} }; };
  return { main: options => module.exports.main({ args: [], ...options, create }), calls, reads, stores };
}
const configuration = { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40', port: 9443, home: '/home/display' };
test('configured gateway validates identity before TLS reads and shares private preference/helper paths', async () => {
  const fixture = harness(), input = { ...configuration }, pending = fixture.main({ configuration: input });
  input.home = '/home/changed'; await pending;
  assert.equal(fixture.calls[0][0], 'account'); assert.equal(Object.isFrozen(fixture.calls[0][1]), true);
  assert.ok(fixture.reads.includes('/home/display/.config/droiddeck/key.pem'));
  assert.ok(fixture.reads.includes('/home/display/.config/droiddeck/cert.pem'));
  for (const file of ['rear-display.json', 'browser-audio.json', 'browser-video.json']) assert.ok(fixture.stores.includes('/home/display/.config/droiddeck/' + file));
  for (const file of ['display.sock', 'gps.sock', 'connection.sock']) assert.ok(fixture.stores.includes('/run/droiddeck/' + file));
  assert.ok(fixture.reads.includes('/etc/droiddeck/proxy-origin'));
  const gateway = fixture.calls.find(row => row[0] === 'gateway')[1];
  assert.equal(gateway.frontendDir, '/opt/droiddeck/frontend'); assert.equal(gateway.publicOrigin, 'https://10.20.30.40:9443');
  assert.equal(gateway.accessFile, '/home/display/.config/droiddeck/browser-access.json');
  assert.equal(gateway.targets.h264, 'ws://127.0.0.1:9092'); assert.equal(gateway.targets.audio, 'ws://127.0.0.1:9093');
  assert.ok(fixture.calls.some(row => row[0] === 'listen' && row[1] === 9443 && row[2] === '192.168.240.112'));
});
test('account failure and mismatched configured path overrides cannot read TLS or create servers', async () => {
  const rejected = harness({ rejectAccount: true }); await assert.rejects(rejected.main({ configuration }), /account mismatch/);
  assert.deepEqual(rejected.reads, []); assert.deepEqual(rejected.stores, []); assert.equal(rejected.calls.length, 1);
  for (const environment of [{ GATEWAY_TLS_KEY_FILE: '/tmp/key' }, { GATEWAY_DISPLAY_SOCKET: '/tmp/socket' },
    { GATEWAY_ORIGIN: 'https://10.20.30.41:9443' }, { GATEWAY_ACCESS_FILE: '/tmp/access' }, { GATEWAY_H264_STREAM: '0' }]) {
    const fixture = harness({ environment }); await assert.rejects(fixture.main({ configuration }));
    assert.deepEqual(fixture.reads, []); assert.equal(fixture.calls.length, 1);
  }
});
