// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs/promises');
const { normalizeRuntimeConfig, deriveRuntimeLayout } = require('./runtime-config');
const { loadWorkerConfiguration } = require('./worker-config');
const { assertGpuProcessAccess } = require('./gpu-access');
async function assertDesktopEnvironment({ configuration, environment = process.env, io = fs, signal } = {}) {
  signal?.throwIfAborted();
  const config = normalizeRuntimeConfig(configuration), layout = deriveRuntimeLayout(config);
  if (environment.HOME !== config.home || environment.XDG_RUNTIME_DIR !== layout.runtimeDirectory ||
      environment.DBUS_SESSION_BUS_ADDRESS !== layout.dbusAddress || environment.WAYLAND_DISPLAY !== 'wayland-teslaandroid') {
    throw new Error('Portable desktop environment mismatch');
  }
  const directory = await io.lstat(layout.runtimeDirectory);
  if (!directory.isDirectory() || directory.uid !== config.uid || directory.mode & 0o077) throw new Error('Unsafe user runtime directory');
  const bus = await io.lstat(layout.runtimeDirectory + '/bus');
  if (!bus.isSocket() || bus.uid !== config.uid) throw new Error('Unsafe user bus socket');
  signal?.throwIfAborted();
}
async function main({ configuration, args = process.argv.slice(2), load = loadWorkerConfiguration, checkGpu = assertGpuProcessAccess } = {}) {
  if (process.platform !== 'linux' || !Array.isArray(args) || args.length) throw new Error('Fixed portable desktop preflight required');
  configuration = await load({ role: 'user', read: () => configuration });
  await checkGpu(configuration);
  await assertDesktopEnvironment({ configuration });
}
module.exports = { assertDesktopEnvironment, main };
