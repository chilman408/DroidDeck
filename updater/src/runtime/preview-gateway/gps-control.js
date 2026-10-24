// SPDX-License-Identifier: GPL-3.0-only
const crypto = require("node:crypto");
const { createLocationForwarder } = require("./gps-location");
const { validateState } = require("../waydroid-display/gps-protocol");
const failure = (message, status = 503) => Object.assign(new Error(message), { status });
function createGpsControl({ helper, minimumIntervalMs = 1000, heartbeatMs = 1000 } = {}) {
  let state = { enabled: false, active: false, supported: false, mock: true, recoveryRequired: false };
  let feed = null, closed = false, updating = false, updateController = null, retiring = false;
  let updateSettled, finishUpdate;
  let revision = 0, refreshId = 0;
  const allowed = check => { try { return check() === true; } catch { return false; } };
  async function refresh() {
    const observed = revision, request = ++refreshId;
    try {
      const actual = validateState(await helper.inspect());
      if (observed === revision && request === refreshId && !updating) state = actual;
    } catch {
      if (observed === revision && request === refreshId && !updating) state = { ...state, supported: false };
      throw failure("GPS helper unavailable");
    }
    return { ...state };
  }
  function available() {
    if (closed || updating || retiring || !state.supported || !state.enabled || state.recoveryRequired) throw failure("GPS unavailable");
    if (feed) throw failure("GPS feed busy", 409);
  }
  async function stop(current) {
    if (!current) return;
    if (current.cleanup) return current.cleanup;
    current.closed = true; current.controller.abort(); clearInterval(current.timer);
    revision++;
    // Keep the slot until cancelled Java work settles and removal is acknowledged.
    current.cleanup = Promise.resolve().then(async () => {
      await current.started.catch(() => {}); await current.forwarder.close();
      try { state = validateState(await helper.end(current.lease)); }
      catch { await refresh().catch(() => {}); }
      revision++; if (feed === current) feed = null;
    });
    if (current.socket.readyState !== 3) current.socket.terminate();
    return current.cleanup;
  }
  async function attach(socket, authorized) {
    if (!allowed(authorized)) throw failure("GPS authority required", 403);
    available();
    const current = { socket, authorized, lease: crypto.randomBytes(16).toString("hex"), ready: false,
      closed: false, controller: new AbortController(), lastHeartbeat: Date.now(), heartbeat: false };
    revision++; feed = current;
    const authority = () => !current.closed && !closed && !updating && !retiring && state.enabled && state.supported &&
      !state.recoveryRequired && allowed(authorized);
    current.forwarder = createLocationForwarder({ minimumIntervalMs, authorized: () => current.ready && authority(),
      deliver: (fix, options) => helper.deliver(current.lease, fix, options), onError: () => { void stop(current); } });
    socket.on("error", () => { void stop(current); }); socket.once("close", () => { void stop(current); });
    socket.on("message", (data, binary) => {
      if (!current.ready) return;
      try {
        if (binary || data.length > 2048 || !authority()) throw new Error();
        current.forwarder.submit(JSON.parse(data.toString("utf8")));
      } catch { void stop(current); }
    });
    current.started = helper.begin(current.lease, { signal: current.controller.signal });
    try {
      state = validateState(await current.started);
      revision++;
      if (!authority() || socket.readyState !== 1) throw failure("GPS authority revoked", 403);
      current.ready = true; socket.send(JSON.stringify({ version: 1, ready: true, mock: true }));
      current.timer = setInterval(() => {
        if (!authority()) { void stop(current); return; }
        if (current.heartbeat || Date.now() - current.lastHeartbeat < 5000) return;
        current.heartbeat = true;
        void helper.heartbeat(current.lease, { signal: current.controller.signal })
          .then(value => { if (!current.closed && !updating) { state = validateState(value); revision++; } current.lastHeartbeat = Date.now(); })
          .catch(() => { void stop(current); }).finally(() => { current.heartbeat = false; });
      }, heartbeatMs);
      current.timer.unref?.();
    } catch { await stop(current); throw failure("GPS unavailable"); }
  }
  return {
    initialize: refresh, refresh, snapshot: () => ({ ...state }),
    assertAvailable: available, attach,
    async update(enabled, { authorized, signal } = {}) {
      if (typeof enabled !== "boolean") throw failure("Invalid GPS value", 400);
      if (!allowed(authorized)) throw failure("GPS authority required", 403);
      if (closed || updating || retiring || !state.supported) throw failure("GPS unavailable");
      updating = true; revision++; updateController = new AbortController();
      updateSettled = new Promise(resolve => { finishUpdate = resolve; });
      const abort = () => updateController?.abort(); signal?.addEventListener("abort", abort, { once: true });
      if (signal?.aborted) abort();
      const authorityTimer = setInterval(() => { if (!allowed(authorized)) abort(); }, heartbeatMs);
      authorityTimer.unref?.();
      try {
        const stopping = stop(feed);
        await stopping;
        if (!allowed(authorized)) abort();
        updateController.signal.throwIfAborted();
        state = validateState(await helper.setEnabled(enabled, { signal: updateController.signal }));
        if (!allowed(authorized) || closed) throw failure("GPS authority revoked", 403);
        return { ...state };
      } catch (error) {
        await refresh().catch(() => {}); throw error.status ? error : failure("GPS setting unavailable");
      } finally {
        clearInterval(authorityTimer); signal?.removeEventListener("abort", abort);
        revision++; updating = false; updateController = null; finishUpdate(); updateSettled = null;
      }
    },
    async retireFeed({ signal } = {}) {
      signal?.throwIfAborted();
      if (closed || retiring) throw failure("GPS retirement unavailable");
      retiring = true; revision++;
      const hadNative = feed !== null || state.active || state.supported;
      try {
        updateController?.abort(); await updateSettled;
        await stop(feed);
        signal?.throwIfAborted();
        if (hadNative) {
          state = validateState(await helper.inspect({ signal }));
          if (state.active || state.recoveryRequired) throw failure("GPS retirement not acknowledged");
        }
        return { ...state };
      } finally { revision++; retiring = false; }
    },
    async close() { closed = true; updateController?.abort(); await stop(feed); },
  };
}
module.exports = { createGpsControl };
