// SPDX-License-Identifier: GPL-3.0-only
const crypto = require("node:crypto");
const { performance } = require("node:perf_hooks");
function createGpsLifecycle({ adapter, preferences, leases, token = () => crypto.randomBytes(16).toString("hex"),
  now = () => performance.now() } = {}) {
  let enabled = false, active = null, journal = null, recoveryRequired = false, ready = false, closing = false;
  let chain = Promise.resolve(), pending = 0;
  const state = () => ({ enabled, active: active !== null, supported: ready, mock: true, recoveryRequired });
  const validToken = value => { if (typeof value !== "string" || !/^[a-f0-9]{32}$/.test(value)) throw new Error("Invalid GPS lease"); };
  const available = () => {
    if (!ready || closing) throw new Error("GPS service unavailable");
    if (recoveryRequired) throw Object.assign(new Error("GPS recovery required"), { recoveryRequired: true });
  };
  function serialize(work, { signal } = {}, internal = false) {
    if (!internal && (pending >= 8 || closing)) return Promise.reject(new Error("GPS service busy"));
    pending++;
    const result = chain.then(() => { signal?.throwIfAborted(); return work(); });
    chain = result.catch(() => {});
    return result.finally(() => { pending--; });
  }
  async function cleanup() {
    active = null;
    if (!journal) return;
    try {
      const actual = await adapter.inspect(journal.lease);
      if (actual.present && !actual.owned) throw new Error("Foreign GPS provider");
      if (actual.owned) await adapter.close(journal.lease);
      if ((await adapter.inspect(journal.lease)).present) throw new Error("GPS provider remains");
      await leases.save(null); journal = null;
    } catch { recoveryRequired = true; throw Object.assign(new Error("GPS cleanup failed"), { recoveryRequired: true }); }
  }
  async function sweep() { if (active && now() >= active.expires) await cleanup(); }
  function owned(client) {
    validToken(client); available();
    if (!active || active.client !== client) throw new Error("GPS feed conflict");
  }
  return {
    reconcile: () => serialize(async () => {
      if (ready) throw new Error("GPS service already initialized");
      const saved = await preferences.load();
      if (saved !== null && (saved.version !== 1 || typeof saved.enabled !== "boolean" || Object.keys(saved).length !== 2)) {
        throw new Error("Invalid GPS preferences");
      }
      enabled = saved?.enabled ?? false;
      journal = await leases.load();
      if (journal !== null) {
        if (journal.version !== 1 || Object.keys(journal).length !== 2) throw new Error("Invalid GPS journal");
        validToken(journal.lease);
        await cleanup().catch(() => {});
      } else {
        const probe = token(); validToken(probe);
        recoveryRequired = (await adapter.inspect(probe)).present;
      }
      ready = true; return state();
    }),
    inspect: () => serialize(async () => { await sweep(); return state(); }),
    setEnabled: (value, options) => serialize(async () => {
      if (typeof value !== "boolean") throw new Error("Invalid GPS preference");
      if (!ready || closing) throw new Error("GPS service unavailable");
      if (value && recoveryRequired) throw Object.assign(new Error("GPS recovery required"), { recoveryRequired: true });
      await preferences.save({ version: 1, enabled: value }); enabled = value;
      if (!value) await cleanup();
      return state();
    }, options),
    begin: (client, options) => serialize(async () => {
      validToken(client); available(); await sweep(); available();
      if (!enabled) throw new Error("GPS disabled");
      if (active && active.client !== client) throw new Error("GPS feed conflict");
      if (active) { active.expires = now() + 15000; return state(); }
      const lease = token(); validToken(lease);
      const actual = await adapter.inspect(lease, options);
      if (actual.present) { recoveryRequired = true; throw new Error("GPS recovery required"); }
      journal = { version: 1, lease };
      await leases.save(journal);
      try {
        await adapter.open(lease, options); options?.signal?.throwIfAborted();
        active = { client, expires: now() + 15000 }; return state();
      } catch {
        await cleanup(); throw new Error("GPS creation failed");
      }
    }, options),
    heartbeat: (client, options) => serialize(async () => {
      await sweep(); owned(client); active.expires = now() + 15000; return state();
    }, options),
    deliver: (client, fix, options = {}) => serialize(async () => {
      await sweep(); owned(client);
      try { await adapter.deliver(journal.lease, fix, options); options.signal?.throwIfAborted(); }
      catch { await cleanup(); throw new Error("GPS delivery failed"); }
      active.expires = now() + 15000; return state();
    }, options),
    end: (client, options) => serialize(async () => {
      validToken(client); available(); await sweep();
      if (active) { owned(client); await cleanup(); }
      return state();
    }, options),
    sweep: () => serialize(async () => { await sweep(); return state(); }),
    shutdown() {
      closing = true;
      return serialize(async () => { await cleanup(); ready = false; }, {}, true);
    },
  };
}
module.exports = { createGpsLifecycle };
