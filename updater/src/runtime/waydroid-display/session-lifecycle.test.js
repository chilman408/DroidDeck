// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const { waitForSession } = require("./session-lifecycle");

test("waits for registered, running and booted session before opening UI", async () => {
  const events = [];
  let attempt = 0;
  await waitForSession({ registered: async () => attempt > 0,
    state: async () => attempt < 2 ? "STOPPED" : "RUNNING",
    booted: async () => attempt > 2, unfreeze: async () => events.push("unfreeze"),
    showUI: async () => events.push("UI"), sleep: async () => { attempt++; } });
  assert.equal(attempt, 3);
  assert.deepEqual(events, ["UI"]);
});

test("unfreezes before boot readiness instead of starting another session", async () => {
  const events = [];
  let frozen = true;
  await waitForSession({ registered: async () => true,
    state: async () => frozen ? "FROZEN" : "RUNNING",
    unfreeze: async () => { frozen = false; events.push("unfreeze"); },
    booted: async () => { events.push("boot"); return true; },
    showUI: async () => events.push("UI") });
  assert.deepEqual(events, ["unfreeze", "boot", "UI"]);
});

test("cancelled startup never opens UI or launches a competing session", async () => {
  const controller = new AbortController();
  let opened = false;
  await assert.rejects(waitForSession({ registered: async () => false,
    showUI: async () => { opened = true; }, sleep: async () => controller.abort(new Error("cancelled")) },
  controller.signal), /cancelled/);
  assert.equal(opened, false);
});
test("running LXC with command environment not ready is retried, not treated as a failed resize", async () => {
  let attempts = 0, opened = false;
  await waitForSession({ registered: async () => true, state: async () => "RUNNING",
    booted: async () => { if (attempts++ === 0) throw Object.assign(new Error("not mounted"), { code: 127 }); return true; },
    bootUnavailable: (error) => error.code === 127, sleep: async () => {},
    showUI: async () => { opened = true; } });
  assert.equal(attempts, 2);
  assert.equal(opened, true);
});
test("readiness polling does not hide unexpected guest errors", async () => {
  await assert.rejects(waitForSession({ registered: async () => true, state: async () => "RUNNING",
    booted: async () => { throw Object.assign(new Error("permission denied"), { code: 13 }); },
    bootUnavailable: (error) => error.code === 127 }), /permission denied/);
});
