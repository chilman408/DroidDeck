// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const { createGpsLifecycle } = require("./gps-lifecycle");
const CLIENT = "a".repeat(32), OTHER = "b".repeat(32), NATIVE = "c".repeat(32);
function fixture({ saved = { version: 1, enabled: true }, journal = null, owner = null, foreign = false, closeFails = false } = {}) {
  const calls = []; let clock = 0;
  const preferences = { load: async () => saved, save: async value => { saved = value; } };
  const leases = { load: async () => journal, save: async value => { journal = value; } };
  const adapter = {
    async inspect(token) { calls.push("inspect"); return { present: foreign || owner !== null, owned: owner === token }; },
    async open(token, options) { options?.signal?.throwIfAborted(); calls.push("open"); owner = token; },
    async close(token) { calls.push("close"); if (closeFails || owner !== token) throw new Error("Cleanup failed"); owner = null; },
    async deliver(token, fix, options) { options.signal?.throwIfAborted(); assert.equal(owner, token); calls.push(fix); },
  };
  return { calls, adapter, get journal() { return journal; }, get saved() { return saved; },
    advance: value => { clock += value; },
    service: createGpsLifecycle({ adapter, preferences, leases, token: () => NATIVE, now: () => clock }) };
}
test("GPS defaults off and creates a provider only after explicit enable and an active feed", async () => {
  const f = fixture({ saved: null }); await f.service.reconcile();
  assert.deepEqual(await f.service.inspect(), { enabled: false, active: false, supported: true, mock: true, recoveryRequired: false });
  await assert.rejects(f.service.begin(CLIENT), /GPS disabled/); await f.service.setEnabled(true);
  await f.service.begin(CLIENT);
  assert.deepEqual(f.journal, { version: 1, lease: NATIVE });
  await assert.rejects(f.service.begin(OTHER), /GPS feed conflict/);
  await assert.rejects(f.service.deliver(OTHER, {}), /GPS feed conflict/);
  await assert.rejects(f.service.end(OTHER), /GPS feed conflict/);
  assert.equal(f.calls.filter(call => call === "open").length, 1);
  await f.service.end(CLIENT); assert.equal(f.journal, null);
  assert.equal((await f.service.inspect()).active, false);
});
test("queued cancelled preferences never commit while another native operation is active", async () => {
  const f = fixture(); await f.service.reconcile(); await f.service.begin(CLIENT);
  let release, entered; const gate = new Promise(resolve => { release = resolve; });
  const started = new Promise(resolve => { entered = resolve; });
  const deliver = f.adapter.deliver;
  f.adapter.deliver = async (...args) => { entered(); await gate; return deliver(...args); };
  const write = f.service.deliver(CLIENT, { synthetic: true }); await started;
  const controller = new AbortController();
  const update = f.service.setEnabled(false, { signal: controller.signal });
  const rejected = assert.rejects(update); controller.abort(); release(); await write; await rejected;
  assert.deepEqual(f.saved, { version: 1, enabled: true }); await f.service.shutdown();
});
test("lifecycle refuses array client tokens before opening a provider", async () => {
  const f = fixture(); await f.service.reconcile();
  await assert.rejects(f.service.begin([CLIENT])); assert.equal(f.journal, null);
});
test("disabled GPS blocks feeds and saved preferences contain no coordinates", async () => {
  const f = fixture(); await f.service.reconcile(); await f.service.begin(CLIENT);
  await f.service.setEnabled(false);
  assert.deepEqual(f.saved, { version: 1, enabled: false }); assert.equal(f.journal, null);
  await assert.rejects(f.service.begin(CLIENT), /GPS disabled/);
  await f.service.setEnabled(true); await f.service.begin(OTHER);
  await f.service.end(OTHER);
});
test("expired feed removes its provider before another session can start", async () => {
  const f = fixture(); await f.service.reconcile(); await f.service.begin(CLIENT);
  f.advance(15001); await f.service.sweep(); assert.equal(f.journal, null);
  await f.service.begin(OTHER); await f.service.heartbeat(OTHER);
  f.advance(10000); await f.service.heartbeat(OTHER); f.advance(10000);
  assert.equal((await f.service.inspect()).active, true); await f.service.shutdown();
  assert.equal(f.journal, null);
});
test("restart removes only a journaled owned provider and leaves foreign providers untouched", async () => {
  const owned = fixture({ journal: { version: 1, lease: NATIVE }, owner: NATIVE });
  await owned.service.reconcile(); assert.equal(owned.journal, null); assert.ok(owned.calls.includes("close"));
  const foreign = fixture({ journal: { version: 1, lease: NATIVE }, foreign: true });
  await foreign.service.reconcile(); assert.equal((await foreign.service.inspect()).recoveryRequired, true);
  assert.equal(foreign.calls.includes("close"), false); assert.ok(foreign.journal);
  await assert.rejects(foreign.service.begin(CLIENT), /GPS recovery required/);
});
test("failed cleanup retains the journal, reports recovery and cannot reopen GPS", async () => {
  const f = fixture({ closeFails: true }); await f.service.reconcile(); await f.service.begin(CLIENT);
  await assert.rejects(f.service.setEnabled(false), /GPS cleanup failed/);
  assert.deepEqual(f.saved, { version: 1, enabled: false }); assert.ok(f.journal);
  assert.equal((await f.service.inspect()).recoveryRequired, true);
  await assert.rejects(f.service.setEnabled(true), /GPS recovery required/);
});
test("cancelled creation cleans up its journaled provider and late writers do not run", async () => {
  const f = fixture(); await f.service.reconcile(); const controller = new AbortController();
  const open = f.adapter.open; f.adapter.open = async (...args) => { await open(...args); controller.abort(); };
  await assert.rejects(f.service.begin(CLIENT, { signal: controller.signal })); assert.equal(f.journal, null);
  assert.equal((await f.service.inspect()).active, false);
  await assert.rejects(f.service.deliver(CLIENT, {}, { signal: controller.signal }));
});
