// SPDX-License-Identifier: GPL-3.0-only
const crypto = require("node:crypto");
const { setTimeout: delay } = require("node:timers/promises");
const { execute } = require("./host-adapter");
const { runtimeCommand, runtimeUnit, ROOT } = require("./runtime-commands");
const { SERVICES } = require("./runtime-supervisor");
const { readContainerIdentity, readAndroidIdentity } = require("./runtime-discovery");
const { normalizeRuntimeConfig, deriveRuntimeLayout } = require('../portable/runtime-config');
function parseUnitState(text) {
  const keys = { LoadState: "load", ActiveState: "active", Description: "description" }, result = {};
  for (const line of text.trim().split(/\r?\n/)) {
    const split = line.indexOf("="), key = line.slice(0, split);
    if (split < 0 || !Object.hasOwn(keys, key) || Object.hasOwn(result, keys[key])) throw new Error("Invalid runtime unit state");
    result[keys[key]] = line.slice(split + 1);
  }
  if (Object.keys(result).length !== 3 || !["loaded", "not-found", "error", "masked"].includes(result.load) ||
      !["active", "inactive", "failed", "activating", "deactivating", "reloading", "maintenance", "refreshing"].includes(result.active)) {
    throw new Error("Invalid runtime unit state");
  }
  return result;
}
function createRuntimeOwnership({ run = execute, readUnit, identity, androidIdentity = readAndroidIdentity, ready, sleep = (ms, options) => delay(ms, undefined, options),
  owner = crypto.randomBytes(16).toString("hex"), now = () => performance.now(), configuration } = {}) {
  if (typeof owner !== "string" || !/^[a-f0-9]{32}$/.test(owner) || typeof ready !== "function") {
    throw new Error("Runtime owner and readiness adapter required");
  }
  const config = configuration === undefined ? undefined : normalizeRuntimeConfig(configuration);
  const layout = config ? deriveRuntimeLayout(config) : null;
  const env = { ...process.env, LD_LIBRARY_PATH: (layout?.root || ROOT) + "/libgbinder" };
  const command = (file, args, options = {}) => run(file, args, { ...options, env });
  readUnit ||= async (unit, options) => parseUnitState((await command("/usr/bin/systemctl",
    ["show", unit, "--property=LoadState,ActiveState,Description"], options)).toString());
  identity ||= async (pid, options) => {
    const args = ["-P", "/var/lib/waydroid/lxc", "-n", "waydroid"];
    const current = (await command("/usr/bin/lxc-info", [...args, "-pH"], options)).toString().trim();
    const state = (await command("/usr/bin/lxc-info", [...args, "-sH"], options)).toString().trim();
    if (current !== String(pid) || state !== "RUNNING") throw new Error("Container identity changed before launch");
    return readContainerIdentity(pid);
  };
  const description = "TeslaAndroid runtime " + owner, attempted = new Set(), rejected = new Set();
  const captureRestart = new Map();
  return {
    get owner() { return owner; },
    async assertVacant(options) {
      for (const name of SERVICES) {
        if ((await readUnit(runtimeUnit(name), options)).load !== "not-found") throw new Error("Runtime preview worker already exists");
      }
    },
    async startService(name, state, { signal } = {}) {
      const unit = runtimeUnit(name), options = { signal };
      if (attempted.has(name) || rejected.has(name)) throw new Error("Runtime worker already attempted");
      let launch;
      try {
        launch = runtimeCommand(name, state, undefined, config);
        signal?.throwIfAborted();
        if (await identity(state.pid, options) !== state.identity ||
            await androidIdentity(state.pid, options) !== state.androidIdentity) throw new Error("Container or Android identity changed before launch");
        if ((await readUnit(unit, options)).load !== "not-found") throw new Error("Runtime preview worker already exists");
        signal?.throwIfAborted();
      } catch (error) {
        // No launch command ran, so cleanup must not stop any pre-existing unit.
        rejected.add(name); throw error;
      }
      attempted.add(name);
      await command(launch.file, ["--collect", "--description=" + description, ...launch.args], options);
      await ready(name, state, options);
      signal?.throwIfAborted();
      if (await identity(state.pid, options) !== state.identity ||
          await androidIdentity(state.pid, options) !== state.androidIdentity) throw new Error("Container or Android identity changed during readiness");
      const actual = await readUnit(unit, options);
      if (actual.description !== description || actual.active !== "active") throw new Error("Runtime worker failed readiness ownership");
    },
    async stopService(name) {
      const unit = runtimeUnit(name);
      captureRestart.delete(name);
      if (rejected.delete(name)) return;
      if (!attempted.has(name)) throw new Error("Runtime worker was not attempted by this supervisor");
      const current = await readUnit(unit);
      if (current.load === "not-found") { attempted.delete(name); return; }
      if (current.description !== description) throw new Error("Runtime worker ownership changed");
      await command("/usr/bin/systemctl", ["stop", unit], { timeoutMs: 65000 });
      for (let i = 0; i < 30; i++) {
        const actual = await readUnit(unit);
        if (actual.load === "not-found") { attempted.delete(name); return; }
        if (actual.description !== description) throw new Error("Runtime worker ownership changed during stop");
        await sleep(100);
      }
      throw new Error("Runtime worker has not unloaded after stop");
    },
    async healthy(state, options) {
      if (await identity(state.pid, options) !== state.identity || await androidIdentity(state.pid, options) !== state.androidIdentity) return false;
      for (const name of SERVICES) {
        if (!attempted.has(name)) return false;
        const actual = await readUnit(runtimeUnit(name), options);
        if (actual.load !== "loaded" || actual.description !== description) return false;
        if (actual.active === 'active') { captureRestart.delete(name); continue; }
        // Last audio viewer closes scrcpy; systemd Restart=always recreates its listener.
        // Only an owned capture's short activation may settle without stopping the gateway/video.
        if (!['audio', 'video'].includes(name) || actual.active !== 'activating') return false;
        const current = now();
        if (!Number.isFinite(current)) return false;
        if (!captureRestart.has(name)) captureRestart.set(name, current);
        const elapsed = current - captureRestart.get(name);
        if (elapsed < 0 || elapsed >= 10000) return false;
      }
      return true;
    },
  };
}
module.exports = { createRuntimeOwnership, parseUnitState };
