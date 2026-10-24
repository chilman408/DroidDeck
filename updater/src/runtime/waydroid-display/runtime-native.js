// SPDX-License-Identifier: GPL-3.0-only
const fs = require("node:fs/promises");
const { execute } = require("./host-adapter");
const { createGpsStore } = require("./gps-store");
const { ROOT } = require("./runtime-commands");
const { protectedStat } = require("./runtime-preflight");
const { normalizeRuntimeConfig, deriveRuntimeLayout } = require('../portable/runtime-config');
const DIRECTORY = "/run/teslaandroid-waydroid/runtime";
async function prepareNativeDirectory({ io = fs, configuration } = {}) {
  const config = configuration === undefined ? undefined : normalizeRuntimeConfig(configuration);
  const layout = config ? deriveRuntimeLayout(config) : null;
  const parentPath = layout?.sockets || "/run/teslaandroid-waydroid", directory = parentPath + '/runtime', group = config?.gid || 1000;
  await io.mkdir(parentPath, { mode: 0o750 }).catch(error => { if (error.code !== "EEXIST") throw error; });
  const parent = await io.lstat(parentPath);
  if (!parent.isDirectory() || parent.uid !== 0 || (parent.mode & 0o027)) throw new Error("Unsafe native runtime parent");
  // mkdir's mode is masked by the supervisor's 0077 umask on a fresh boot.
  await io.chown(parentPath, 0, group);
  await io.chmod(parentPath, 0o750);
  const shared = await io.lstat(parentPath);
  if (!shared.isDirectory() || shared.uid !== 0 || shared.gid !== group || (shared.mode & 0o777) !== 0o750) {
    throw new Error("Unsafe native runtime parent permissions");
  }
  await io.mkdir(directory, { mode: 0o700 }).catch(error => { if (error.code !== "EEXIST") throw error; });
  const journal = await io.lstat(directory);
  if (!journal.isDirectory() || journal.uid !== 0 || (journal.mode & 0o777) !== 0o700) {
    throw new Error("Unsafe native journal directory");
  }
}
function createNativeRuntime({ store, run = execute, checkScript, prepareDirectory, configuration } = {}) {
  const config = configuration === undefined ? undefined : normalizeRuntimeConfig(configuration);
  const layout = config ? deriveRuntimeLayout(config) : null, root = layout?.root || ROOT;
  const directory = layout ? layout.sockets + '/runtime' : DIRECTORY;
  store ||= createGpsStore(directory + '/native-lease.json', 'journal');
  prepareDirectory ||= () => prepareNativeDirectory({ configuration: config });
  checkScript ||= async () => {
    for (const directory of ["/opt", root, root + "/runtime", root + "/runtime/waydroid-display"]) {
      protectedStat(await fs.lstat(directory), "directory");
    }
    protectedStat(await fs.lstat(root + "/runtime/waydroid-display/native-stop.py"), "file");
  };
  let lease;
  const valid = value => { if (typeof value !== "string" || !/^[a-f0-9]{32}$/.test(value)) throw new Error("Invalid native runtime lease"); return value; };
  const cleanup = async (owner, role) => {
    await checkScript();
    const result = await run("/usr/bin/python3", [root + "/runtime/waydroid-display/native-stop.py", valid(owner), role], { env: process.env });
    if (result.toString().trim() !== "REMOVED") throw new Error("Native cleanup was not acknowledged");
  };
  const checkLease = async () => {
    if ((await store.load())?.lease !== lease) throw new Error("Native lease ownership changed");
  };
  return {
    async recover() {
      await prepareDirectory(); const original = await store.load();
      if (original) { await cleanup(original.lease, "all"); await store.save(null); }
    },
    async assertVacant(options) {
      await checkScript();
      const result = await run("/usr/bin/python3", [root + "/runtime/waydroid-display/native-stop.py", "--assert-vacant"], { env: process.env, ...options });
      if (result.toString().trim() !== "VACANT") throw new Error("Native preview processes remain");
    },
    async begin(owner) {
      valid(owner); if (await store.load()) throw new Error("Unrecovered native lease");
      await store.save({ version: 1, lease: owner }); lease = owner;
    },
    async adopt(owner) {
      valid(owner);
      if ((await store.load())?.lease !== owner) throw new Error("Native lease ownership changed");
      lease = owner;
    },
    async stop(role) {
      if (!["api", "display", "video", "audio"].includes(role)) throw new Error("Invalid native role");
      if (lease) { await checkLease(); await cleanup(lease, role); }
    },
    async finish() {
      if (!lease) return;
      await checkLease();
      await cleanup(lease, "all"); await store.save(null); lease = null;
    },
  };
}
module.exports = { createNativeRuntime, prepareNativeDirectory };
