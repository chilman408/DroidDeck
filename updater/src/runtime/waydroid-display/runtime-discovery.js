// SPDX-License-Identifier: GPL-3.0-only
const fs = require("node:fs/promises");
const { createHostAdapter, execute, parseBootClasspaths, validateSize } = require("./host-adapter");
const { ROOT } = require("./runtime-commands");
const { normalizeRuntimeConfig, deriveRuntimeLayout } = require('../portable/runtime-config');
const LXC = ["-P", "/var/lib/waydroid/lxc", "-n", "waydroid"];
const ADDRESS = /^192\.168\.240\.(?:[2-9]|[1-9]\d|1\d\d|2[0-4]\d|25[0-4])$/;
function parseContainerAddress(text) {
  const links = JSON.parse(text);
  if (!Array.isArray(links)) throw new Error("Invalid guest address response");
  const addresses = links.filter(link => link?.ifname === "eth0").flatMap(link => {
    if (!Array.isArray(link.addr_info)) throw new Error("Invalid guest address response");
    return link.addr_info.filter(addr => addr?.family === "inet" && addr.scope === "global").map(addr => addr.local);
  });
  if (addresses.length !== 1 || typeof addresses[0] !== "string" || !ADDRESS.test(addresses[0])) {
    throw new Error("Guest address missing, invalid or ambiguous");
  }
  return addresses[0];
}
function parseProcessStart(text, pid) {
  const end = text.lastIndexOf(") ");
  const fields = text.slice(end + 2).trim().split(/\s+/);
  if (!text.startsWith(`${pid} (`) || end < 0 || !/^\d+$/.test(fields[19] || "")) {
    throw new Error("Invalid container process identity");
  }
  return fields[19];
}
async function readContainerIdentity(pid) {
  const start = parseProcessStart(await fs.readFile(`/proc/${pid}/stat`, "utf8"), pid);
  const namespace = await fs.stat(`/proc/${pid}/ns/net`, { bigint: true });
  return `${namespace.dev}:${namespace.ino}:${start}`;
}
async function readAndroidIdentity(pid, options = {}, { run = execute, io = fs } = {}) {
  if (!Number.isSafeInteger(pid) || pid < 2) throw new Error("Invalid container PID");
  const identities = [];
  for (const name of ["system_server", "surfaceflinger"]) {
    options.signal?.throwIfAborted();
    const value = (await run("/usr/bin/nsenter", ["-t", String(pid), "-m", "-p", "-r", "--",
      "/system/bin/toybox", "pidof", name], options)).toString().trim();
    const process = Number(value);
    if (!/^\d+$/.test(value) || !Number.isSafeInteger(process) || process < 2) throw new Error("Android service identity unavailable");
    const stat = await io.readFile(`/proc/${pid}/root/proc/${process}/stat`, { encoding: "utf8", signal: options.signal });
    identities.push(`${process}:${parseProcessStart(stat, process)}`);
  }
  options.signal?.throwIfAborted();
  return identities.join("/");
}
function createRuntimeDiscovery({ run = execute, host, readIdentity = readContainerIdentity, readAndroid = readAndroidIdentity, registered, configuration } = {}) {
  const config = configuration === undefined ? null : normalizeRuntimeConfig(configuration);
  const layout = config ? deriveRuntimeLayout(config) : null;
  const env = { ...process.env, LD_LIBRARY_PATH: (layout?.root || ROOT) + "/libgbinder" };
  registered ||= async options => {
    const response = JSON.parse((await run("/usr/sbin/runuser", ["-u", config?.user || "hilman", "--", "/usr/bin/env",
      "XDG_RUNTIME_DIR=" + (layout?.runtimeDirectory || '/run/user/1000'), "DBUS_SESSION_BUS_ADDRESS=" + (layout?.dbusAddress || 'unix:path=/run/user/1000/bus'),
      "/usr/bin/busctl", "--user", "--json=short", "call", "org.freedesktop.DBus", "/org/freedesktop/DBus",
      "org.freedesktop.DBus", "NameHasOwner", "s", "id.waydro.Session"], options)).toString());
    return response.type === "b" && Array.isArray(response.data) && response.data.length === 1 && response.data[0] === true;
  };
  // Readiness must not thaw a container during an identity race.
  host ||= createHostAdapter({ hostLibs: env.LD_LIBRARY_PATH, captureMethod: "logical", run: (file, args, options) => {
    if (file === "/usr/bin/lxc-unfreeze") throw new Error("Waydroid container is not running");
    return run(file, args, options);
  } });
  return { async inspect({ signal } = {}) {
    const options = { env, signal };
    const command = async (file, args) => {
      signal?.throwIfAborted(); return (await run(file, args, options)).toString().trim();
    };
    const running = async () => {
      if (await command("/usr/bin/lxc-info", [...LXC, "-sH"]) !== "RUNNING") throw new Error("Waydroid container is not running");
    };
    const pidValue = async () => {
      const text = await command("/usr/bin/lxc-info", [...LXC, "-pH"]);
      const pid = Number(text);
      if (!/^\d+$/.test(text) || !Number.isSafeInteger(pid) || pid < 2) throw new Error("Invalid container PID");
      return pid;
    };
    const guest = async args => {
      signal?.throwIfAborted(); return (await host.guest(args, { signal })).toString().trim();
    };
    signal?.throwIfAborted();
    if (!await registered(options)) throw new Error("Waydroid session is not registered");
    await running();
    const pid = await pidValue(), identity = await readIdentity(pid);
    const androidIdentity = await readAndroid(pid, options);
    signal?.throwIfAborted();
    if (await guest(["/system/bin/getprop", "sys.boot_completed"]) !== "1") throw new Error("Android session is not booted");
    const pidLimit = await guest(["/system/bin/toybox", "cat", "/proc/sys/kernel/pid_max"]);
    if (!/^\d+$/.test(pidLimit) || Number(pidLimit) < 1024 || Number(pidLimit) > 65535) {
      throw new Error("Guest PID limit is incompatible with 32-bit Android; prepare the startup compatibility hook");
    }
    const geometry = await host.inspect({ signal });
    signal?.throwIfAborted(); validateSize(geometry);
    const boot = parseBootClasspaths(await guest(["/system/bin/toybox", "cat", "/data/system/environ/classpath"]));
    const containerAddress = parseContainerAddress(await guest(["/system/bin/ip", "-j", "-4", "addr", "show", "dev", "eth0"]));
    await running();
    if (await pidValue() !== pid || await readIdentity(pid) !== identity) throw new Error("Container identity changed during discovery");
    if (await readAndroid(pid, options) !== androidIdentity) throw new Error("Android identity changed during discovery");
    signal?.throwIfAborted();
    return { pid, identity, androidIdentity, width: geometry.width, height: geometry.height, density: geometry.density, containerAddress, boot };
  } };
}
module.exports = { createRuntimeDiscovery, parseContainerAddress, parseProcessStart, readContainerIdentity, readAndroidIdentity };
