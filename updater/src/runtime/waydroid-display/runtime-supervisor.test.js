// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const { createRuntimeSupervisor, SERVICES } = require("./runtime-supervisor");
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
function fixture(overrides = {}) {
  const calls = [];
  const supervisor = createRuntimeSupervisor({ waitReady: async () => { calls.push("ready"); },
    startService: async name => { calls.push("start:" + name); }, stopService: async name => { calls.push("stop:" + name); },
    pauseTouch: async () => { calls.push("touch"); }, ...overrides });
  return { supervisor, calls };
}
test("fixed runtime services start in order and stop in reverse with verified touch cleanup last", async () => {
  assert.deepEqual([...SERVICES], ["api", "display-helper", "display", "gps", "video", "video-relay", "audio", "audio-relay", "gateway", "lan-relay"]);
  const f = fixture(); assert.equal((await f.supervisor.start()).phase, "ready");
  assert.deepEqual(f.calls, ["ready", ...SERVICES.map(name => "start:" + name)]);
  assert.equal((await f.supervisor.stop()).phase, "stopped");
  assert.deepEqual(f.calls.slice(11), [...SERVICES].reverse().map(name => "stop:" + name).concat("touch"));
  const count = f.calls.length; await f.supervisor.stop(); assert.equal(f.calls.length, count);
});
test("readiness never opens ingress and concurrent starts cannot create another coordinator", async () => {
  const gate = deferred(), entered = deferred();
  const f = fixture({ waitReady: async () => { entered.resolve(); await gate.promise; } });
  const first = f.supervisor.start(); await entered.promise;
  assert.equal(f.supervisor.snapshot().phase, "waiting");
  await assert.rejects(f.supervisor.start(), /Runtime busy/); assert.deepEqual(f.calls, []);
  gate.resolve(); await first; await f.supervisor.stop();
});
test("stop cancels readiness without starting services or touching Android input", async () => {
  const entered = deferred(); let signal;
  const f = fixture({ waitReady: options => { signal = options.signal; entered.resolve(); return new Promise((_resolve, reject) => {
    signal.addEventListener("abort", () => reject(signal.reason), { once: true }); }); } });
  const opening = assert.rejects(f.supervisor.start(), /Runtime stopped/); await entered.promise;
  await f.supervisor.stop(); await opening; assert.equal(signal.aborted, true); assert.deepEqual(f.calls, []);
});
test("stop holds the coordinator until a late started service settles and is removed", async () => {
  const gate = deferred(), entered = deferred(); let complete = false;
  const f = fixture({ startService: async name => { f.calls.push("start:" + name); if (name === "display-helper") { entered.resolve(); await gate.promise; } } });
  const opening = assert.rejects(f.supervisor.start(), /Runtime stopped/); await entered.promise;
  const stopping = f.supervisor.stop().then(() => { complete = true; });
  await Promise.resolve(); assert.equal(complete, false); await assert.rejects(f.supervisor.start(), /Runtime busy/);
  gate.resolve(); await stopping; await opening;
  assert.deepEqual(f.calls, ["ready", "start:api", "start:display-helper", "stop:display-helper", "stop:api", "touch"]);
});
test("failed startup cleans the attempted service before predecessors and can restart after verified cleanup", async () => {
  let fail = true;
  const f = fixture({ startService: async name => { f.calls.push("start:" + name); if (fail && name === "video") throw new Error("Start failed"); } });
  await assert.rejects(f.supervisor.start(), /Start failed/);
  assert.deepEqual(f.calls.slice(-6), ["stop:video", "stop:gps", "stop:display", "stop:display-helper", "stop:api", "touch"]);
  assert.equal(f.supervisor.snapshot().recoveryRequired, false); fail = false;
  await f.supervisor.start(); await f.supervisor.stop();
});
test("cleanup attempts every service and touch despite errors, and blocks a new startup", async () => {
  const f = fixture({ stopService: async name => { f.calls.push("stop:" + name); if (name === "gateway" || name === "gps") throw new Error("Stop failed:" + name); },
    pauseTouch: async () => { f.calls.push("touch"); throw new Error("Touch cleanup failed"); } });
  await f.supervisor.start(); let failure;
  await assert.rejects(f.supervisor.stop(), error => { failure = error; return error instanceof AggregateError && error.errors.length === 3; });
  assert.deepEqual(f.calls.slice(11), [...SERVICES].reverse().map(name => "stop:" + name).concat("touch"));
  assert.equal(f.supervisor.snapshot().recoveryRequired, true);
  await assert.rejects(f.supervisor.start(), /Runtime recovery required/);
  await assert.rejects(f.supervisor.stop(), error => error === failure);
});
test("caller cancellation preserves failed-start and cleanup errors separately", async () => {
  const controller = new AbortController();
  const f = fixture({ startService: async () => { controller.abort(new Error("Caller cancelled")); },
    stopService: async () => { throw new Error("Cleanup failed"); } });
  await assert.rejects(f.supervisor.start({ signal: controller.signal }), error => error instanceof AggregateError &&
    error.errors[0].message === "Caller cancelled" && error.errors[1] instanceof AggregateError);
  assert.equal(f.supervisor.snapshot().recoveryRequired, true);
});
test("already cancelled callers cannot mutate runtime", async () => {
  const f = fixture(), controller = new AbortController(); controller.abort(new Error("Cancelled"));
  await assert.rejects(f.supervisor.start({ signal: controller.signal }), /Cancelled/);
  assert.deepEqual(f.calls, []); assert.equal(f.supervisor.snapshot().phase, "stopped");
});
