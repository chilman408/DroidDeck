// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const { createDisplayControl } = require("./display-control");
const baseline = { width: 1280, height: 768, density: 180, resolutionPreset: 0, isResponsive: 1, recoveryRequired: false };
function fakeClock() {
  let now = 0, id = 0; const timers = new Map();
  return { now: () => now, setTimeout(fn, ms) { timers.set(++id, { fn, at: now + ms }); return id; },
    clearTimeout(key) { timers.delete(key); }, tick(ms) { now += ms; for (const [key, timer] of [...timers])
      if (timer.at <= now) { timers.delete(key); timer.fn(); } } };
}
async function fixture(t, resize) {
  let actual = { ...baseline }, calls = 0;
  const clock = fakeClock();
  const control = createDisplayControl({ clock, helper: { inspect: async () => actual,
    resize: async (desired, options) => { calls++; actual = { ...await (resize ? resize(desired, options) : desired), recoveryRequired: false }; return actual; } } });
  t.after(() => control.close()); await control.initialize();
  return { control, clock, calls: () => calls };
}
function change(control, session, size = { width: 960, height: 640 }) {
  return control.resize(session.token, { ...baseline, ...size, generation: control.snapshot(session.token).generation }, session.csrfToken);
}
test("display-first ownership is per-tab; touch-first and other viewers cannot claim or write", async t => {
  const { control } = await fixture(t);
  const a = control.createSession(), b = control.createSession();
  assert.equal(a.token.length, 64); assert.notEqual(a.token, a.csrfToken);
  assert.throws(() => control.connect(b.token, "touchscreen"), /owner/i);
  control.connect(a.token, "display"); control.connect(b.token, "display");
  assert.equal(control.snapshot(a.token).canControl, true);
  assert.equal(control.snapshot(b.token).canControl, false);
  await assert.rejects(change(control, b), /owner/i);
  await assert.rejects(control.resize(a.token, { ...baseline, generation: 0 }, "wrong"), /CSRF/);
  assert.throws(() => control.snapshot("unknown"), /session/i);
});
test("both-channel disconnect grants five-second grace; eligible viewer takes over", async t => {
  const { control, clock } = await fixture(t);
  const a = control.createSession(), b = control.createSession();
  control.connect(a.token, "display"); control.connect(a.token, "touchscreen"); control.connect(b.token, "display");
  control.disconnect(a.token, "display"); clock.tick(6000);
  assert.equal(control.snapshot(a.token).canControl, true);
  control.disconnect(a.token, "touchscreen"); clock.tick(4999);
  control.connect(a.token, "display"); clock.tick(1);
  assert.equal(control.snapshot(a.token).canControl, true);
  control.disconnect(a.token, "display"); clock.tick(5000);
  assert.equal(control.snapshot(b.token).canControl, true);
  assert.throws(() => control.snapshot(a.token), /session/i);
});
test("actual state publishes only after commit, duplicate is no-op and stale/unsupported states fail", async t => {
  const { control, calls } = await fixture(t); const a = control.createSession(); control.connect(a.token, "display");
  assert.equal(control.snapshot(a.token).isResponsive, 1);
  assert.equal(control.snapshot(a.token).landscapeNavigationMinHeight, 676);
  const next = await change(control, a); assert.equal(next.width, 960); assert.equal(next.generation, 1);
  await change(control, a); assert.equal(calls(), 1);
  for (const invalid of [{ generation: 0 }, { renderer: 1 }, { width: 961 }, { density: 200 },
    { landscapeNavigationMinHeight: 0 }, { quality: 91 }, { shell: "reboot" }]) {
    await assert.rejects(control.resize(a.token, { ...next, ...invalid }, a.csrfToken));
  }
  assert.equal(calls(), 1);
});

test("owner receives a full reconnect grace period after a slow resize finishes", async t => {
  let finish;
  const { control, clock } = await fixture(t, desired => new Promise(resolve => { finish = () => resolve(desired); }));
  const owner = control.createSession(); control.connect(owner.token, "display");
  control.on("pause", () => control.disconnect(owner.token, "display"));
  const pending = change(control, owner);
  await new Promise(resolve => setImmediate(resolve));
  clock.tick(5000); clock.tick(4000); finish(); await pending;
  const viewer = control.createSession(); control.connect(viewer.token, "display");
  clock.tick(1000);
  assert.equal(control.snapshot(owner.token).canControl, true);
  clock.tick(3999); control.connect(owner.token, "display"); clock.tick(1);
  assert.equal(control.snapshot(owner.token).canControl, true);
  assert.equal(control.snapshot(viewer.token).canControl, false);
});
test("one pending resize coalesces to the newest request and transactions are serialized", async t => {
  let finish; const { control, calls } = await fixture(t, desired => new Promise(resolve => { finish = () => resolve(desired); }));
  const a = control.createSession(); control.connect(a.token, "display");
  const first = change(control, a); await new Promise(resolve => setImmediate(resolve));
  assert.equal(control.snapshot(a.token).width, 1280); assert.equal(control.snapshot(a.token).transactionStatus, "resizing");
  const displaced = change(control, a, { width: 800, height: 600 });
  const rejection = assert.rejects(displaced, /superseded/i);
  const newest = change(control, a, { width: 640, height: 960 }); await rejection;
  assert.equal(calls(), 1); finish(); await first; await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls(), 2); finish(); assert.equal((await newest).height, 960);
});
test("login revocation cancels active work and removes all authority without publishing old results", async t => {
  let finish, observedSignal;
  const { control } = await fixture(t, (desired, { signal }) => { observedSignal = signal; return new Promise(resolve => { finish = () => resolve(desired); }); });
  const a = control.createSession(); control.connect(a.token, "display");
  const pending = change(control, a); const rejected = assert.rejects(pending, /revoked/i);
  await new Promise(resolve => setImmediate(resolve)); control.revokeAll(); assert.equal(observedSignal.aborted, true);
  assert.throws(() => control.snapshot(a.token), /session/i);
  const b = control.createSession(); assert.throws(() => control.connect(b.token, "display"), /in progress/);
  assert.equal(control.snapshot(b.token).canControl, false);
  finish(); await rejected; control.connect(b.token, "display"); assert.equal(control.snapshot(b.token).canControl, true);
});
test("failed helper refreshes actual state and failed recovery blocks further control", async t => {
  const { control } = await fixture(t, async () => { throw Object.assign(new Error("failed"), { recoveryRequired: true }); });
  const a = control.createSession(); control.connect(a.token, "display"); await assert.rejects(change(control, a), /failed/);
  const state = control.snapshot(a.token); assert.equal(state.width, 1280); assert.equal(state.recoveryRequired, true);
  await assert.rejects(change(control, a), /recovery/i);
});
test("unconnected sessions expire and session count is bounded", async t => {
  const { control, clock } = await fixture(t); const a = control.createSession();
  for (let i = 1; i < 64; i++) control.createSession(); assert.throws(() => control.createSession(), /limit/i);
  clock.tick(5000); assert.throws(() => control.snapshot(a.token), /session/i); assert.ok(control.createSession());
});
