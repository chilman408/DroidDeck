// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { sourceNode } = require('./compositor-video-launcher');

test('portable compositor discovery checks configured UID and desktop bus before accepting a node', async () => {
  const configuration = { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40' };
  let uid = 1200;
  const io = { lstat: async () => ({ uid }), readFile: async () => Buffer.from('/usr/bin/weston\0--backend=pipewire\0--socket=wayland-teslaandroid\0'), readlink: async () => '/usr/bin/weston' };
  const run = async (file, args) => {
    if (file === '/usr/bin/systemctl') return Buffer.from('40000');
    assert.equal(file, '/usr/sbin/runuser'); assert.equal(args[1], 'deck');
    assert.ok(args.includes('XDG_RUNTIME_DIR=/run/user/1200'));
    assert.ok(args.includes('DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1200/bus'));
    return Buffer.from(JSON.stringify([{ id: 50, type: 'PipeWire:Interface:Node', info: { props: {
      'application.process.id': 40000, 'node.name': 'weston.pipewire', 'media.class': 'Stream/Output/Video', 'object.serial': 99,
    } } }]));
  };
  assert.deepEqual(await sourceNode(run, io, configuration), { id: 50, serial: 99 });
  uid = 1000; await assert.rejects(sourceNode(run, io, configuration), /private PipeWire/);
  await assert.rejects(sourceNode(async () => assert.fail(), io, { ...configuration, uid: 0 }));
});

test('portable encoder launch changes only host account, paths and explicit expected UID', () => {
  const { compositorLaunchArgs } = require('./compositor-video-launcher');
  const configuration = { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40' }, owner = 'a'.repeat(32);
  const plan = { serial: 99 };
  const args = compositorLaunchArgs('40000', owner, plan, configuration);
  assert.ok(args.includes('deck')); assert.ok(args.includes('/opt/droiddeck/runtime/preview-gateway/compositor-h264.py'));
  assert.deepEqual(args.slice(-3), [JSON.stringify(plan), '--uid', '1200']);
  const legacy = compositorLaunchArgs('40000', owner, plan);
  assert.ok(legacy.includes('hilman')); assert.ok(legacy.includes('/opt/teslaandroid-waydroid/runtime/preview-gateway/compositor-h264.py'));
  assert.equal(legacy.includes('--uid'), false);
  for (const pid of ['0', '1', '40000;sh', 40000]) assert.throws(() => compositorLaunchArgs(pid, owner, plan, configuration));
  assert.throws(() => compositorLaunchArgs('40000', 'forged', plan, configuration));
});
const { capturePlan } = require('../waydroid-display/compositor-source');
const viewport = 'Viewport INTERNAL: displayId=0, orientation=0, logicalFrame=[0, 0, 980, 720], physicalFrame=[150, 0, 1130, 720], deviceSize=[1280, 720], isActive=[1]';
test('host capture crops the same viewport used for accepted edge touch mapping', () => {
  assert.deepEqual(capturePlan(viewport, { width: 980, height: 720 }, { serial: 99 }), {
    serial: 99, width: 980, height: 720, sourceWidth: 1280, sourceHeight: 720, left: 150, top: 0, right: 150, bottom: 0,
  });
  for (const text of [viewport.replace('orientation=0', 'orientation=1'), viewport.replace('isActive=[1]', 'isActive=[0]'),
    viewport.replace('1130', '1300'), viewport.replace('980, 720]', '960, 720]')]) {
    assert.throws(() => capturePlan(text, { width: 980, height: 720 }, { serial: 99 }));
  }
  assert.throws(() => capturePlan(viewport, { width: 980, height: 720 }, { serial: '99' }));
});
test('production node discovery requires the owned private compositor and fences PID change', async () => {
  const snapshot = [{ id: 50, type: 'PipeWire:Interface:Node', info: { props: {
    'application.process.id': 40000, 'node.name': 'weston.pipewire', 'media.class': 'Stream/Output/Video', 'object.serial': 99,
  } } }];
  let nextPid = 40000, argv = '/usr/bin/weston\0--backend=pipewire\0--socket=wayland-teslaandroid\0', uid = 1000, calls = 0;
  const io = { lstat: async () => ({ uid }), readFile: async () => Buffer.from(argv), readlink: async () => '/usr/bin/weston' };
  const run = async (file, args) => {
    if (file === '/usr/bin/systemctl') return Buffer.from(String(calls++ ? nextPid : 40000));
    assert.equal(file, '/usr/sbin/runuser'); assert.ok(args.includes('/usr/bin/pw-dump'));
    return Buffer.from(JSON.stringify(snapshot));
  };
  assert.deepEqual(await sourceNode(run, io), { id: 50, serial: 99 });
  nextPid = 40001; calls = 0;
  await assert.rejects(sourceNode(run, io), /identity changed/);
  uid = 0; calls = 0;
  await assert.rejects(sourceNode(run, io), /private PipeWire/);
  uid = 1000; argv = argv.replace('wayland-teslaandroid', 'wayland-desktop'); calls = 0;
  await assert.rejects(sourceNode(run, io), /private PipeWire/);
});
test('Linux producer framing tests run without loading GStreamer or capturing content', { skip: process.platform !== 'linux' }, async () => {
  const { execute } = require('../waydroid-display/host-adapter');
  await execute('/usr/bin/python3', [require('node:path').join(__dirname, 'compositor-h264-test.py')], { env: process.env });
});
