// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
function fixture() {
  const log = [], children = [], failures = [];
  let state = { version: 1, revision: 0, bitRate: 4000000, maxFps: 30 };
  const controller = require('./video-capture-controller').createVideoCaptureController({
    readProfile: () => ({ ...state }),
    prepare: async () => { log.push('prepare-owned-video'); },
    launch: next => {
      const child = new EventEmitter(); child.exitCode = null; child.signalCode = null;
      child.kill = signal => { log.push(signal); child.signalCode = signal; queueMicrotask(() => child.emit('exit', null, signal)); };
      log.push(['launch', next]); children.push(child); return child;
    },
    onFailure: error => failures.push(error.message),
  });
  return { controller, log, children, failures, change: change => { state = { ...state, ...change }; } };
}
test('profile change retires only its own video, coalesces changes and leaves audio alone', async () => {
  const f = fixture(); await f.controller.reconcile();
  await f.controller.reconcile(); assert.equal(f.children.length, 1);
  f.change({ revision: 1, bitRate: 2000000 });
  await f.controller.reconcile();
  assert.equal(f.children.length, 2);
  assert.deepEqual(f.log.map(item => Array.isArray(item) ? item[0] : item),
    ['prepare-owned-video', 'launch', 'prepare-owned-video', 'SIGTERM', 'launch']);
  assert.equal(f.log.at(-1)[1].bitRate, 2000000);
  assert.deepEqual(f.failures, []);
  await f.controller.stop();
  await f.controller.reconcile(); assert.equal(f.children.length, 2);
});
test('unexpected capture exit fails closed instead of leaving the service falsely healthy', async () => {
  const f = fixture(); await f.controller.reconcile();
  f.children[0].emit('exit', 1, null);
  assert.match(f.failures[0], /exit/i);
  await f.controller.stop();
});
test('shutdown while cleanup is pending must not launch a new guest capture', async () => {
  let release, launched = 0, prepares = 0;
  const controller = require('./video-capture-controller').createVideoCaptureController({
    readProfile: () => ({ version: 1, revision: 0, bitRate: 4000000, maxFps: 30 }),
    prepare: () => ++prepares === 1 ? new Promise(resolve => { release = resolve; }) : Promise.resolve(),
    launch: () => { launched++; }, onFailure() {},
  });
  const pending = controller.reconcile(); await new Promise(resolve => setImmediate(resolve));
  const stopped = controller.stop(); release(); await pending; await stopped;
  assert.equal(launched, 0);
});
test('lease verification failure still terminates its host child and never launches another', async () => {
  let fails = false, stopped = 0, launches = 0;
  const controller = require('./video-capture-controller').createVideoCaptureController({
    readProfile: () => ({ version: 1, revision: fails ? 1 : 0, bitRate: 4000000, maxFps: 30 }),
    prepare: async () => { if (fails) throw new Error('Lease changed'); },
    launch() { launches++; const child = new EventEmitter(); child.exitCode = null; child.signalCode = null;
      child.kill = () => { stopped++; queueMicrotask(() => child.emit('exit', 0, null)); }; return child; }, onFailure() {},
  });
  await controller.reconcile(); fails = true;
  await assert.rejects(controller.reconcile(), /Lease changed/);
  assert.equal(stopped, 1); assert.equal(launches, 1);
  await assert.rejects(controller.stop(), /Lease changed/);
});
test('changes arriving during owned cleanup launch only the newest target', async () => {
  let release, state = { version: 1, revision: 0, bitRate: 4000000, maxFps: 30 }, applied;
  const controller = require('./video-capture-controller').createVideoCaptureController({
    readProfile: () => state, prepare: () => new Promise(resolve => { release = resolve; }),
    launch(profile) { applied = profile; const child = new EventEmitter(); child.exitCode = 0; child.signalCode = null; return child; }, onFailure() {},
  });
  const pending = controller.reconcile(); await new Promise(resolve => setImmediate(resolve));
  state = { ...state, revision: 2, bitRate: 12000000, maxFps: 60 }; release(); await pending;
  assert.deepEqual(applied, state);
  const stopped = controller.stop(); await new Promise(resolve => setImmediate(resolve)); release(); await stopped;
});
