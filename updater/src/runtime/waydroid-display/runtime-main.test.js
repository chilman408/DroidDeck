// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test"), assert = require("node:assert/strict");
const { runApplication } = require("./runtime-main");
test("exclusive ownership is acquired before preflight or workers and released only after the loop settles", async () => {
  const calls = [], signal = new AbortController().signal;
  const state = { pid: 1234 };
  await runApplication({ signal, acquireLock: async () => { calls.push("lock"); return { close: cb => { calls.push("unlock"); cb(); } }; },
    ownership: { owner: "a".repeat(32), assertVacant: async () => { calls.push("vacant"); } }, assertLanVacant: async () => { calls.push("lan vacant"); },
    native: { recover: async () => { calls.push("recover native"); }, assertVacant: async () => { calls.push("native vacant"); },
      begin: async () => { calls.push("native lease"); }, finish: async () => { calls.push("native finish"); } },
    discovery: { inspect: async () => { calls.push("discovery"); return state; } },
    preflight: async value => { assert.equal(value, state); calls.push("protected artifacts"); }, pauseTouch: async () => {},
    loop: async ({ ready }) => { calls.push("loop"); await ready({ signal }); calls.push("settled"); } });
  assert.deepEqual(calls, ["lock", "vacant", "recover native", "loop", "vacant", "discovery", "lan vacant", "protected artifacts", "native vacant", "native lease", "settled", "native finish", "unlock"]);
});
test("manual preview rejection releases the lock without invoking runtime work", async () => {
  let released = false;
  await assert.rejects(runApplication({ signal: new AbortController().signal,
    acquireLock: async () => ({ close: cb => { released = true; cb(); } }),
    ownership: { assertVacant: async () => { throw new Error("manual preview"); } }, loop: async () => assert.fail(),
  }), /manual preview/); assert.equal(released, true);
});
test("an already cancelled application cannot acquire or mutate runtime", async () => {
  await assert.rejects(runApplication({ signal: AbortSignal.abort(new Error("cancelled")), acquireLock: async () => assert.fail() }), /cancelled/);
});
test('invalid portable configuration fails before acquiring the runtime lock', async () => {
  let acquired = false;
  await assert.rejects(runApplication({ configuration: { user: 'root', uid: 1200, gid: 1201, lanAddress: '10.20.30.40' },
    acquireLock: async () => { acquired = true; throw new Error('Lock must not run'); } }), /Invalid runtime identity/);
  assert.equal(acquired, false);
});
test('portable assembly shares one immutable snapshot across defaults, preflight and desktop/touch checks', async () => {
  const fs = require('node:fs'), vm = require('node:vm');
  const localRequire = require('node:module').createRequire(require.resolve('./runtime-main'));
  const factories = [], executions = [], preflights = [], hostLibraries = [], accounts = [], state = { pid: 1234, identity: '42:99:123' };
  let rejectAccount = false;
  const native = { recover: async () => {}, assertVacant: async () => {}, begin: async () => {}, finish: async () => {} };
  const modules = {
    '../portable/runtime-account': { validateRuntimeAccount: async config => { accounts.push(config); if (rejectAccount) throw new Error('account mismatch'); } },
    './runtime-supervisor': { createRuntimeSupervisor: options => options },
    './runtime-discovery': { createRuntimeDiscovery: (options = {}) => { factories.push(options); return { inspect: async () => state }; }, readContainerIdentity: async () => state.identity },
    './runtime-readiness': { createRuntimeReadiness: (options = {}) => { factories.push(options); return async () => {}; } },
    './runtime-ownership': { createRuntimeOwnership: (options = {}) => { factories.push(options); return { owner: 'a'.repeat(32), assertVacant: async () => {} }; } },
    './runtime-native': { createNativeRuntime: (options = {}) => { factories.push(options); return native; } },
    './runtime-preflight': { preflightRuntime: async (_state, options) => preflights.push(options) },
    './host-adapter': { execute: async (file, args) => { executions.push({ file, args }); return Buffer.from('not-found'); },
      createHostAdapter: options => { hostLibraries.push(options.hostLibs); return { guest() {}, calibrate() {} }; } },
    './touch-adapter': { createTouchAdapter: options => { hostLibraries.push(options.hostLibs); return { pause: async () => {} }; } },
  };
  const module = { exports: {} }, shim = name => modules[name] || localRequire(name);
  vm.runInNewContext(fs.readFileSync(require.resolve('./runtime-main'), 'utf8'), { module, require: shim, process, Buffer, console });
  const configuration = { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40' }, signal = new AbortController().signal;
  const pending = module.exports.runApplication({ configuration, signal,
    acquireLock: async () => ({ close: callback => callback() }),
    loop: async ({ ready, supervisor }) => { await ready({ signal }); await supervisor.pauseTouch(); } });
  configuration.user = 'root'; configuration.uid = 0; await pending;
  assert.equal(factories.length, 4);
  assert.equal(accounts.length, 1); assert.equal(accounts[0], factories[0].configuration);
  assert.ok(factories.every(options => options.configuration?.user === 'deck' && Object.isFrozen(options.configuration)));
  assert.ok(factories.every(options => options.configuration === factories[0].configuration));
  assert.equal(preflights[0]?.configuration, factories[0].configuration);
  assert.deepEqual(hostLibraries, ['/opt/droiddeck/libgbinder', '/opt/droiddeck/libgbinder']);
  assert.equal(executions[0].args[1], 'deck');
  assert.ok(executions[0].args.includes('XDG_RUNTIME_DIR=/run/user/1200'));
  assert.ok(executions[0].args.includes('DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1200/bus'));
  rejectAccount = true; let acquired = false;
  await assert.rejects(module.exports.runApplication({ configuration: { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40' },
    acquireLock: async () => { acquired = true; throw new Error('Unexpected lock'); } }), /account mismatch/);
  assert.equal(acquired, false); assert.equal(factories.length, 4);
});
