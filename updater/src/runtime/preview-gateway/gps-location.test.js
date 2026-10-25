// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const { normalizeLocation, createLocationForwarder } = require("./gps-location");
const NOW = 1800000000000;
const sample = overrides => ({ latitude: "0", longitude: "-180", vertical_accuracy: "5",
  timestamp: String(NOW), speed: "null", bearing: "null", ...overrides });

test("location protocol preserves zero and unavailable motion without inventing heading", () => {
  assert.deepEqual(normalizeLocation(sample(), NOW), {
    latitude: 0, longitude: -180, accuracy: 5, timestamp: NOW, speed: null, bearing: null,
  });
  assert.equal(normalizeLocation(sample({ speed: "0", bearing: "0" }), NOW).speed, 0);
  assert.equal(normalizeLocation(sample({ speed: 12.5, bearing: 359.9 }), NOW).bearing, 359.9);
});

test("location protocol rejects malformed, stale, future and out-of-range values", () => {
  for (const update of [{ latitude: "90.1" }, { longitude: "-180.1" }, { vertical_accuracy: "0" },
    { vertical_accuracy: "100001" }, { timestamp: NOW - 30001 }, { timestamp: NOW + 5001 },
    { timestamp: NOW + 0.1 }, { speed: -1 }, { speed: 151 }, { bearing: 360 },
    { bearing: -0.1 }, { latitude: " " }, { latitude: "0;id" }, { longitude: "Infinity" },
    { vertical_accuracy: "NaN" }, { unknown: 1 }, { latitude: null }, { speed: "" }]) {
    assert.throws(() => normalizeLocation(sample(update), NOW), /Invalid location/);
  }
  for (const value of [null, [], "{}", {}, Buffer.alloc(10)]) {
    assert.throws(() => normalizeLocation(value, NOW), /Invalid location/);
  }
  assert.equal(normalizeLocation(sample({ timestamp: NOW - 30000 }), NOW).timestamp, NOW - 30000);
});

test("forwarding coalesces a slow backend to the newest fix with one writer", async () => {
  let finish, active = 0, maximum = 0;
  const delivered = [];
  const forwarder = createLocationForwarder({ now: () => NOW, minimumIntervalMs: 0,
    deliver: async fix => {
      active++; maximum = Math.max(maximum, active); delivered.push(fix.latitude);
      if (delivered.length === 1) await new Promise(resolve => { finish = resolve; });
      active--;
    }, authorized: () => true });
  forwarder.submit(sample({ latitude: "1" }));
  await new Promise(setImmediate);
  for (let latitude = 2; latitude <= 20; latitude++) forwarder.submit(sample({ latitude }));
  finish(); await forwarder.idle();
  assert.deepEqual(delivered, [1, 20]); assert.equal(maximum, 1);
  await forwarder.close();
});

test("revocation aborts the writer, clears pending positions and rejects later submits", async () => {
  let signal, finish;
  const delivered = [];
  const forwarder = createLocationForwarder({ now: () => NOW, minimumIntervalMs: 0,
    authorized: () => true, deliver: async (fix, options) => {
      signal = options.signal; delivered.push(fix.latitude);
      await new Promise(resolve => { finish = resolve; });
    } });
  forwarder.submit(sample({ latitude: 1 })); await new Promise(setImmediate);
  forwarder.submit(sample({ latitude: 2 }));
  const closed = forwarder.close(); assert.equal(signal.aborted, true);
  assert.throws(() => forwarder.submit(sample()), /Location feed closed/);
  finish(); await closed; assert.deepEqual(delivered, [1]);
});

test("queued location is revalidated at delivery time and authority is never cached", async () => {
  let current = NOW, allowed = true, finish;
  const delivered = [], errors = [];
  const forwarder = createLocationForwarder({ now: () => current, minimumIntervalMs: 0,
    authorized: () => allowed, onError: code => errors.push(code), deliver: async fix => {
      delivered.push(fix.latitude);
      if (delivered.length === 1) await new Promise(resolve => { finish = resolve; });
    } });
  forwarder.submit(sample({ latitude: 1 })); await new Promise(setImmediate);
  forwarder.submit(sample({ latitude: 2 })); current += 30001;
  finish(); await forwarder.idle();
  assert.deepEqual(delivered, [1]); assert.deepEqual(errors, ["invalid-location"]);
  allowed = false;
  assert.throws(() => forwarder.submit(sample({ timestamp: current })), /Location authority unavailable/);
  await forwarder.close();
});

test("backend failures report only fixed error codes, not coordinates or backend text", async () => {
  const errors = [];
  const forwarder = createLocationForwarder({ now: () => NOW, minimumIntervalMs: 0,
    authorized: () => true, onError: code => errors.push(code),
    deliver: async () => { throw new Error("sensitive location/backend output"); } });
  forwarder.submit(sample()); await forwarder.idle();
  assert.deepEqual(errors, ["delivery-failed"]); await forwarder.close();
});

test("lost authority drops a queued fix even when its coordinates remain fresh", async () => {
  let allowed = true, finish;
  const delivered = [], errors = [];
  const forwarder = createLocationForwarder({ now: () => NOW, minimumIntervalMs: 0,
    authorized: () => allowed, onError: code => errors.push(code), deliver: async fix => {
      delivered.push(fix.latitude);
      await new Promise(resolve => { finish = resolve; });
    } });
  forwarder.submit(sample({ latitude: 1 })); await new Promise(setImmediate);
  forwarder.submit(sample({ latitude: 2 })); allowed = false;
  finish(); await forwarder.idle();
  assert.deepEqual(delivered, [1]); assert.deepEqual(errors, ["authority-unavailable"]);
  await forwarder.close();
});

test("closing during throttle delay discards the pending fix without waiting for its timer", async () => {
  const delivered = [];
  const forwarder = createLocationForwarder({ minimumIntervalMs: 60000, authorized: () => true,
    deliver: async fix => delivered.push(fix.latitude) });
  forwarder.submit(sample({ timestamp: Date.now() })); await forwarder.idle();
  forwarder.submit(sample({ timestamp: Date.now(), latitude: 2 })); await new Promise(setImmediate);
  await forwarder.close(); assert.deepEqual(delivered, [0]);
});

test("rate limit allows only one newest fix per interval", async () => {
  const times = [];
  const forwarder = createLocationForwarder({ minimumIntervalMs: 40, authorized: () => true,
    deliver: async () => times.push(performance.now()) });
  const current = () => sample({ timestamp: Date.now() });
  forwarder.submit(current()); await new Promise(setImmediate);
  forwarder.submit(current()); forwarder.submit(current()); await forwarder.idle();
  assert.equal(times.length, 2); assert.ok(times[1] - times[0] >= 35);
  await forwarder.close();
});
