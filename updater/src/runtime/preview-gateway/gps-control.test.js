// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { createGpsControl } = require("./gps-control");
const { createGpsLifecycle } = require("../waydroid-display/gps-lifecycle");
const { setTimeout: delay } = require("node:timers/promises");
class Socket extends EventEmitter {
  readyState = 1; sent = [];
  send(text) { this.sent.push(JSON.parse(text)); }
  terminate() { this.readyState = 3; this.emit("close"); }
}
test("revocation aborts a delayed preference write before it commits", async t => {
  const f = await fixture(t); let release, entered, signal;
  const blocked = new Promise(resolve => { release = resolve; }), started = new Promise(resolve => { entered = resolve; });
  const set = f.lifecycle.setEnabled;
  f.lifecycle.setEnabled = async (value, options) => { signal = options.signal; entered(); await blocked;
    options.signal.throwIfAborted(); return set(value, options); };
  const writing = assert.rejects(f.gps.update(false, { authorized: f.allowed }), /GPS/);
  await started; f.revoke(); await delay(30); const cancelled = signal.aborted;
  release(); await writing; assert.equal(cancelled, true); assert.equal(f.saved.enabled, true);
});
test("an inspect started before disable cannot overwrite the committed preference", async t => {
  const f = await fixture(t); let release, entered;
  const blocked = new Promise(resolve => { release = resolve; }), started = new Promise(resolve => { entered = resolve; });
  const inspect = f.lifecycle.inspect;
  f.lifecycle.inspect = async () => { const previous = await inspect(); entered(); await blocked; return previous; };
  const stale = f.gps.refresh(); await started; await f.gps.update(false, { authorized: f.allowed });
  release(); await stale; assert.equal(f.gps.snapshot().enabled, false); assert.equal(f.saved.enabled, false);
});
async function fixture(t) {
  let native = null, journal = null, saved = { version: 1, enabled: true }, writes = [], authorized = true, ends = 0;
  const lifecycle = createGpsLifecycle({ preferences: { load: async () => saved, save: async x => { saved = x; } },
    leases: { load: async () => journal, save: async x => { journal = x; } }, adapter: {
      inspect: async token => ({ present: native !== null, owned: native === token }),
      open: async token => { native = token; }, close: async () => { native = null; },
      deliver: async (_token, fix) => { writes.push(fix); },
    } });
  await lifecycle.reconcile();
  const end = lifecycle.end; lifecycle.end = (...args) => { ends++; return end(...args); };
  const gps = createGpsControl({ helper: lifecycle, minimumIntervalMs: 0, heartbeatMs: 10 });
  await gps.initialize(); t.after(async () => { await gps.close(); await lifecycle.shutdown(); });
  return { gps, lifecycle, writes, allowed: () => authorized, revoke: () => { authorized = false; },
    get native() { return native; }, get saved() { return saved; }, get ends() { return ends; } };
}
test("GPS feed forwards canonical fixes only after native ready and removes provider on close", async t => {
  const f = await fixture(t), socket = new Socket();
  await f.gps.attach(socket, f.allowed);
  assert.deepEqual(socket.sent, [{ version: 1, ready: true, mock: true }]);
  socket.emit("message", Buffer.from(JSON.stringify({ latitude: "0", longitude: "0", vertical_accuracy: "5",
    timestamp: String(Date.now()), speed: "null", bearing: "0" })), false);
  await delay(5); assert.equal(f.writes.length, 1); assert.equal(f.writes[0].speed, null); assert.equal(f.writes[0].bearing, 0);
  socket.terminate(); await f.gps.close(); assert.equal(f.native, null);
  assert.equal(f.ends, 1, "A socket close and terminate callback must share one cleanup");
});
test("GPS revocation discards pending fixes, closes feed and prevents later native writes", async t => {
  const f = await fixture(t), socket = new Socket(); await f.gps.attach(socket, f.allowed); f.revoke();
  socket.emit("message", Buffer.from(JSON.stringify({ latitude: 1, longitude: 2, vertical_accuracy: 5,
    timestamp: Date.now(), speed: 0, bearing: 0 })), false);
  await delay(20); await f.gps.close(); assert.equal(f.writes.length, 0); assert.equal(f.native, null);
  await assert.rejects(f.gps.update(false, { authorized: f.allowed }), /GPS authority/);
  assert.deepEqual(f.saved, { version: 1, enabled: true });
});
test("GPS off closes streams, persists enabled only and rejects viewers or competing feeds", async t => {
  const f = await fixture(t), socket = new Socket();
  await assert.rejects(f.gps.attach(new Socket(), () => false), /GPS authority/);
  await f.gps.attach(socket, f.allowed);
  await assert.rejects(f.gps.attach(new Socket(), f.allowed), /GPS feed busy/);
  await f.gps.update(false, { authorized: f.allowed });
  assert.equal(socket.readyState, 3); assert.equal(f.native, null); assert.deepEqual(f.saved, { version: 1, enabled: false });
  assert.equal(f.ends, 1, "Disabling a feed must remove it once even when terminate emits close synchronously");
  await assert.rejects(f.gps.attach(new Socket(), f.allowed), /GPS unavailable/);
  await f.gps.update(true, { authorized: f.allowed }); await f.gps.attach(new Socket(), f.allowed);
});
test("GPS binary or malformed messages close the feed without writing positions", async t => {
  for (const [data, binary] of [[Buffer.from("{}"), false], [Buffer.from("bad"), false], [Buffer.alloc(2049), false], [Buffer.from("{}"), true]]) {
    const f = await fixture(t), socket = new Socket(); await f.gps.attach(socket, f.allowed);
    socket.emit("message", data, binary); await f.gps.close(); assert.equal(f.writes.length, 0); assert.equal(f.native, null);
  }
});
test("disconnect during native startup holds the slot until creation and owned cleanup settle", async t => {
  const f = await fixture(t), socket = new Socket(); let release, entered;
  const gate = new Promise(resolve => { release = resolve; }), started = new Promise(resolve => { entered = resolve; });
  const begin = f.lifecycle.begin;
  f.lifecycle.begin = async (...args) => { const state = await begin(...args); entered(); await gate; return state; };
  const opening = f.gps.attach(socket, f.allowed); const rejected = assert.rejects(opening, /GPS unavailable/);
  await started; socket.terminate();
  await assert.rejects(f.gps.attach(new Socket(), f.allowed), /GPS feed busy/);
  assert.notEqual(f.native, null); release(); await rejected; await f.gps.close();
  assert.equal(f.native, null); assert.equal(f.ends, 1); assert.equal(socket.sent.length, 0);
});

