// SPDX-License-Identifier: GPL-3.0-only
const { createRuntimeSupervisor } = require("./runtime-supervisor");
const { runRuntimeLoop } = require("./runtime-loop");
const { acquireRuntimeLock } = require("./runtime-lock");
const { createRuntimeDiscovery, readContainerIdentity } = require("./runtime-discovery");
const { createRuntimeOwnership } = require("./runtime-ownership");
const { createRuntimeReadiness } = require("./runtime-readiness");
const { preflightRuntime } = require("./runtime-preflight");
const { createHostAdapter, execute } = require("./host-adapter");
const { createTouchAdapter } = require("./touch-adapter");
const { ROOT } = require("./runtime-commands");
const { createNativeRuntime } = require("./runtime-native");
const { normalizeRuntimeConfig, deriveRuntimeLayout } = require('../portable/runtime-config');
const { validateRuntimeAccount } = require('../portable/runtime-account');
async function runApplication({ signal, acquireLock = acquireRuntimeLock, discovery,
  preflight, ownership, native, pauseTouch, loop = runRuntimeLoop, report = () => {}, assertLanVacant, configuration } = {}) {
  signal?.throwIfAborted();
  const config = configuration === undefined ? undefined : normalizeRuntimeConfig(configuration);
  if (config) await validateRuntimeAccount(config, { signal });
  const layout = config ? deriveRuntimeLayout(config) : null;
  discovery ||= createRuntimeDiscovery({ configuration: config });
  preflight ||= current => preflightRuntime(current, { configuration: config });
  ownership ||= createRuntimeOwnership({ configuration: config, ready: createRuntimeReadiness({ configuration: config }) });
  native ||= createNativeRuntime({ configuration: config });
  assertLanVacant ||= async () => {
    const result = await execute("/usr/sbin/runuser", ["-u", config?.user || "hilman", "--", "/usr/bin/env",
      "XDG_RUNTIME_DIR=" + (layout?.runtimeDirectory || '/run/user/1000'), "DBUS_SESSION_BUS_ADDRESS=" + (layout?.dbusAddress || 'unix:path=/run/user/1000/bus'),
      "/usr/bin/systemctl", "--user", "show", "tesla-waydroid-lan-preview", "--property=LoadState", "--value"], { env: process.env, signal });
    if (result.toString().trim() !== "not-found") throw new Error("Manual LAN relay already exists");
  };
  const lock = await acquireLock();
  let state, success = false;
  try {
    signal?.throwIfAborted();
    await ownership.assertVacant({ signal });
    await native.recover();
    pauseTouch ||= async () => {
      try {
        if (await readContainerIdentity(state.pid) !== state.identity) return;
      } catch (error) { if (["ENOENT", "ESRCH"].includes(error.code)) return; throw error; }
      const hostLibs = (layout?.root || ROOT) + "/libgbinder", host = createHostAdapter({ captureMethod: "logical", hostLibs });
      const touch = createTouchAdapter({ guest: host.guest, calibrate: host.calibrate, hostLibs });
      await touch.pause();
    };
    const supervisor = createRuntimeSupervisor({
      waitReady: async ({ signal: current }) => { current.throwIfAborted(); if (!state) throw new Error("Runtime not discovered"); },
      startService: (name, options) => ownership.startService(name, state, options),
      stopService: async name => {
        await ownership.stopService(name);
        if (["api", "display", "video", "audio"].includes(name)) await native.stop(name);
        if (name === "api") await native.finish();
      }, pauseTouch,
    });
    await loop({ supervisor, signal, report,
      ready: async options => {
        await ownership.assertVacant(options);
        state = await discovery.inspect(options); await assertLanVacant(); await preflight(state);
        await native.assertVacant(options); await native.begin(ownership.owner);
        state.nativeLease = ownership.owner;
      },
      healthy: options => ownership.healthy(state, options),
    });
    success = true;
  } finally {
    try { if (success) await native.finish(); }
    finally { await new Promise(resolve => lock.close(resolve)); }
  }
}
async function main() {
  if (process.platform !== "linux" || process.getuid?.() !== 0 || process.argv.length !== 2) {
    throw new Error("Fixed Waydroid supervisor requires Linux root and no arguments");
  }
  const controller = new AbortController();
  for (const name of ["SIGTERM", "SIGINT"]) process.once(name, () => controller.abort(new Error("Runtime stopped")));
  let lastPhase;
  await runApplication({ signal: controller.signal, report: phase => {
    if (phase !== lastPhase) { lastPhase = phase; console.log("Runtime " + phase); }
  } });
}
if (require.main === module) main().catch(() => { console.error("Runtime supervisor stopped; inspect checkpoint and unit status"); process.exitCode = 1; });
module.exports = { runApplication };
