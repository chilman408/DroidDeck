// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test"), assert = require("node:assert/strict");
const { prepareNativeDirectory } = require("./runtime-native");
const parent = "/run/teslaandroid-waydroid", journal = parent + "/runtime";
function fixture(initial) {
  const entries = new Map(initial || []), mutations = [];
  const io = {
    async mkdir(name, { mode }) {
      if (entries.has(name)) throw Object.assign(new Error("exists"), { code: "EEXIST" });
      entries.set(name, { uid: 0, gid: 0, mode: mode & ~0o077, isDirectory: () => true });
    },
    async lstat(name) { return { ...entries.get(name) }; },
    async chown(name, uid, gid) { mutations.push(["chown", name, uid, gid]); Object.assign(entries.get(name), { uid, gid }); },
    async chmod(name, mode) { mutations.push(["chmod", name, mode]); entries.get(name).mode = mode; },
  };
  return { io, entries, mutations };
}
test("cold boot corrects restrictive umask for shared sockets but keeps native journals private", async () => {
  const f = fixture(); await prepareNativeDirectory(f);
  assert.equal(f.entries.get(parent).mode, 0o750); assert.equal(f.entries.get(parent).gid, 1000);
  assert.equal(f.entries.get(journal).mode, 0o700); assert.equal(f.entries.get(journal).gid, 0);
});
test("owned private cold-boot parent is repaired without touching journal or preferences", async () => {
  const f = fixture([[parent, { uid: 0, gid: 1000, mode: 0o700, isDirectory: () => true }]]);
  await prepareNativeDirectory(f);
  assert.deepEqual(f.mutations, [["chown", parent, 0, 1000], ["chmod", parent, 0o750]]);
});
test("foreign, linked or writable runtime directories fail before chmod/chown", async () => {
  for (const patch of [{ uid: 1000 }, { mode: 0o770 }, { mode: 0o757 }, { isDirectory: () => false }]) {
    const f = fixture([[parent, { uid: 0, gid: 0, mode: 0o700, isDirectory: () => true, ...patch }]]);
    await assert.rejects(prepareNativeDirectory(f)); assert.deepEqual(f.mutations, []);
  }
  const f = fixture([[journal, { uid: 1000, gid: 1000, mode: 0o700, isDirectory: () => true }]]);
  await assert.rejects(prepareNativeDirectory(f));
});
test("permission repair is verified rather than assuming chown/chmod succeeded", async () => {
  const f = fixture(); f.io.chmod = async () => {};
  await assert.rejects(prepareNativeDirectory(f), /Unsafe/);
});
test('portable native state snapshots shared group while journals remain root-private', async () => {
  const f = fixture(), configuration = { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40' };
  const pending = prepareNativeDirectory({ ...f, configuration }); configuration.gid = 0; await pending;
  assert.ok(f.entries.has('/run/droiddeck'));
  assert.equal(f.entries.get('/run/droiddeck').gid, 1201); assert.equal(f.entries.get('/run/droiddeck').mode, 0o750);
  assert.equal(f.entries.get('/run/droiddeck/runtime').gid, 0); assert.equal(f.entries.get('/run/droiddeck/runtime').mode, 0o700);
  assert.equal(f.entries.has(parent), false);
  const invalid = fixture(); await assert.rejects(prepareNativeDirectory({ ...invalid, configuration })); assert.equal(invalid.entries.size, 0);
});
