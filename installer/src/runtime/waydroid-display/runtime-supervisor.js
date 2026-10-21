// SPDX-License-Identifier: GPL-3.0-only
const SERVICES = Object.freeze(["api", "display-helper", "display", "gps", "video", "video-relay",
  "audio", "audio-relay", "gateway", "lan-relay"]);
function createRuntimeSupervisor({ waitReady, startService, stopService, pauseTouch } = {}) {
  if (![waitReady, startService, stopService, pauseTouch].every(fn => typeof fn === "function")) {
    throw new Error("Runtime lifecycle adapters required");
  }
  let phase = "stopped", controller, opening, stopping, cleaning, cleanupFailure;
  const attempted = [];
  const snapshot = () => ({ phase, recoveryRequired: phase === "blocked" });
  async function cleanup() {
    if (cleanupFailure) throw cleanupFailure;
    if (cleaning) return cleaning;
    if (!attempted.length) { phase = "stopped"; return; }
    const owned = [...attempted].reverse();
    cleaning = Promise.resolve().then(async () => {
      const errors = [];
      for (const name of owned) {
        try { await stopService(name); } catch (error) { errors.push(error); }
      }
      if (owned.includes("display-helper")) {
        try { await pauseTouch(); } catch (error) { errors.push(error); }
      }
      if (errors.length) {
        phase = "blocked"; cleanupFailure = new AggregateError(errors, "Runtime cleanup failed");
        throw cleanupFailure;
      }
      attempted.length = 0; phase = "stopped";
    });
    try { await cleaning; } finally { cleaning = null; }
  }
  return {
    snapshot,
    start({ signal } = {}) {
      if (phase === "blocked") return Promise.reject(new Error("Runtime recovery required"));
      if (phase !== "stopped" || stopping) return Promise.reject(new Error("Runtime busy"));
      if (signal?.aborted) return Promise.reject(signal.reason);
      phase = "waiting"; controller = new AbortController();
      const current = controller, abort = () => current.abort(signal.reason);
      signal?.addEventListener("abort", abort, { once: true });
      if (signal?.aborted) abort();
      opening = Promise.resolve().then(async () => {
        try {
          current.signal.throwIfAborted(); await waitReady({ signal: current.signal });
          current.signal.throwIfAborted(); phase = "starting";
          for (const name of SERVICES) {
            current.signal.throwIfAborted(); attempted.push(name);
            await startService(name, { signal: current.signal }); current.signal.throwIfAborted();
          }
          phase = "ready"; return snapshot();
        } catch (error) {
          try { await cleanup(); }
          catch (failure) { throw new AggregateError([error, failure], "Runtime startup and cleanup failed"); }
          throw error;
        } finally {
          signal?.removeEventListener("abort", abort);
          if (controller === current) controller = null;
        }
      });
      return opening;
    },
    stop() {
      if (stopping) return stopping;
      if (cleanupFailure) return Promise.reject(cleanupFailure);
      if (phase === "stopped") return Promise.resolve(snapshot());
      phase = "stopping"; controller?.abort(new Error("Runtime stopped"));
      const pending = opening;
      stopping = Promise.resolve().then(async () => {
        await pending?.catch(() => {}); await cleanup(); return snapshot();
      }).finally(() => { stopping = null; });
      return stopping;
    },
  };
}
module.exports = { createRuntimeSupervisor, SERVICES };
