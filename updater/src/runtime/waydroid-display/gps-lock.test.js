// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const { acquireGpsLock } = require("./gps-helper");
test("a GPS helper holds exclusive ownership before any reconciliation, released without stale files", {
  skip: process.platform !== "linux",
}, async () => {
  const name = "\0teslaandroid-gps-test-" + crypto.randomBytes(16).toString("hex");
  const lock = await acquireGpsLock(name);
  try { await assert.rejects(acquireGpsLock(name), /GPS helper already active/); }
  finally { await new Promise(resolve => lock.close(resolve)); }
  const next = await acquireGpsLock(name); await new Promise(resolve => next.close(resolve));
});
