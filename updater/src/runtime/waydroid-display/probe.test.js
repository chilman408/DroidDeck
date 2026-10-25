// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const { PNG } = require("pngjs");
const { runProbe } = require("./probe");
const { execFileSync } = require("node:child_process");
const path = require("node:path");

function fake({ black = false, fail = false, mismatch = false, hang = false, restoreFail = false } = {}) {
  const initial = { width: 1280, height: 768, density: 200 };
  let state = { ...initial };
  return {
    get current() { return state; },
    async inspect() { return { ...state }; },
    async capture() {
      const png = new PNG({ width: state.width, height: state.height });
      png.data.fill(255);
      if (black && state.width !== initial.width) {
        for (let i = 0; i < png.data.length; i += 4) png.data.fill(0, i, i + 3);
      }
      return PNG.sync.write(png);
    },
    async applySize(desired, { signal }) {
      if (!mismatch) state = { ...desired };
      if (fail) throw new Error("apply failed");
      if (hang) await new Promise((_, reject) => signal.addEventListener("abort",
        () => reject(signal.reason), { once: true }));
    },
    async restore(snapshot) {
      if (restoreFail) throw new Error("restore failed");
      state = { ...snapshot };
    },
  };
}

test("proves both shapes and restores the measured baseline", async () => {
  const adapter = fake();
  const result = await runProbe(adapter, [{ width: 960, height: 640 }, { width: 640, height: 960 }]);
  assert.equal(result.verified, true);
  assert.deepEqual(result.frames.map(({ width, height }) => [width, height]), [[960, 640], [640, 960]]);
  assert.deepEqual(adapter.current, { width: 1280, height: 768, density: 200 });
  assert.deepEqual(result.restored, { width: 1280, height: 768, nonblack: true });
});
for (const [name, options] of [["black capture", { black: true }],
  ["capture-size mismatch", { mismatch: true }], ["apply error", { fail: true }],
  ["timeout", { hang: true }]]) {
  test(`restores after ${name} without claiming success`, async () => {
    const adapter = fake(options);
    const result = await runProbe(adapter, [{ width: 960, height: 640 }], { timeoutMs: 100 });
    assert.equal(result.verified, false);
    assert.ok(result.error);
    assert.equal(result.recoveryRequired, false);
    assert.deepEqual(adapter.current, { width: 1280, height: 768, density: 200 });
  });
}
test("reports failed restoration separately and never claims a working preview", async () => {
  const result = await runProbe(fake({ fail: true, restoreFail: true }), [{ width: 960, height: 640 }]);
  assert.equal(result.verified, false);
  assert.equal(result.recoveryRequired, true);
  assert.match(result.error, /apply failed/);
  assert.match(result.recoveryError, /restore failed/);
});
test("waits for cancelled forward work to settle before restoring", async () => {
  const adapter = fake();
  adapter.applySize = async (desired) => {
    await new Promise((resolve) => setTimeout(resolve, 85));
    await adapter.restore(desired);
  };
  const result = await runProbe(adapter, [{ width: 960, height: 640 }], { timeoutMs: 100 });
  assert.equal(result.verified, false);
  assert.equal(result.recoveryRequired, false);
  assert.deepEqual(adapter.current, { width: 1280, height: 768, density: 200 });
  await new Promise((resolve) => setTimeout(resolve, 100));
  assert.equal(adapter.current.width, 1280);
});
test("unsettled cancellation leaves recovery disabled rather than racing an active writer", async () => {
  const adapter = fake();
  adapter.applySize = () => new Promise(() => {});
  const result = await runProbe(adapter, [{ width: 960, height: 640 }], { timeoutMs: 100, settleMs: 25 });
  assert.equal(result.recoveryRequired, true);
  assert.equal(result.quiescenceRequired, true);
  assert.equal(result.restored, undefined);
});
test("settled launcher cancellation preserves an active background job warning", async () => {
  const adapter = fake();
  adapter.applySize = async (_, { signal }) => new Promise((resolve, reject) => {
    signal.addEventListener("abort", () => reject(Object.assign(new Error("restart still active"),
      { quiescenceRequired: true })), { once: true });
  });
  const result = await runProbe(adapter, [{ width: 960, height: 640 }], { timeoutMs: 100 });
  assert.equal(result.quiescenceRequired, true);
  assert.equal(result.restored, undefined);
});
test("standalone CLI cannot resize while preview control is live", () => {
  assert.throws(() => execFileSync(process.execPath, [path.join(__dirname, "probe.js")], { stdio: "pipe" }),
    (error) => error.status === 2 && /standalone.*disabled/i.test(error.stderr.toString()));
});
