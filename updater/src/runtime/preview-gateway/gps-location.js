// SPDX-License-Identifier: GPL-3.0-only
const { performance } = require("node:perf_hooks");
const { setTimeout: delay } = require("node:timers/promises");

function normalizeLocation(value, now = Date.now()) {
  const invalid = () => { throw new Error("Invalid location"); };
  if (!value || typeof value !== "object" || Array.isArray(value) ||
      Object.keys(value).some(key => !["latitude", "longitude", "vertical_accuracy", "timestamp", "speed", "bearing"].includes(key))) invalid();
  const number = input => {
    if (typeof input === "string" && (input.length > 32 || !/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(input))) invalid();
    if (!["string", "number"].includes(typeof input) || !Number.isFinite(Number(input))) invalid();
    return Number(input);
  };
  const optional = input => input == null || input === "null" || input === "not-available" ? null : number(input);
  const fix = { latitude: number(value.latitude), longitude: number(value.longitude),
    // Upstream calls browser horizontal accuracy "vertical_accuracy" on the wire.
    accuracy: number(value.vertical_accuracy), timestamp: number(value.timestamp),
    speed: optional(value.speed), bearing: optional(value.bearing) };
  if (Math.abs(fix.latitude) > 90 || Math.abs(fix.longitude) > 180 || fix.accuracy <= 0 || fix.accuracy > 100000 ||
      !Number.isSafeInteger(fix.timestamp) || fix.timestamp <= 0 || fix.timestamp < now - 30000 || fix.timestamp > now + 5000 ||
      (fix.speed !== null && (fix.speed < 0 || fix.speed > 150)) ||
      (fix.bearing !== null && (fix.bearing < 0 || fix.bearing >= 360))) invalid();
  return fix;
}

function createLocationForwarder({ deliver, authorized, now = Date.now, minimumIntervalMs = 1000, onError = () => {} } = {}) {
  if (typeof deliver !== "function" || typeof authorized !== "function" ||
      !Number.isFinite(minimumIntervalMs) || minimumIntervalMs < 0 || minimumIntervalMs > 60000) throw new Error("Invalid location forwarder");
  let pending = null, work = null, closed = false, lastStarted = -Infinity;
  const controller = new AbortController();
  const allowed = () => { try { return authorized() === true; } catch { return false; } };
  const report = code => { try { onError(code); } catch { /* Reporting must not strand the writer. */ } };
  async function drain() {
    while (pending && !closed) {
      const wait = minimumIntervalMs - (performance.now() - lastStarted);
      if (wait > 0) {
        try { await delay(wait, undefined, { signal: controller.signal }); }
        catch { break; }
      }
      if (closed) break;
      const wire = pending; pending = null;
      if (!allowed()) { report("authority-unavailable"); continue; }
      let fix;
      try { fix = normalizeLocation(wire, now()); }
      catch { report("invalid-location"); continue; }
      lastStarted = performance.now();
      try { await deliver(fix, { signal: controller.signal }); }
      catch { if (!closed) report("delivery-failed"); }
    }
  }
  const start = () => {
    if (work) return;
    work = Promise.resolve().then(drain).finally(() => {
      work = null;
      if (pending && !closed) start();
    });
  };
  return {
    submit(wire) {
      if (closed) throw new Error("Location feed closed");
      if (!allowed()) throw new Error("Location authority unavailable");
      normalizeLocation(wire, now()); pending = { ...wire }; start();
    },
    async idle() { while (work) await work; },
    async close() {
      closed = true; pending = null; controller.abort(new Error("Location feed closed"));
      if (work) await work;
    },
  };
}
module.exports = { normalizeLocation, createLocationForwarder };
