// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test"), assert = require("node:assert/strict");
const { createNativeRuntime } = require("./runtime-native");
function fixture(original = null, configuration) {
  let state = original, fail = false; const calls = [];
  const native = createNativeRuntime({ configuration, prepareDirectory: async () => {}, checkScript: async () => {},
    store: { load: async () => state, save: async value => { calls.push(["store", value]); state = value; } },
    run: async (file, args) => { calls.push([file, args]); if (fail) throw new Error("cleanup failed"); return Buffer.from(args.at(-1) === "--assert-vacant" ? "VACANT" : "REMOVED"); } });
  return { native, calls, state: () => state, fail: () => { fail = true; } };
}
test("native lease is durable before workers start and fixed role cleanup does not erase it early", async () => {
  const f = fixture(), owner = "a".repeat(32); await f.native.begin(owner);
  assert.deepEqual(f.calls[0], ["store", { version: 1, lease: owner }]);
  await f.native.stop("display"); assert.equal(f.state().lease, owner);
  await f.native.finish(); assert.equal(f.state(), null);
});
test('portable native cleanup uses protected configured root and retains the durable lease on failure', async () => {
  const config = { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40' }, lease = 'e'.repeat(32);
  const f = fixture(null, config); config.user = 'root';
  await f.native.begin(lease); await f.native.stop('video');
  assert.deepEqual(f.calls[1], ['/usr/bin/python3', ['/opt/droiddeck/runtime/waydroid-display/native-stop.py', lease, 'video']]);
  f.fail(); await assert.rejects(f.native.finish(), /cleanup failed/); assert.equal(f.state().lease, lease);
  assert.throws(() => fixture(null, { user: 'root', uid: 1200, gid: 1201, lanAddress: '10.20.30.40' }));
});
test("interrupted controller recovers only its stored lease before clearing the journal", async () => {
  const owner = "b".repeat(32), f = fixture({ version: 1, lease: owner }); await f.native.recover();
  assert.deepEqual(f.calls[0][1].slice(-2), [owner, "all"]); assert.equal(f.state(), null);
});
test("failed cleanup retains native lease and rejects unsafe roles or tokens", async () => {
  const f = fixture(); await f.native.begin("c".repeat(32)); f.fail();
  await assert.rejects(f.native.finish(), /cleanup failed/); assert.equal(f.state().lease, "c".repeat(32));
  await assert.rejects(f.native.stop("physical-input"), /Invalid/);
  await assert.rejects(f.native.begin("../shell"), /Invalid/);
});
test("vacancy is a read-only fixed command, never kill-by-name", async () => {
  const f = fixture(); await f.native.assertVacant();
  assert.deepEqual(f.calls[0], ["/usr/bin/python3", ["/opt/teslaandroid-waydroid/runtime/waydroid-display/native-stop.py", "--assert-vacant"]]);
});

test("capture restart adopts its existing journal without replacing or erasing ownership", async () => {
  const owner = "d".repeat(32), f = fixture({ version: 1, lease: owner });
  await f.native.adopt(owner);
  for (const role of ["video", "audio"]) await f.native.stop(role);
  assert.deepEqual(f.calls.map(call => call[1].slice(-2)), [[owner, "video"], [owner, "audio"]]);
  assert.equal(f.state().lease, owner);
  await assert.rejects(f.native.adopt("e".repeat(32)), /ownership/);
  await assert.rejects(fixture().native.adopt(owner), /ownership/);
});
