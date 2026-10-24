// SPDX-License-Identifier: GPL-3.0-only
const { setTimeout: delay } = require("node:timers/promises");
async function runRuntimeLoop({ supervisor, ready, healthy, signal,
  sleep = (ms, options) => delay(ms, undefined, options), report = () => {} } = {}) {
  if (!supervisor || ![ready, healthy, sleep, report].every(fn => typeof fn === "function") || !signal) {
    throw new Error("Runtime loop adapters and cancellation required");
  }
  const options = { signal };
  while (!signal.aborted) {
    let attempted = false, failure;
    try {
      await ready(options); signal.throwIfAborted();
      attempted = true;
      await supervisor.start(options); signal.throwIfAborted(); report("ready");
      while (!signal.aborted) {
        await sleep(1000, options); signal.throwIfAborted();
        if (!await healthy(options)) throw new Error("Runtime health lost");
      }
    } catch (error) { failure = error; }
    if (attempted) {
      try { await supervisor.stop(); }
      catch (error) {
        report("blocked");
        if (failure) throw new AggregateError([failure, error], "Runtime cleanup failed");
        throw error;
      }
    }
    if (supervisor.snapshot().recoveryRequired || supervisor.snapshot().phase === "blocked") {
      report("blocked"); throw failure || new Error("Runtime recovery required");
    }
    if (signal.aborted) return;
    report("waiting");
    try { await sleep(2000, options); }
    catch (error) { if (!signal.aborted) throw error; }
  }
}
module.exports = { runRuntimeLoop };
