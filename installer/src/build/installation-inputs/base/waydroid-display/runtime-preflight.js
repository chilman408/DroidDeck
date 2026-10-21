// SPDX-License-Identifier: GPL-3.0-only
const fs = require("node:fs/promises"), path = require("node:path");
const { ROOT } = require("./runtime-commands");
const { normalizeRuntimeConfig, deriveRuntimeLayout } = require('../portable/runtime-config');
function protectedStat(stat, kind) {
  if (stat.uid !== 0 || (stat.mode & 0o022) || !(kind === "directory" ? stat.isDirectory() : stat.isFile()) ||
      (kind === "file" && stat.nlink !== 1)) throw new Error("Unsafe privileged runtime artifact");
}
async function verifyProtectedTree(directory, { io = fs, root = ROOT } = {}) {
  const visited = new Set(); let entries = 0, bytes = 0;
  const within = filename => filename === root || filename.startsWith(root + "/");
  async function check(filename) {
    if (!within(filename) || ++entries > 10000) throw new Error("Unsafe runtime tree boundary");
    const stat = await io.lstat(filename);
    if (stat.isSymbolicLink()) {
      if (stat.uid !== 0) throw new Error("Unsafe runtime link owner");
      const target = await io.realpath(filename);
      if (!within(target)) throw new Error("Unsafe runtime link target");
      for (let parent = path.posix.dirname(target); within(parent) && parent !== root; parent = path.posix.dirname(parent)) {
        protectedStat(await io.lstat(parent), "directory");
      }
      if (!visited.has(target)) await check(target);
      return;
    }
    if (visited.has(filename)) return; visited.add(filename);
    if (stat.isDirectory()) {
      protectedStat(stat, "directory");
      for (const entry of await io.readdir(filename)) await check(path.posix.join(filename, entry));
    } else {
      protectedStat(stat, "file"); bytes += stat.size;
      if (bytes > 100 * 1024 * 1024) throw new Error("Runtime tree size exceeded");
    }
  }
  await check(directory);
}
async function preflightRuntime(state, { io = fs, configuration,
  resolveDependency = (name, directory) => require.resolve(name, { paths: [directory] }) } = {}) {
  const config = configuration === undefined ? undefined : normalizeRuntimeConfig(configuration);
  const root = config ? deriveRuntimeLayout(config).root : ROOT;
  for (const directory of ["/opt", root, ...(config ? [] : [root + "/gps-stage"])]) protectedStat(await io.lstat(directory), "directory");
  for (const directory of [root + "/runtime", ...(config ? [] : [root + "/gps-stage/runtime"]), root + "/libgbinder"]) {
    await verifyProtectedTree(directory, { io, root });
  }
  const code = ["runtime/waydroid-display/helper.js", "runtime/waydroid-display/runtime-main.js",
    "runtime/waydroid-display/native-stop.py",
    "runtime/preview-gateway/server.js", "runtime/preview-gateway/video-launcher.js", "runtime/preview-gateway/audio-launcher.js",
    "runtime/preview-gateway/h264-relay.js", "runtime/preview-gateway/audio-relay.js", "runtime/preview-gateway/runtime-probe.js",
    ...(config ? ["runtime/portable/worker-entry.js", "runtime/preview-gateway/compositor-video-launcher.js", "runtime/waydroid-display/gps-helper.js"] : ["gps-stage/runtime/waydroid-display/gps-helper.js"])];
  for (const file of code) protectedStat(await io.lstat(root + "/" + file), "file");
  if (!Number.isSafeInteger(state?.pid) || state.pid < 2) throw new Error("Invalid guest artifact identity");
  const base = `/proc/${state.pid}/root/data/local/tmp/tesla-waydroid-test-20261003`;
  for (const directory of [base, base + "/bin", base + "/lib64"]) protectedStat(await io.lstat(directory), "directory");
  for (const file of ["bin/tesla-android-x86-api", "bin/tesla-android-x86-display-latest", "bin/tesla-android-x86-touch",
    "logical-screencap", "logical-capture.jar", "scrcpy-server-v4.0.jar", "location-bridge.jar"]) {
    protectedStat(await io.lstat(base + "/" + file), "file");
  }
  // A dependency resolved outside the reviewed tree must not execute with root privileges.
  for (const [name, directory] of [["pngjs", "waydroid-display"], ["ws", "preview-gateway"],
    ["http-proxy", "preview-gateway"], ["serve-handler", "preview-gateway"]]) {
    const filename = resolveDependency(name, root + "/runtime/" + directory);
    if (typeof filename !== 'string' || !filename.startsWith(root + "/runtime/")) throw new Error("Unprotected runtime dependency");
  }
}
module.exports = { protectedStat, verifyProtectedTree, preflightRuntime };
