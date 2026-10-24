// SPDX-License-Identifier: GPL-3.0-only
const crypto = require("node:crypto");
const { EventEmitter } = require("node:events");
const { setTimeout: delay } = require("node:timers/promises");
const { validateDesired } = require("../waydroid-display/transaction");
const { validateState } = require("./display-helper-client");
const { validateProfile, preferredRole, canDriveSize } = require("./rear-display-settings");

const LEGACY = { renderer: 0, isHeadless: 0, isH264: 0, refreshRate: 2, quality: 90,
  isRearDisplayEnabled: 0, isRearDisplayPrioritised: 0, fixedDisplay: 0,
  landscapeNavigationMinHeight: 676 };
const DESIRED = ["width", "height", "density", "resolutionPreset", "isResponsive"];
const failure = (message, status = 409) => Object.assign(new Error(message), { status });
function createDisplayControl({ helper, clock = { now: Date.now, setTimeout, clearTimeout },
  randomBytes = crypto.randomBytes, graceMs = 5000, rearSettings, retireAuthority,
  retireTimeoutMs = 10000 } = {}) {
  if (rearSettings && typeof retireAuthority !== "function") throw new Error("Rear display requires authority retirement");
  if (!Number.isSafeInteger(retireTimeoutMs) || retireTimeoutMs < 1 || retireTimeoutMs > 30000) {
    throw new Error("Invalid authority retirement timeout");
  }
  const events = new EventEmitter(), sessions = new Map();
  let actual, generation = 0, epoch = 0, owner = null, active = null, queued = null, closed = false;
  let rear = rearSettings ? validateProfile(rearSettings.snapshot()) : null;
  let transfer = null, authorityFailed = false;
  const session = token => {
    const value = sessions.get(token);
    if (!value || closed) throw failure("Unknown display session", 403);
    return value;
  };
  const connected = value => value.display + value.touchscreen > 0;
  const cancelExpiry = value => { if (value.timer !== null) clock.clearTimeout(value.timer); value.timer = null; };
  const preferredOwner = () => {
    const candidates = [...sessions.values()].filter(value => value.display > 0);
    if (!rear?.enabled) return sessions.has(owner) ? owner : candidates[0]?.token || null;
    // An already-preferred owner retains its normal reconnect grace.
    if (sessions.get(owner)?.role === preferredRole(rear)) return owner;
    return candidates.find(value => value.role === preferredRole(rear))?.token ||
      (sessions.has(owner) ? owner : candidates[0]?.token || null);
  };
  async function reconcileOwner(claim) {
    if (active || queued || transfer || authorityFailed || actual?.recoveryRequired || closed) return false;
    const candidate = claim?.token ?? preferredOwner();
    const commitClaim = () => {
      if (!claim) return;
      const next = sessions.get(claim.token);
      if (!next || next.display < 1) throw failure('Display claimant disconnected', 403);
      for (const value of sessions.values()) {
        if (value.token !== claim.token && value.role === claim.role) value.role = claim.role === 'primary' ? 'rear' : 'primary';
      }
      next.role = claim.role;
    };
    if (candidate === owner) { if (claim) { commitClaim(); generation++; } return false; }
    if (!owner) { commitClaim(); owner = candidate; if (claim) generation++; events.emit("ownership"); return false; }
    const operation = { epoch, controller: new AbortController() };
    transfer = operation;
    let timer;
    try {
      // Block both peers before closing the old native channels. No stream resize.
      events.emit("authorityPause", owner);
      await Promise.race([
        Promise.resolve().then(() => retireAuthority(owner, { signal: operation.controller.signal, previousState: { ...actual } })),
        new Promise((_, reject) => { timer = clock.setTimeout(() => {
          operation.controller.abort(); reject(failure("Authority retirement timed out", 503));
        }, retireTimeoutMs); }),
      ]);
      if (operation.epoch !== epoch || closed) throw failure("Display authority revoked", 403);
      commitClaim(); owner = claim ? candidate : preferredOwner(); generation++;
      return true;
    } catch (error) {
      if (operation.epoch !== epoch || closed) throw failure("Display authority revoked", 403);
      authorityFailed = true; owner = null;
      throw failure("Authority retirement failed", 503);
    } finally {
      clock.clearTimeout(timer);
      if (transfer === operation) transfer = null;
      events.emit("ownership");
    }
  }
  const selectOwner = () => {
    if (rear) { void reconcileOwner().catch(() => {}); return; }
    if (owner || active || actual?.recoveryRequired || closed) return;
    const candidate = [...sessions.values()].find(value => value.display > 0);
    if (candidate) { owner = candidate.token; events.emit("ownership"); }
  };
  const scheduleExpiry = value => {
    cancelExpiry(value);
    value.timer = clock.setTimeout(() => {
      value.timer = null;
      if (connected(value) || !sessions.has(value.token)) return;
      if (owner === value.token && (active || transfer)) { scheduleExpiry(value); return; }
      sessions.delete(value.token);
      events.emit("expired", value.token);
      if (owner === value.token) { if (!rear) owner = null; selectOwner(); }
    }, graceMs);
    value.timer?.unref?.();
  };
  function snapshot(token) {
    session(token);
    if (!actual) throw failure("Display helper unavailable", 503);
    const canControl = owner === token && !actual.recoveryRequired && !transfer && !authorityFailed;
    return { ...LEGACY, ...actual, generation, displayControlSupported: 1,
      ...(rear ? { rearDisplayControlSupported: 1, displayRoleClaimSupported: 1, isRearDisplayEnabled: rear.enabled ? 1 : 0,
        isRearDisplayPrioritised: rear.prioritised ? 1 : 0, rearDisplayRevision: rear.revision,
        displayRole: session(token).role, canResize: canControl && canDriveSize(rear, session(token).role) } : {}),
      canControl,
      transactionStatus: authorityFailed ? "authority_failed" : transfer ? "transferring" :
        actual.recoveryRequired ? "recovery_required" : active ? "resizing" : "idle" };
  }
  async function refresh() {
    const deadline = Date.now() + 22000;
    for (;;) {
      try { return validateState(await helper.inspect()); }
      catch (error) { if (error.status !== 409 || Date.now() >= deadline) throw error; await delay(100); }
    }
  }
  async function run(job) {
    const controller = new AbortController(); active = { ...job, controller };
    events.emit("pause");
    try {
      const committed = validateState(await helper.resize(job.desired, { signal: controller.signal }));
      if (job.epoch !== epoch || owner !== job.token || !sessions.has(job.token)) {
        throw failure("Display authority revoked", 403);
      }
      actual = committed; generation++; job.resolve({ ...snapshot(job.token), transactionStatus: queued ? "resizing" : "idle" });
    } catch (error) {
      try {
        const recovered = await refresh();
        if (DESIRED.some(key => recovered[key] !== actual[key])) generation++;
        actual = { ...recovered, recoveryRequired: recovered.recoveryRequired || error.recoveryRequired === true };
      }
      catch { actual = { ...actual, recoveryRequired: true }; }
      if (job.epoch !== epoch) error = failure("Display authority revoked", 403);
      job.reject(error);
    } finally {
      active = null;
      const next = queued; queued = null;
      if (next && next.epoch === epoch && owner === next.token && !actual.recoveryRequired && !closed &&
          (!rear || canDriveSize(rear, sessions.get(next.token)?.role ?? null))) {
        void run(next);
      } else {
        if (next) next.reject(failure("Display authority revoked", 403));
        // Internal stream suspension must not consume the owner's reconnect grace.
        const currentOwner = sessions.get(owner);
        if (currentOwner && !connected(currentOwner)) scheduleExpiry(currentOwner);
        if (rear) await reconcileOwner().catch(() => {}); else selectOwner();
        events.emit("change");
      }
    }
  }
  async function resize(token, payload, csrfToken) {
    const value = session(token), state = snapshot(token);
    if (value.csrfToken !== csrfToken) throw failure("Invalid display CSRF token", 403);
    if (actual.recoveryRequired) throw failure("Display recovery required", 503);
    if (transfer || authorityFailed) throw failure("Display authority unavailable", 503);
    if (owner !== token) throw failure("Only the display owner can resize", 403);
    if (rear && !canDriveSize(rear, value.role)) throw failure("Only the preferred display role can resize", 403);
    if (!payload || Array.isArray(payload) || !Number.isSafeInteger(payload.generation) || payload.generation !== generation) {
      throw failure("Stale display generation");
    }
    for (const [key, input] of Object.entries(payload)) {
      if (DESIRED.includes(key) || key === "generation") continue;
      if (!(key in state) || input !== state[key]) throw failure("Unsupported display setting", 400);
    }
    const desired = validateDesired(Object.fromEntries(DESIRED.map(key => [key, payload[key]])));
    if (!active && DESIRED.every(key => desired[key] === actual[key])) return state;
    return new Promise((resolve, reject) => {
      const job = { token, desired, epoch, resolve, reject };
      if (active) { if (queued) queued.reject(failure("Pending resize superseded")); queued = job; }
      else void run(job);
    });
  }
  function revokeAll() {
    epoch++;
    transfer?.controller.abort();
    for (const value of sessions.values()) cancelExpiry(value);
    sessions.clear(); owner = null;
    if (queued) queued.reject(failure("Display authority revoked", 403)); queued = null;
    active?.controller.abort(failure("Display authority revoked", 403));
    events.emit("pause");
  }
  return Object.assign(events, {
    async initialize() { actual = validateState(await helper.inspect()); },
    createSession() {
      if (closed || !actual) throw failure("Display helper unavailable", 503);
      if (sessions.size >= 64) throw failure("Display session limit reached", 503);
      let token; do { token = randomBytes(32).toString("hex"); } while (sessions.has(token));
      const value = { token, csrfToken: randomBytes(32).toString("hex"), display: 0, touchscreen: 0, timer: null, role: null };
      sessions.set(token, value); scheduleExpiry(value);
      return { token, csrfToken: value.csrfToken };
    },
    connect(token, channel) {
      const value = session(token);
      if (!["display", "touchscreen"].includes(channel)) throw failure("Unknown display channel", 400);
      if (active || transfer || authorityFailed || actual.recoveryRequired) throw failure("Display transaction or recovery in progress", 503);
      if (channel === "touchscreen" && owner !== token) throw failure("Only the display owner can control touch", 403);
      cancelExpiry(value); value[channel]++;
      if (rear && channel === 'display' && value.role === null) {
        value.role = [...sessions.values()].some(other => other.token !== token && other.display > 0 && other.role === 'primary') ? 'rear' : 'primary';
      }
      selectOwner(); return snapshot(token);
    },
    disconnect(token, channel) {
      const value = sessions.get(token); if (!value || !["display", "touchscreen"].includes(channel)) return;
      value[channel] = Math.max(0, value[channel] - 1); if (!connected(value)) scheduleExpiry(value);
    },
    assertOwner(token, csrfToken) {
      if (session(token).csrfToken !== csrfToken || !snapshot(token).canControl) throw failure("Display owner and CSRF required", 403);
    },
    async registerRole(token, role, csrfToken, options = {}) {
      const value = session(token);
      if (!rear) throw failure("Rear display unsupported", 404);
      if (value.csrfToken !== csrfToken) throw failure("Invalid display CSRF token", 403);
      if (!["primary", "rear"].includes(role)) throw failure("Invalid display role", 400);
      if (transfer || authorityFailed) throw failure("Display authority unavailable", 503);
      if (Object.keys(options).length) {
        if (Object.keys(options).sort().join(',') !== 'claim,generation' || options.claim !== true || !Number.isSafeInteger(options.generation)) throw failure('Invalid display role claim', 400);
        if (options.generation !== generation || active || queued || actual.recoveryRequired) throw failure('Stale or busy display claim');
        if (value.display < 1 || role !== preferredRole(rear)) throw failure('Only a connected preferred display can claim control');
        await reconcileOwner({ token, role });
        return snapshot(token);
      }
      const changed = value.role !== role; value.role = role;
      const handedOff = await reconcileOwner();
      if (changed && rear.enabled && !active && !handedOff) generation++;
      return snapshot(token);
    },
    async updateRearSettings(token, payload, csrfToken) {
      this.assertOwner(token, csrfToken);
      if (!rear) throw failure("Rear display unsupported", 404);
      if (active || queued) throw failure("Display transaction in progress");
      const previousRevision = rear.revision;
      rear = validateProfile(rearSettings.update(payload));
      const handedOff = await reconcileOwner();
      if (!handedOff && previousRevision !== rear.revision) generation++;
      return snapshot(token);
    },
    rearSnapshot(token) {
      session(token);
      if (!rear) throw failure("Rear display unsupported", 404);
      return { ...rear };
    }, snapshot, resize, revokeAll,
    close() { closed = true; revokeAll(); events.removeAllListeners(); },
  });
}
module.exports = { createDisplayControl, LEGACY };