test("authority retirement drains native delivery and removes the provider before a new feed", async t => {
  const f = await fixture(t), socket = new Socket();
  await f.gps.attach(socket, f.allowed);
  let finish, started;
  const entered = new Promise(resolve => { started = resolve; });
  const end = f.lifecycle.end;
  f.lifecycle.end = async (...args) => { started(); await new Promise(resolve => { finish = resolve; }); return end(...args); };
  const pending = f.gps.retireFeed(); await entered;
  await assert.rejects(f.gps.attach(new Socket(), f.allowed), /unavailable/);
  finish(); assert.equal((await pending).active, false);
  assert.equal(f.native, null); assert.equal(socket.readyState, 3);
  assert.deepEqual(f.saved, { version: 1, enabled: true });
  await f.gps.attach(new Socket(), f.allowed);
  // Return the original end implementation for fixture cleanup.
  f.lifecycle.end = end;
});

test("failed native provider removal is not accepted as authority retirement", async t => {
  const f = await fixture(t); await f.gps.attach(new Socket(), f.allowed);
  f.lifecycle.end = async () => { throw new Error("Native provider still present"); };
  await assert.rejects(f.gps.retireFeed(), /not acknowledged/);
  assert.notEqual(f.native, null);
});

test("retirement aborts and awaits an outstanding GPS preference write", async t => {
  const f = await fixture(t);
  let finish, started;
  const entered = new Promise(resolve => { started = resolve; });
  const set = f.lifecycle.setEnabled;
  f.lifecycle.setEnabled = async (enabled, options) => {
    started(); await new Promise(resolve => { finish = resolve; });
    options.signal.throwIfAborted(); return set(enabled, options);
  };
  const writing = assert.rejects(f.gps.update(false, { authorized: f.allowed }), /GPS/);
  await entered; const pending = f.gps.retireFeed();
  await assert.rejects(f.gps.attach(new Socket(), f.allowed), /unavailable/);
  finish(); await writing; await pending;
  assert.deepEqual(f.saved, { version: 1, enabled: true });
});
