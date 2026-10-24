// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { configurationDigest } = require('./configuration-generation');
const { startWorker } = require('./worker-entry');
const { runtimeCommand } = require('../waydroid-display/runtime-commands');
const config = { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40' };
const state = { pid: 1234, width: 980, height: 720, density: 180, containerAddress: '192.168.240.112',
  nativeLease: 'a'.repeat(32), boot: ['BOOTCLASSPATH=/system/framework/framework.jar', 'DEX2OATBOOTCLASSPATH=/apex/com.android.art/javalib/core-oj.jar'] };

test('configuration generation is canonical normalized metadata, not JSON order or credentials', () => {
  const digest = configurationDigest(config);
  assert.match(digest, /^[a-f0-9]{64}$/);
  assert.equal(configurationDigest({ lanAddress: config.lanAddress, gid: 1201, uid: 1200, user: 'deck', version: 1,
    home: '/home/deck', port: 8443, hostname: 'device.teslaandroid.com' }), digest);
  for (const change of [{ uid: 1202 }, { gid: 1203 }, { user: 'viewer' }, { home: '/home/stream' },
    { lanAddress: '10.20.30.41' }, { port: 9443 }, { hostname: 'deck.example' }]) {
    assert.notEqual(configurationDigest({ ...config, ...change }), digest);
  }
  assert.throws(() => configurationDigest({ ...config, password: 'secret' }));
});

test('generated host worker commands carry one parent generation into the closed entry', async () => {
  for (const [service, worker, uid] of [['display-helper', 'display', 0], ['gps', 'gps', 0],
    ['video', 'compositor-video', 0], ['audio', 'audio', 0], ['video-relay', 'video-relay', 1200],
    ['audio-relay', 'audio-relay', 1200], ['gateway', 'gateway', 1200]]) {
    const { args } = runtimeCommand(service, state, 'compositor', config);
    assert.deepEqual(args.slice(-3), ['/usr/bin/node', '/opt/droiddeck/runtime/portable/worker-entry.js', worker]);
    const generations = args.filter(value => value.startsWith('--setenv=DROIDDECK_CONFIGURATION_SHA256='));
    assert.deepEqual(generations, ['--setenv=DROIDDECK_CONFIGURATION_SHA256=' + configurationDigest(config)]);
    let launched = false;
    await startWorker(args.at(-1), { uid, expectedDigest: generations[0].split('=')[2], read: () => ({ ...config }),
      validateAccount: async () => {}, launch: async (selected, options) => {
        assert.equal(selected, worker); assert.equal(configurationDigest(options.configuration), configurationDigest(config));
        assert.deepEqual(options.args, []); launched = true;
      } });
    assert.equal(launched, true);
  }
  assert.equal(runtimeCommand('video', state, undefined, config).args.at(-1), 'video');
});

test('changed, missing or malformed child generation rejects before account lookup and launch', async () => {
  const reject = async () => assert.fail('No lookup or worker launch allowed');
  const digest = configurationDigest(config);
  for (const expectedDigest of ['', 'A'.repeat(64), 'a'.repeat(63), 'a'.repeat(65), null, 1, 'b'.repeat(64)]) {
    await assert.rejects(startWorker('video', { uid: 0, expectedDigest, read: () => config, validateAccount: reject, launch: reject }), /generation/);
  }
  await assert.rejects(startWorker('gateway', { uid: 1200, expectedDigest: digest,
    read: () => ({ ...config, port: 9443 }), validateAccount: reject, launch: reject }), /generation/);
  await assert.rejects(startWorker('video', { uid: 0, expectedDigest: digest,
    read: () => undefined, validateAccount: reject, launch: reject }), /generation/);
});
