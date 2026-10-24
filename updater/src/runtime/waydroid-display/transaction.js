// SPDX-License-Identifier: GPL-3.0-only
const { validateSize } = require("./host-adapter");
const { readFrame } = require("./frame-check");

const FIELDS = ["width", "height", "density", "resolutionPreset", "isResponsive"];
function validateDesired(value) {
  if (!value || typeof value !== "object" || Array.isArray(value) ||
      Object.keys(value).some(key => !FIELDS.includes(key)) ||
      !Number.isInteger(value.resolutionPreset) || value.resolutionPreset < 0 || value.resolutionPreset > 4 ||
      ![0, 1].includes(value.isResponsive) || value.density !== 180) throw new Error("Unsupported display state");
  validateSize(value);
  return Object.fromEntries(FIELDS.map(key => [key, value[key]]));
}
function verifyFrame(buffer, size) {
  const frame = readFrame(buffer);
  if (!frame.nonblack || frame.width !== size.width || frame.height !== size.height) {
    throw new Error("Display capture does not match the requested nonblack layout");
  }
}

// Wait for cancellation to settle before allowing rollback to mutate the same display.
async function phase(operation, ms, parent) {
  const controller = new AbortController();
  const cancel = () => controller.abort(parent.reason);
  parent?.throwIfAborted();
  parent?.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(() => controller.abort(new Error("Display transaction timed out")), ms);
  let settleTimer;
  const pending = Promise.resolve().then(() => operation(controller.signal));
  try {
    return await Promise.race([pending, new Promise((_resolve, reject) => {
      controller.signal.addEventListener("abort", () => {
        settleTimer = setTimeout(() => reject(Object.assign(new Error("Display operation has not stopped"),
          { quiescenceRequired: true })), Math.min(500, ms));
      }, { once: true });
    })]);
  } finally {
    clearTimeout(timer);
    clearTimeout(settleTimer);
    parent?.removeEventListener("abort", cancel);
  }
}

function createTransactions({ adapter, store, timeoutMs = 20000 }) {
  let busy = false, recoveryRequired = false;
  const preferences = async (actual) => {
    const saved = await store.load();
    if (saved !== null) validateDesired(saved);
    return { width: actual.width, height: actual.height, density: actual.density,
      resolutionPreset: saved?.resolutionPreset ?? 0, isResponsive: saved?.isResponsive ?? 1 };
  };
  async function readState() {
    if (busy) throw Object.assign(new Error("Display transaction already running"), { status: 409 });
    busy = true;
    try {
      const actual = await phase(signal => adapter.inspect({ signal }), timeoutMs);
      return { ...await preferences(actual), recoveryRequired };
    } finally { busy = false; }
  }
  async function apply(input, { signal: parent, force = false } = {}) {
    const desired = validateDesired(input);
    if (recoveryRequired) throw Object.assign(new Error("Display recovery required"), { recoveryRequired: true });
    if (busy) throw Object.assign(new Error("Display transaction already running"), { status: 409 });
    busy = true;
    let snapshot, previous, paused = false;
    const started = Date.now();
    try {
      return await phase(async signal => {
        snapshot = await adapter.inspect({ signal });
        previous = await preferences(snapshot);
        if (desired.density !== snapshot.density) throw new Error("Density does not match this image");
        if (!force && FIELDS.every(key => desired[key] === previous[key])) return { ...previous, recoveryRequired: false };
        paused = true;
        await adapter.pause({ signal });
        await adapter.applySize(desired, { signal });
        await adapter.remap(desired, { signal });
        await adapter.verifyTouch(desired, { signal });
        verifyFrame(await adapter.capture({ signal }), desired);
        signal.throwIfAborted();
        await store.save(desired);
        signal.throwIfAborted();
        await adapter.resume(desired, { signal });
        signal.throwIfAborted();
        return { ...desired, recoveryRequired: false };
      }, Math.floor(timeoutMs * 0.6), parent);
    } catch (error) {
      if (paused) {
        try {
          if (error.quiescenceRequired) throw new Error("Forward mutation is still active");
          // Reserve time to close a remapped device if recovery fails verification.
          const remaining = timeoutMs - (Date.now() - started) - Math.min(2000, Math.floor(timeoutMs * 0.1));
          if (remaining < 1) throw new Error("Rollback budget exhausted");
          await phase(async signal => {
            await adapter.pause({ signal });
            await adapter.restore(snapshot, { signal });
            await adapter.remap(previous, { signal });
            await adapter.verifyTouch(previous, { signal });
            verifyFrame(await adapter.capture({ signal }), previous);
            signal.throwIfAborted();
            await store.save(previous);
            await adapter.resume(previous, { signal });
          }, remaining);
        } catch (rollbackError) {
          recoveryRequired = true;
          error.recoveryRequired = true;
          error.rollbackError = rollbackError;
          try {
            if (error.quiescenceRequired || rollbackError.quiescenceRequired) {
              throw new Error("Touch cleanup requires the active mutation to stop");
            }
            const remaining = timeoutMs - (Date.now() - started);
            if (remaining < 1) throw new Error("Touch cleanup budget exhausted");
            await phase(signal => adapter.pause({ signal }), remaining);
          } catch (cleanupError) { error.cleanupError = cleanupError; }
        }
      }
      throw error;
    } finally { busy = false; }
  }
  async function reconcile() {
    const saved = await store.load();
    const initial = saved === null ? await readState() : saved;
    return apply(Object.fromEntries(FIELDS.map(key => [key, initial[key]])), { force: true });
  }
  async function retireTouch({ signal: parent } = {}) {
    if (recoveryRequired) throw Object.assign(new Error("Display recovery required"), { recoveryRequired: true });
    if (busy) throw Object.assign(new Error("Display transaction already running"), { status: 409 });
    busy = true;
    let paused = false;
    const started = Date.now();
    try {
      return await phase(async signal => {
        const before = await adapter.inspect({ signal });
        const desired = validateDesired(await preferences(before));
        paused = true;
        await adapter.pause({ signal });
        await adapter.remap(desired, { signal });
        await adapter.verifyTouch(desired, { signal });
        const after = await adapter.inspect({ signal });
        if (["width", "height", "density"].some(key => before[key] !== after[key])) {
          throw new Error("Display geometry changed during touch retirement");
        }
        signal.throwIfAborted();
        await adapter.resume(desired, { signal });
        signal.throwIfAborted();
        return { ...desired, recoveryRequired: false };
      }, Math.floor(timeoutMs * 0.6), parent);
    } catch (error) {
      if (paused) {
        recoveryRequired = true; error.recoveryRequired = true;
        try {
          if (error.quiescenceRequired) throw new Error("Touch retirement has not stopped");
          const remaining = timeoutMs - (Date.now() - started);
          if (remaining < 1) throw new Error("Touch cleanup budget exhausted");
          await phase(signal => adapter.pause({ signal }), remaining);
        } catch (cleanupError) { error.cleanupError = cleanupError; }
      }
      throw error;
    } finally { busy = false; }
  }
  return { readState, apply, reconcile, retireTouch };
}
module.exports = { createTransactions, validateDesired, verifyFrame, phase };
