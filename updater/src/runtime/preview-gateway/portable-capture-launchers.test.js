// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const { createRequire } = require('node:module');
const { EventEmitter } = require('node:events');
const { normalizeRuntimeConfig } = require('../portable/runtime-config');
const { loadWorkerConfiguration } = require('../portable/worker-config');
const owner = 'a'.repeat(32);
function harness(filename, rejectAccount = false) {
  const localRequire = createRequire(require.resolve(filename));
  const calls = [], profiles = [], children = [], captures = [];
  const environment = { TESLA_WAYDROID_VIDEO_SETTINGS: '1', TESLA_WAYDROID_RUNTIME_OWNER: owner, TESLA_WAYDROID_RUNTIME_ROLE: 'video' };
  const run = async (file, args) => {
    calls.push(['execute', file, args]);
    if (file === '/usr/bin/lxc-info' || file === '/usr/bin/systemctl') return Buffer.from('40000');
    assert.equal(file, '/usr/sbin/runuser'); assert.equal(args[1], 'deck');
    return Buffer.from(JSON.stringify([{ id: 50, type: 'PipeWire:Interface:Node', info: { props: {
      'application.process.id': 40000, 'node.name': 'weston.pipewire', 'media.class': 'Stream/Output/Video', 'object.serial': 99,
    } } }]));
  };
  const modules = {
    '../portable/worker-config': { loadWorkerConfiguration: options => loadWorkerConfiguration({ ...options, uid: 0,
      validateAccount: async configuration => { calls.push(['account', configuration]); if (rejectAccount) throw new Error('account mismatch'); } }) },
    './capture-lease': { ...localRequire('./capture-lease'), prepareCapture: async (role, options) => { calls.push(['lease', role]); captures.push(options.configuration); return owner; } },
    '../waydroid-display/host-adapter': { ...localRequire('../waydroid-display/host-adapter'), execute: run,
      createHostAdapter: options => { calls.push(['adapter', options]); return { guest: async args => {
        if (args[0] === '/system/bin/wm') return Buffer.from('Override size: 980x720');
        if (args[0] === '/system/bin/dumpsys') return Buffer.from('Viewport INTERNAL: displayId=0, orientation=0, logicalFrame=[0, 0, 980, 720], physicalFrame=[150, 0, 1130, 720], deviceSize=[1280, 720], isActive=[1]');
        return Buffer.from('export BOOTCLASSPATH /system/framework/framework.jar\nexport DEX2OATBOOTCLASSPATH /system/framework/framework.jar');
      } }; } },
    'node:fs/promises': { lstat: async file => { calls.push(['artifact', file]); return { isFile: () => true, uid: file === '/proc/40000' ? 1200 : 0, mode: 0o100644, size: 20, nlink: 1 }; },
      readFile: async file => Buffer.from(file.endsWith('cmdline') ? '/usr/bin/weston\0--backend=pipewire\0--socket=wayland-teslaandroid\0' : 'fixture'), readlink: async () => '/usr/bin/weston' },
    'node:crypto': { createHash: () => ({ update: () => ({ digest: () => localRequire('./video-launcher').SERVER_SHA256 }) }) },
    'node:child_process': { spawn: (file, args) => {
      children.push({ file, args }); const child = new EventEmitter();
      child.stdout = new EventEmitter(); child.stdout.pipe = () => {}; child.kill = () => {}; child.exitCode = null; child.signalCode = null;
      return child;
    } },
    './video-settings': { ...localRequire('./video-settings'), readVideoSettings: (file, options) => { profiles.push({ file, ...options }); return { version: 1, revision: 22, bitRate: 3000000, maxFps: 30 }; } },
    './video-capture-controller': { createVideoCaptureController: options => ({ reconcile: async () => {
      const profile = await options.readProfile(); await options.prepare(); options.launch(profile);
    }, stop: async () => {} }) },
  };
  const module = { exports: {} };
  vm.runInNewContext(fs.readFileSync(require.resolve(filename), 'utf8'), { module,
    require: name => modules[name] || localRequire(name), Buffer, console: { log() {} },
    process: { platform: 'linux', getuid: () => 0, argv: ['node', 'worker-entry.js', 'worker'], env: environment, once() {}, stdout: {} },
    setInterval: () => 1, clearInterval() {}, setTimeout: () => ({ unref() {} }), clearTimeout() {} });
  return { main: options => module.exports.main({ args: [], ...options }), calls, captures, profiles, children };
}
test('portable audio/video launchers validate account first and retain immutable paths and guest protocol', async () => {
  for (const filename of ['./audio-launcher', './video-launcher', './compositor-video-launcher']) {
    const fixture = harness(filename), input = { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40', home: '/home/display' };
    const expected = normalizeRuntimeConfig(input), pending = fixture.main({ configuration: input });
    input.user = 'root'; input.uid = 0; await pending;
    assert.equal(fixture.calls[0][0], 'account');
    assert.deepEqual(fixture.calls[0][1], expected);
    assert.ok(fixture.captures.every(value => value === fixture.calls[0][1] && Object.isFrozen(value)));
    assert.equal(fixture.calls.find(value => value[0] === 'adapter')[1].hostLibs, '/opt/droiddeck/libgbinder');
    if (filename.includes('compositor')) {
      assert.equal(fixture.children[0].file, '/usr/bin/nsenter');
      assert.deepEqual(Array.from(fixture.children[0].args.slice(-2)), ['--uid', '1200']);
      assert.ok(fixture.children[0].args.includes('/opt/droiddeck/runtime/preview-gateway/compositor-h264.py'));
      const plan = JSON.parse(fixture.children[0].args.at(-3)); assert.equal(plan.bitRate, 3000000); assert.equal(plan.left, 150);
      assert.deepEqual(fixture.profiles, [{ file: '/home/display/.config/droiddeck/browser-video.json', uid: 1200 }]);
      continue;
    }
    assert.equal(fixture.children[0].file, '/usr/bin/lxc-attach');
    assert.ok(fixture.children[0].args.includes('CLASSPATH=/data/local/tmp/tesla-waydroid-test-20261003/scrcpy-server-v4.0.jar'));
    if (filename.includes('video')) {
      assert.deepEqual(fixture.profiles, [{ file: '/home/display/.config/droiddeck/browser-video.json', uid: 1200 }]);
      assert.ok(fixture.children[0].args.includes('video_bit_rate=3000000'));
      assert.ok(fixture.children[0].args.includes('audio=false'));
    } else assert.ok(fixture.children[0].args.includes('audio_codec=raw'));
  }
});
test('portable launcher account failure cannot adopt captures, access artifacts or launch children', async () => {
  for (const filename of ['./audio-launcher', './video-launcher', './compositor-video-launcher']) {
    const fixture = harness(filename, true);
    await assert.rejects(fixture.main({ configuration: { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40' } }), /account mismatch/);
    assert.equal(fixture.calls.length, 1); assert.equal(fixture.captures.length, 0); assert.equal(fixture.children.length, 0);
  }
});
