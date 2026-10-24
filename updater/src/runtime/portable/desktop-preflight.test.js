// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { assertDesktopEnvironment } = require('./desktop-preflight');
const { assertHeadlessSessionReady } = require('../waydroid-display/headless-session-preflight');
const config = { user: 'deck', uid: 1200, gid: 1201, home: '/home/stream', lanAddress: '10.20.30.40' };
const environment = { HOME: '/home/stream', XDG_RUNTIME_DIR: '/run/user/1200', DBUS_SESSION_BUS_ADDRESS: 'unix:path=/run/user/1200/bus', WAYLAND_DISPLAY: 'wayland-teslaandroid' };
function fixture() {
  const reads = [], commands = [], connections = [];
  return { reads, commands, connections, configuration: config, environment,
    io: { lstat: async name => { reads.push(name); return { uid: 1200, mode: 0o700,
      isDirectory: () => name === '/run/user/1200', isSocket: () => name !== '/run/user/1200' }; } },
    run: async (file, args, options) => { commands.push({ file, args, options }); return Buffer.from(file.endsWith('systemctl') ?
      'LoadState=not-found\nActiveState=inactive\nUnitFileState=\n' : JSON.stringify({ type: 'b', data: [args.includes('--system')] })); },
    connect: async name => { connections.push(name); } };
}

test('configured desktop/headless preflight uses only its account bus, private socket and exact environment', async () => {
  const f = fixture(); await assertDesktopEnvironment(f); assert.deepEqual(f.reads, ['/run/user/1200', '/run/user/1200/bus']);
  f.reads.length = 0; await assertHeadlessSessionReady(f);
  assert.deepEqual(f.reads, ['/run/user/1200', '/run/user/1200/bus', '/run/user/1200/wayland-teslaandroid']);
  assert.deepEqual(f.connections, ['/run/user/1200/wayland-teslaandroid']); assert.equal(f.commands.length, 3);
  assert.ok(f.commands.every(call => call.options.env === environment));
});

test('configured session waits for container bus with bounded retries and refuses malformed or absent readiness', async () => {
  const f = fixture(), original = f.run; let attempts = 0, sleeps = 0;
  f.run = async (file, args, options) => args.includes('--system') ? Buffer.from(JSON.stringify({ type: 'b', data: [++attempts >= 3] })) : original(file, args, options);
  f.sleep = async ms => { assert.equal(ms, 250); sleeps++; };
  await assertHeadlessSessionReady(f); assert.equal(attempts, 3); assert.equal(sleeps, 2);
  for (const invalid of ['{}', '{"type":"s","data":[true]}', '{"type":"b","data":[true,false]}', '{"type":"b","data":[1]}']) {
    const bad = fixture(), before = bad.run;
    bad.run = async (file, args, options) => args.includes('--system') ? Buffer.from(invalid) : before(file, args, options);
    await assert.rejects(assertHeadlessSessionReady(bad), /container bus/);
  }
  const absent = fixture(), before = absent.run; let absentAttempts = 0, absentSleeps = 0;
  absent.run = async (file, args, options) => args.includes('--system') ? (absentAttempts++, Buffer.from('{"type":"b","data":[false]}')) : before(file, args, options);
  absent.sleep = async () => { absentSleeps++; };
  await assert.rejects(assertHeadlessSessionReady(absent), /Container bus/);
  assert.equal(absentAttempts, 20); assert.equal(absentSleeps, 19);
  const cancelled = fixture(), controller = new AbortController(); cancelled.signal = controller.signal;
  const beforeCancel = cancelled.run;
  cancelled.run = async (file, args, options) => args.includes('--system') ? Buffer.from('{"type":"b","data":[false]}') : beforeCancel(file, args, options);
  cancelled.sleep = async () => controller.abort(new Error('cancelled'));
  await assert.rejects(assertHeadlessSessionReady(cancelled), /cancelled/);
});

test('wrong environment, unsafe account directories/sockets, invalid config and cancellation fail closed', async () => {
  for (const change of [{ HOME: '/home/other' }, { XDG_RUNTIME_DIR: '/run/user/1000' },
    { DBUS_SESSION_BUS_ADDRESS: 'tcp:host=localhost' }, { WAYLAND_DISPLAY: 'wayland-0' }]) {
    const f = fixture(); f.environment = { ...environment, ...change };
    await assert.rejects(assertDesktopEnvironment(f)); assert.equal(f.reads.length, 0);
  }
  for (const patch of [{ uid: 1000 }, { mode: 0o777 }, { isDirectory: () => false }]) {
    const f = fixture(), before = f.io.lstat;
    f.io.lstat = async name => ({ ...await before(name), ...(name === '/run/user/1200' ? patch : {}) });
    await assert.rejects(assertDesktopEnvironment(f));
  }
  const socket = fixture(), before = socket.io.lstat;
  socket.io.lstat = async name => ({ ...await before(name), ...(name.endsWith('/bus') ? { uid: 0 } : {}) });
  await assert.rejects(assertDesktopEnvironment(socket));
  const invalid = fixture(); invalid.configuration = { ...config, user: 'root' };
  await assert.rejects(assertDesktopEnvironment(invalid)); assert.equal(invalid.reads.length, 0);
  const aborted = fixture(); aborted.signal = AbortSignal.abort(new Error('cancelled'));
  await assert.rejects(assertHeadlessSessionReady(aborted), /cancelled/); assert.equal(aborted.reads.length, 0);
});
