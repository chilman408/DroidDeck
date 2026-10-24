// SPDX-License-Identifier: GPL-3.0-only
const assert = require("node:assert/strict");
const test = require("node:test");
const { createTransactions } = require("./transaction");
const baseline = { width: 852, height: 676, density: 180, resolutionPreset: 1, isResponsive: 1 };
function fixture({ remap = async () => {}, inspect = async () => baseline, verifyTouch = async () => {} } = {}) {
  const calls = [];
  const transactions = createTransactions({ adapter: {
    inspect, pause: async () => { calls.push("pause"); },
    remap: async (state, options) => { calls.push("remap"); assert.deepEqual(state, baseline); await remap(options); },
    verifyTouch: async () => { calls.push("verify"); await verifyTouch(); },
    resume: async () => { calls.push("resume"); },
    applySize: async () => { throw new Error("Must not resize"); },
    restore: async () => { throw new Error("Must not restore size"); },
    capture: async () => { throw new Error("Must not depend on protected app pixels"); },
  }, store: { load: async () => baseline, save: async () => { throw new Error("Must not overwrite preferences"); } } });
  return { transactions, calls };
}
test("retirement acknowledges old touch removal and verified recreation without geometry or preference writes", async () => {
  const f = fixture();
  assert.deepEqual(await f.transactions.retireTouch(), { ...baseline, recoveryRequired: false });
  assert.deepEqual(f.calls, ["pause", "remap", "verify", "resume"]);
});
test("touch retirement and resize share the same exclusive mutation lock", async () => {
  let finish;
  const f = fixture({ remap: () => new Promise(resolve => { finish = resolve; }) });
  const pending = f.transactions.retireTouch(); await new Promise(resolve => setImmediate(resolve));
  await assert.rejects(f.transactions.apply(baseline), error => error.status === 409);
  await assert.rejects(f.transactions.retireTouch(), error => error.status === 409);
  await assert.rejects(f.transactions.readState(), error => error.status === 409);
  finish(); await pending;
});
test("failed verified recreation closes new touch and leaves recovery required", async () => {
  const f = fixture({ verifyTouch: async () => { throw new Error("Wrong native identity"); } });
  await assert.rejects(f.transactions.retireTouch(), error => error.recoveryRequired);
  assert.deepEqual(f.calls, ["pause", "remap", "verify", "pause"]);
  assert.equal((await f.transactions.readState()).recoveryRequired, true);
  await assert.rejects(f.transactions.retireTouch(), /recovery/);
});
test("geometry drift during handoff never grants a mismapped touch device", async () => {
  let count = 0;
  const f = fixture({ inspect: async () => ({ ...baseline, width: ++count === 1 ? 852 : 960 }) });
  await assert.rejects(f.transactions.retireTouch(), /geometry changed/);
  assert.equal(f.calls.at(-1), "pause");
});
