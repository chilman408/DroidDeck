// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const { restoreGps } = require("./live-gps-helper-probe");
test("diagnostic GPS preference restoration is attempted even if feed cleanup fails", async () => {
  const saved = [];
  const client = { end: async () => { throw new Error("Removal failed"); }, setEnabled: async value => { saved.push(value); } };
  await assert.rejects(restoreGps(client, "a".repeat(32), false), /GPS probe restoration failed/);
  assert.deepEqual(saved, [false]);
});
test("diagnostic restoration preserves both cleanup and preference failures", async () => {
  const removal = new Error("Removal failed"), preference = new Error("Preference failed");
  await assert.rejects(restoreGps({ end: async () => { throw removal; }, setEnabled: async () => { throw preference; } },
    "a".repeat(32), true), error => error instanceof AggregateError && error.errors[0] === removal && error.errors[1] === preference);
});
