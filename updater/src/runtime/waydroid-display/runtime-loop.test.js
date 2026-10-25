// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const { runRuntimeLoop } = require("./runtime-loop");
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
test("checks readiness before startup, monitors readiness, cleans and rediscovers after failure", async () => {
  const controller = new AbortController(), calls = []; let starts = 0, ready = 0;
  const supervisor = { start: async () => { calls.push("start"); starts++; }, stop: async () => { calls.push("stop"); },
    snapshot: () => ({ phase: "stopped" }) };
  await runRuntimeLoop({ supervisor, signal: controller.signal,
    ready: async () => { calls.push("ready"); if (++ready === 1) throw new Error("not booted"); },
    healthy: async () => { calls.push("health"); if (starts === 1) return false; controller.abort(); return true; },
    sleep: async ms => { calls.push(ms); } });
  assert.deepEqual(calls, ["ready", 2000, "ready", "start", 1000, "health", "stop", 2000,
    "ready", "start", 1000, "health", "stop"]);
});
test("startup failure cleans before retry and never overlaps another start", async () => {
  const controller = new AbortController(), calls = []; let count = 0;
  await runRuntimeLoop({ signal: controller.signal, ready: async () => {}, healthy: async () => true,
    supervisor: { start: async () => { calls.push("start"); if (++count === 1) throw new Error("failed"); controller.abort(); },
      stop: async () => { calls.push("stop"); }, snapshot: () => ({ phase: "stopped" }) },
    sleep: async () => { calls.push("delay"); } });
  assert.deepEqual(calls, ["start", "stop", "delay", "start", "stop"]);
});
test("failed cleanup blocks automatic retry instead of hiding unsettled workers", async () => {
  let starts = 0;
  await assert.rejects(runRuntimeLoop({ signal: new AbortController().signal, ready: async () => {}, healthy: async () => false,
    supervisor: { start: async () => { starts++; }, stop: async () => { throw new Error("cleanup failed"); },
      snapshot: () => ({ phase: "blocked" }) }, sleep: async () => {} }), /cleanup failed/);
  assert.equal(starts, 1);
});
test("abort during health waits for native check settlement before stop", async () => {
  const controller = new AbortController(), entered = deferred(), native = deferred(), calls = [];
  const pending = runRuntimeLoop({ signal: controller.signal, ready: async () => {}, sleep: async () => {},
    healthy: async ({ signal }) => { assert.equal(signal, controller.signal); entered.resolve(); await native.promise; return true; },
    supervisor: { start: async () => {}, stop: async () => { calls.push("stop"); }, snapshot: () => ({ phase: "ready" }) } });
  await entered.promise; controller.abort(); await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(calls, []); native.resolve(); await pending; assert.deepEqual(calls, ["stop"]);
});
test("abort during startup settles it and closes all workers exactly once", async () => {
  const controller = new AbortController(), entered = deferred(), native = deferred(); let stopped = 0;
  const pending = runRuntimeLoop({ signal: controller.signal, ready: async () => {}, healthy: async () => true, sleep: async () => {},
    supervisor: { start: async ({ signal }) => { assert.equal(signal, controller.signal); entered.resolve(); await native.promise; },
      stop: async () => { stopped++; }, snapshot: () => ({ phase: "starting" }) } });
  await entered.promise; controller.abort(); assert.equal(stopped, 0); native.resolve(); await pending; assert.equal(stopped, 1);
});
test("an already cancelled loop never acquires startup ownership or touches workers", async () => {
  const controller = new AbortController(); controller.abort();
  await runRuntimeLoop({ signal: controller.signal, ready: async () => assert.fail(), healthy: async () => assert.fail(),
    supervisor: { start: async () => assert.fail(), stop: async () => assert.fail(), snapshot: () => ({ phase: "stopped" }) } });
});
