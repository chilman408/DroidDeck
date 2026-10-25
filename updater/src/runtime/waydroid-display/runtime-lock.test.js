// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const { acquireRuntimeLock } = require("./runtime-lock");
test("runtime supervisor lock excludes duplicate ownership and releases without stale files", {
  skip: process.platform !== "linux",
}, async () => {
  const name = "\0teslaandroid-waydroid-runtime-test-" + crypto.randomBytes(16).toString("hex");
  const first = await acquireRuntimeLock(name);
  try { await assert.rejects(acquireRuntimeLock(name), /already active/); }
  finally { await new Promise(resolve => first.close(resolve)); }
  const second = await acquireRuntimeLock(name); await new Promise(resolve => second.close(resolve));
});
test("runtime lock cannot bind caller-selected filesystem paths", async () => {
  await assert.rejects(acquireRuntimeLock("/tmp/runtime.lock"), /Invalid runtime lock|requires Linux/);
});
