// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test"), assert = require("node:assert/strict");
const { protectedStat, verifyProtectedTree, preflightRuntime } = require("./runtime-preflight");
const stat = (kind, more = {}) => ({ uid: 0, mode: 0o755, nlink: 1, size: 20,
  isDirectory: () => kind === "directory", isFile: () => kind === "file", isSymbolicLink: () => kind === "link", ...more });
test("privileged code requires root-owned non-writable regular artifacts, not hardlinks or devices", () => {
  protectedStat(stat("file"), "file"); protectedStat(stat("directory"), "directory");
  for (const bad of [stat("file", { uid: 1000 }), stat("file", { mode: 0o666 }), stat("file", { nlink: 2 }), stat("device")]) {
    assert.throws(() => protectedStat(bad, "file"), /Unsafe/);
  }
});

function portableFixture() {
  const root = '/opt/droiddeck', reads = [], lookups = [];
  const guest = '/proc/1234/root/data/local/tmp/tesla-waydroid-test-20261003';
  const directories = new Set(['/opt', root, root + '/runtime', root + '/gps-stage', root + '/gps-stage/runtime', root + '/libgbinder', guest, guest + '/bin', guest + '/lib64']);
  const options = { configuration: { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40' },
    io: { lstat: async file => { reads.push(file); return stat(directories.has(file) ? 'directory' : 'file'); }, readdir: async () => [] },
    resolveDependency: (name, directory) => { lookups.push({ name, directory }); return directory + '/node_modules/' + name + '/index.js'; } };
  return { options, reads, lookups, root, guest };
}
test('portable artifact preflight validates configured protected roots and fresh guest artifacts', async () => {
  const f = portableFixture(); await preflightRuntime({ pid: 1234 }, f.options);
  assert.ok(f.reads.includes(f.root + '/runtime/waydroid-display/native-stop.py'));
  assert.ok(f.reads.includes(f.root + '/runtime/portable/worker-entry.js'));
  assert.ok(f.reads.includes(f.root + '/runtime/waydroid-display/gps-helper.js'));
  assert.equal(f.reads.some(file => file.includes('/gps-stage')), false);
  assert.ok(f.reads.includes(f.guest + '/scrcpy-server-v4.0.jar'));
  assert.equal(f.lookups.length, 4); assert.ok(f.lookups.every(item => item.directory.startsWith(f.root + '/runtime/')));
  assert.equal(f.reads.some(file => file.startsWith('/opt/teslaandroid-waydroid')), false);
});
test('portable artifact preflight rejects foreign code, escaped dependencies and invalid config', async () => {
  const f = portableFixture(); f.options.resolveDependency = () => '/tmp/unreviewed.js';
  await assert.rejects(preflightRuntime({ pid: 1234 }, f.options), /Unprotected/);
  const bad = portableFixture(); const original = bad.options.io.lstat;
  bad.options.io.lstat = async file => file.endsWith('/native-stop.py') ? stat('file', { uid: 1200 }) : original(file);
  await assert.rejects(preflightRuntime({ pid: 1234 }, bad.options), /Unsafe/);
  const invalid = portableFixture(); invalid.options.configuration.user = 'root';
  await assert.rejects(preflightRuntime({ pid: 1234 }, invalid.options)); assert.equal(invalid.reads.length, 0);
});
test("protected tree follows only root-owned links within its reviewed boundary", async () => {
  const root = "/opt/teslaandroid-waydroid", target = root + "/runtime/worker.js";
  const io = { lstat: async p => p.endsWith("runtime") ? stat("directory") : p.endsWith("alias") ? stat("link") : stat("file"),
    readdir: async () => ["worker.js", "alias"], realpath: async () => target };
  await verifyProtectedTree(root + "/runtime", { io, root });
  io.realpath = async () => "/home/hilman/worker.js";
  await assert.rejects(verifyProtectedTree(root + "/runtime", { io, root }), /link target/);
  io.realpath = async () => target;
  io.lstat = async p => p.endsWith("runtime") ? stat("directory") : stat("file", { mode: 0o777 });
  await assert.rejects(verifyProtectedTree(root + "/runtime", { io, root }), /Unsafe/);
});
