// SPDX-License-Identifier: GPL-3.0-only
const fs = require("node:fs/promises"), net = require("node:net");
const { setTimeout: delay } = require("node:timers/promises");
const { execute } = require("./host-adapter");
const { assertSessionVacant } = require("./session-preflight");
const { parseState } = require("./session-recovery");
const { normalizeRuntimeConfig, deriveRuntimeLayout } = require('../portable/runtime-config');
const { loadWorkerConfiguration } = require('../portable/worker-config');
const { assertDesktopEnvironment } = require('../portable/desktop-preflight');
function connectSocket(filename, signal) {
  return new Promise((resolve, reject) => {
    signal?.throwIfAborted();
    const socket = net.createConnection({ path: filename });
    const finish = error => {
      clearTimeout(timer); signal?.removeEventListener("abort", abort); socket.destroy();
      if (error) reject(error); else resolve();
    };
    const abort = () => finish(signal.reason || new Error("Compositor readiness cancelled"));
    const timer = setTimeout(() => finish(new Error("Compositor connection timed out")), 1000);
    signal?.addEventListener("abort", abort, { once: true });
    socket.once("connect", () => finish()); socket.once("error", finish);
    if (signal?.aborted) abort();
  });
}
async function assertHeadlessSessionReady({ io = fs, run = execute, signal,
  connect = connectSocket, sleep = ms => delay(ms, undefined, { signal }), configuration, environment = process.env } = {}) {
  signal?.throwIfAborted();
  const config = configuration === undefined ? undefined : normalizeRuntimeConfig(configuration);
  const layout = config && deriveRuntimeLayout(config), uid = config?.uid ?? 1000;
  const runtimeDirectory = layout?.runtimeDirectory ?? '/run/user/1000';
  const compositorSocket = runtimeDirectory + '/wayland-teslaandroid';
  if (config) await assertDesktopEnvironment({ configuration: config, environment, io, signal });
  else {
    const directory = await io.lstat(runtimeDirectory);
    if (!directory.isDirectory() || directory.uid !== uid || (directory.mode & 0o077)) {
      throw new Error("Unsafe user runtime directory");
    }
    const bus = await io.lstat(runtimeDirectory + '/bus');
    if (!bus.isSocket() || bus.uid !== uid) throw new Error("Unsafe user bus socket");
  }
  let ready = false;
  for (let attempt = 0; attempt < 40; attempt++) {
    signal?.throwIfAborted();
    try {
      const socket = await io.lstat(compositorSocket);
      if (!socket.isSocket() || socket.uid !== uid) throw new Error("Unsafe compositor socket");
      ready = true; break;
    } catch (error) { if (error.code !== "ENOENT") throw error; }
    if (attempt < 39) await sleep(250);
  }
  if (!ready) throw new Error("Headless compositor did not become ready");
  await connect(compositorSocket, signal);
  const options = { env: environment, signal, timeoutMs: 5000 };
  const state = parseState((await run("/usr/bin/systemctl", ["--user", "show", "tesla-waydroid-session.service",
    "--property=LoadState,ActiveState,UnitFileState"], options)).toString());
  if (!(state.LoadState === "not-found" && state.ActiveState === "inactive") &&
      !(state.LoadState === "loaded" && state.ActiveState === "inactive" && state.UnitFileState === "disabled")) {
    throw new Error("Desktop session must be inactive and disabled before headless startup");
  }
  if (config) {
    let ready = false;
    for (let attempt = 0; attempt < 20; attempt++) {
      signal?.throwIfAborted();
      const response = JSON.parse((await run('/usr/bin/busctl', ['--system', '--json=short', 'call',
        'org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'NameHasOwner', 's', 'id.waydro.Container'], options)).toString());
      if (response.type !== 'b' || !Array.isArray(response.data) || response.data.length !== 1 ||
          typeof response.data[0] !== 'boolean') throw new Error('Invalid container bus readiness');
      if (response.data[0]) { ready = true; break; }
      if (attempt < 19) await sleep(250);
    }
    if (!ready) throw new Error('Container bus did not become ready');
  }
  await assertSessionVacant({ run: (file, args) => run(file, args, options) });
  signal?.throwIfAborted();
}
async function main({ configuration, args = process.argv.slice(2) } = {}) {
  if (process.platform !== "linux" || !Array.isArray(args) || args.length) throw new Error('Fixed headless preflight required');
  configuration = await loadWorkerConfiguration({ role: 'user', read: () => configuration });
  if (!configuration && (
      process.env.XDG_RUNTIME_DIR !== "/run/user/1000" || process.env.WAYLAND_DISPLAY !== "wayland-teslaandroid" ||
      process.env.DBUS_SESSION_BUS_ADDRESS !== "unix:path=/run/user/1000/bus")) {
    throw new Error("Fixed headless preflight requires its unprivileged service environment");
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new Error("Headless readiness deadline")), 20000);
  try { await assertHeadlessSessionReady({ signal: controller.signal, configuration }); }
  finally { clearTimeout(timer); }
}
if (require.main === module) main().catch(() => { console.error("Headless session startup unavailable; inspect owned services"); process.exitCode = 1; });
module.exports = { assertHeadlessSessionReady, connectSocket, main };
