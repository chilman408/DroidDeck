// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const { PNG } = require("pngjs");
const { createTransactions } = require("./transaction");

function fixture(fail = "") {
  let size = { width: 1280, height: 768, density: 180, sizeOverride: null };
  let saved = null, paused = false, recreations = 0;
  const adapter = {
    inspect: async () => ({ ...size }),
    capture: async () => {
      const png = new PNG({ width: fail === "mismatch" && size.width === 960 ? 1280 : size.width, height: size.height });
      png.data.fill(fail === "black" && size.width === 960 ? 0 : 255);
      return PNG.sync.write(png);
    },
    pause: async () => { paused = true; },
    applySize: async (desired, { signal }) => {
      if (fail === "timeout") await new Promise((_resolve, reject) => signal.addEventListener("abort", () => reject(signal.reason), { once: true }));
      size = { ...desired };
    },
    remap: async () => { recreations++; if (fail === "remap" && size.width === 960) throw new Error("remap failed"); },
    verifyTouch: async () => true,
    resume: async () => { paused = false; },
    restore: async (snapshot) => { if (fail === "rollback") throw new Error("rollback failed"); size = { ...snapshot }; },
  };
  const store = {
    load: async () => saved,
    save: async (state) => { if ((fail === "save" || fail === "rollback") && state.width === 960) throw new Error("save failed"); saved = state; },
  };
  const transactions = createTransactions({ adapter, store, timeoutMs: fail === "timeout" ? 200 : 20000 });
  return { transactions, actual: () => ({ size, saved, paused, recreations }) };
}
const desired = { width: 960, height: 640, density: 180, resolutionPreset: 2, isResponsive: 1 };

test("missing preferences truthfully default to dynamic on at measured density", async () => {
  const { transactions } = fixture();
  assert.deepEqual(await transactions.readState(), { width: 1280, height: 768, density: 180,
    resolutionPreset: 0, isResponsive: 1, recoveryRequired: false });
});
test("verified state commits and duplicates do not recreate the touchscreen", async () => {
  const { transactions, actual } = fixture();
  const state = await transactions.apply(desired);
  assert.equal(state.width, 960);
  assert.equal(actual().saved.isResponsive, 1);
  assert.equal(actual().paused, false);
  await transactions.apply(desired);
  assert.equal(actual().recreations, 1);
});
for (const fail of ["mismatch", "black", "remap", "save", "timeout"]) {
  test(`${fail} restores original actual size and preferences before resuming control`, async () => {
    const { transactions, actual } = fixture(fail);
    await assert.rejects(transactions.apply(desired));
    assert.equal(actual().size.width, 1280);
    assert.equal(actual().size.height, 768);
    assert.equal(actual().size.sizeOverride, null);
    assert.equal(actual().saved.resolutionPreset, 0);
    assert.equal(actual().paused, false);
    assert.equal((await transactions.readState()).recoveryRequired, false);
  });
}
test("failed rollback leaves control disabled and rejects further writes", async () => {
  const { transactions, actual } = fixture("rollback");
  await assert.rejects(transactions.apply(desired), e => e.recoveryRequired === true && /save failed/.test(e.message));
  assert.equal(actual().paused, true);
  await assert.rejects(transactions.apply(desired), /recovery required/i);
});

for (const cleanupFails of [false, true]) {
  test(`failed rollback after remapping disables touch and preserves errors (cleanup failure: ${cleanupFails})`, async () => {
    let touchEnabled = false, pauses = 0;
    const adapter = {
      inspect: async () => ({ width: 1280, height: 768, density: 180 }),
      pause: async () => { pauses++; if (cleanupFails && pauses === 3) throw new Error("cleanup failed"); touchEnabled = false; },
      applySize: async () => {}, restore: async () => {},
      remap: async () => { touchEnabled = true; },
      verifyTouch: async size => { throw new Error(size.width === 960 ? "forward failed" : "rollback failed"); },
    };
    const transactions = createTransactions({ adapter, store: { load: async () => null, save: async () => {} } });
    await assert.rejects(transactions.apply(desired), error => {
      assert.equal(error.message, "forward failed");
      assert.equal(error.rollbackError.message, "rollback failed");
      assert.equal(error.recoveryRequired, true);
      assert.equal(error.cleanupError?.message, cleanupFails ? "cleanup failed" : undefined);
      return true;
    });
    assert.equal(pauses, 3);
    assert.equal(touchEnabled, cleanupFails);
    await assert.rejects(transactions.apply(desired), /recovery required/i);
  });
}
test("malformed desired state cannot reach guest mutation", async () => {
  const { transactions, actual } = fixture();
  for (const change of [{ renderer: 1 }, { density: 200 }, { resolutionPreset: 5 }, { isResponsive: 2 }, { width: 961 }]) {
    await assert.rejects(transactions.apply({ ...desired, ...change }));
  }
  assert.equal(actual().recreations, 0);
  assert.equal(actual().paused, false);
});
test("a concurrent writer is rejected rather than running a second guest mutation", async () => {
  const { transactions } = fixture("timeout");
  const first = transactions.apply(desired);
  await assert.rejects(transactions.apply(desired), e => e.status === 409);
  await assert.rejects(transactions.readState(), e => e.status === 409);
  await assert.rejects(first);
});

test("startup reconciliation verifies and recreates touch even when saved size already matches", async () => {
  const { transactions, actual } = fixture();
  await transactions.apply(desired);
  await transactions.reconcile();
  assert.equal(actual().recreations, 2);
});
