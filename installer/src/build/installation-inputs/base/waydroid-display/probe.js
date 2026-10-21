// SPDX-License-Identifier: GPL-3.0-only
const { readFrame } = require("./frame-check");

async function bounded(operation, timeoutMs, settleMs = 1000) {
  const controller = new AbortController();
  let timer, settleTimer;
  const settled = Promise.resolve().then(() => operation(controller.signal))
    .then((value) => ({ value }), (error) => ({ error }));
  try {
    const outcome = await Promise.race([
      settled,
      new Promise((resolve) => {
        timer = setTimeout(() => {
          const error = new Error("Display probe timed out");
          controller.abort(error);
          resolve({ timedOut: error });
        }, timeoutMs);
      }),
    ]);
    if (outcome.timedOut) {
      const stopped = await Promise.race([settled, new Promise((resolve) => {
        settleTimer = setTimeout(() => resolve(null), settleMs);
      })]);
      if (!stopped || stopped.error?.quiescenceRequired) outcome.timedOut.quiescenceRequired = true;
      throw outcome.timedOut;
    }
    if (outcome.error) throw outcome.error;
    return outcome.value;
  } finally { clearTimeout(timer); clearTimeout(settleTimer); }
}

function matches(frame, desired) {
  if (!frame.nonblack) throw new Error("Capture is black");
  if (frame.width !== desired.width || frame.height !== desired.height) {
    throw new Error(`Capture mismatch: requested ${desired.width}x${desired.height}, got ${frame.width}x${frame.height}`);
  }
}

async function runProbe(adapter, targets, { timeoutMs = 20000, settleMs = 1000 } = {}) {
  if (!Array.isArray(targets) || !targets.length) throw new Error("Probe requires target sizes");
  if (!Number.isFinite(timeoutMs) || timeoutMs < 50) throw new Error("Invalid probe timeout");
  const result = { verified: false, frames: [], timings: [], recoveryRequired: false };
  const baselineStart = Date.now();
  const snapshot = await bounded((signal) => adapter.inspect({ signal }), timeoutMs);
  const baseline = readFrame(await bounded((signal) => adapter.capture({ signal }), timeoutMs));
  matches(baseline, snapshot);
  result.baseline = baseline;
  const baselineMs = Date.now() - baselineStart;
  for (const target of targets) {
    const timing = { forwardMs: 0, restoreMs: 0, totalMs: 0 };
    const start = Date.now();
    const desired = { ...target, density: snapshot.density };
    try {
      const frame = await bounded(async (signal) => {
        await adapter.applySize(desired, { signal });
        const captured = readFrame(await adapter.capture({ signal }));
        matches(captured, desired);
        return captured;
      }, Math.floor(timeoutMs * 0.6), settleMs);
      result.frames.push(frame);
    } catch (error) {
      result.error = error.message;
      if (error.quiescenceRequired) result.quiescenceRequired = result.recoveryRequired = true;
    }
    finally {
      timing.forwardMs = Date.now() - start;
      const restoreStart = Date.now();
      try {
        if (result.quiescenceRequired) throw Object.assign(new Error("Forward operation has not stopped; manual recovery required"), { quiescenceRequired: true });
        result.restored = await bounded(async (signal) => {
          await adapter.restore(snapshot, { signal });
          const restored = readFrame(await adapter.capture({ signal }));
          matches(restored, baseline);
          return restored;
        }, Math.floor(timeoutMs * 0.4), settleMs);
      } catch (error) {
        result.recoveryRequired = true;
        result.recoveryError = error.message;
        if (error.quiescenceRequired) result.quiescenceRequired = true;
      }
      timing.restoreMs = Date.now() - restoreStart;
      timing.totalMs = baselineMs + Date.now() - start;
      result.timings.push(timing);
    }
    if (result.error || result.recoveryRequired) return result;
  }
  result.verified = true;
  return result;
}

if (require.main === module) {
  console.error("Standalone resizing disabled; use cleanup-protected session-probe.js --verify-logical");
  process.exitCode = 2;
}

module.exports = { runProbe };
