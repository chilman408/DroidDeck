// SPDX-License-Identifier: GPL-3.0-only
const fs = require("node:fs/promises");
const { setTimeout: delay } = require("node:timers/promises");
const { execute } = require("./host-adapter");
const { protectedStat } = require("./runtime-preflight");
const { ROOT } = require("./runtime-commands");
const UNIT = "tesla-waydroid-session.service";
const UNIT_FILE = "/home/hilman/.config/systemd/user/" + UNIT;
const TEMPLATE = ROOT + "/runtime/waydroid-display/" + UNIT;
const USER_ARGS = ["-u", "hilman", "--", "/usr/bin/env", "XDG_RUNTIME_DIR=/run/user/1000",
  "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus", "/usr/bin/systemctl", "--user"];
const PROPERTIES = "LoadState,ActiveState,FragmentPath,DropInPaths,Transient";
function parseState(text) {
  if (text.length > 8192) throw new Error("Invalid desktop session state");
  const entries = text.trim().split(/\r?\n/).map(line => {
    const index = line.indexOf("=");
    if (index < 1) throw new Error("Invalid desktop session state");
    return [line.slice(0, index), line.slice(index + 1)];
  });
  if (new Set(entries.map(([key]) => key)).size !== entries.length) throw new Error("Duplicate session property");
  return Object.fromEntries(entries);
}
async function recoverDesktopSession({ run = execute, io = fs, signal,
  sleep = ms => delay(ms, undefined, { signal }) } = {}) {
  signal?.throwIfAborted();
  try {
    const directory = await io.lstat("/run/user/1000");
    if (!directory.isDirectory() || directory.uid !== 1000 || (directory.mode & 0o077)) {
      throw new Error("Unsafe desktop runtime directory");
    }
    for (const name of ["bus", "wayland-0"]) {
      const socket = await io.lstat("/run/user/1000/" + name);
      if (!socket.isSocket() || socket.uid !== 1000) throw new Error("Unsafe desktop socket");
    }
  } catch (error) { if (error.code === "ENOENT") return "skipped"; throw error; }
  const options = { env: { PATH: "/usr/sbin:/usr/bin:/sbin:/bin", LANG: "C.UTF-8" }, shell: false, signal, timeoutMs: 5000 };
  const user = args => run("/usr/sbin/runuser", [...USER_ARGS, ...args], options);
  const inspect = async () => parseState((await user(["show", UNIT, "--property=" + PROPERTIES])).toString());
  const state = await inspect();
  if (state.LoadState === "not-found" || state.ActiveState === "inactive" || state.ActiveState === "failed") return "skipped";
  if (state.LoadState !== "loaded" || !["active", "activating", "deactivating", "reloading"].includes(state.ActiveState) || state.FragmentPath !== UNIT_FILE ||
      state.DropInPaths !== "" || state.Transient !== "no") throw new Error("Foreign or busy desktop session");
  const original = await io.lstat(TEMPLATE);
  protectedStat(original, "file");
  const installed = await io.lstat(UNIT_FILE);
  if (!installed.isFile() || installed.uid !== 1000 || installed.nlink !== 1 || (installed.mode & 0o022) ||
      installed.size > 16384 || original.size > 16384) throw new Error("Unsafe desktop session unit");
  const canonical = buffer => new TextDecoder("utf-8", { fatal: true }).decode(buffer).replace(/\r\n/g, "\n");
  if (canonical(await io.readFile(UNIT_FILE)) !== canonical(await io.readFile(TEMPLATE))) {
    throw new Error("Modified desktop session unit");
  }
  if (state.ActiveState !== "active") return "already-restarting";
  // The container service is Type=simple; its D-Bus name can appear after ExecStartPost begins.
  let ready = false;
  for (let attempt = 0; attempt < 10; attempt++) {
    signal?.throwIfAborted();
    const result = JSON.parse((await run("/usr/bin/busctl", ["--system", "--json=short", "call",
      "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner", "s", "id.waydro.Container"], options)).toString());
    if (result.type !== "b" || !Array.isArray(result.data) || result.data.length !== 1 ||
        typeof result.data[0] !== "boolean") throw new Error("Invalid container bus state");
    if (result.data[0]) { ready = true; break; }
    if (attempt < 9) await sleep(1000);
  }
  if (!ready) throw new Error("Container bus did not become ready");
  const fresh = await inspect();
  if (fresh.ActiveState === "inactive" || fresh.ActiveState === "failed") return "skipped";
  for (const key of PROPERTIES.split(",").filter(key => key !== "ActiveState")) {
    if (fresh[key] !== state[key]) throw new Error("Desktop session changed");
  }
  if (["activating", "deactivating", "reloading"].includes(fresh.ActiveState)) return "already-restarting";
  if (fresh.ActiveState !== "active") throw new Error("Desktop session changed");
  // Nonblocking avoids a dependency cycle with this parent container startup job. try-restart never starts an inactive unit.
  await user(["--no-block", "try-restart", UNIT]);
  return "queued";
}
async function main() {
  if (process.platform !== "linux" || process.getuid?.() !== 0 || process.argv.length !== 2) {
    throw new Error("Fixed desktop recovery requires Linux root and no arguments");
  }
  for (const directory of ["/opt", ROOT, ROOT + "/runtime", ROOT + "/runtime/waydroid-display"]) {
    protectedStat(await fs.lstat(directory), "directory");
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new Error("Desktop recovery deadline")), 30000);
  try { console.log("Desktop session recovery " + await recoverDesktopSession({ signal: controller.signal })); }
  finally { clearTimeout(timer); }
}
if (require.main === module) main().catch(() => { console.error("Desktop session recovery unavailable; inspect session state"); process.exitCode = 1; });
module.exports = { recoverDesktopSession, parseState };
