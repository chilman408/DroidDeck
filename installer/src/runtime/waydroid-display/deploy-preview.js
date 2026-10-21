// SPDX-License-Identifier: GPL-3.0-only
// Disposable .90 preview only; no persistent VM startup or GPU changes.
const fs = require("node:fs/promises");
const path = require("node:path");
const { setTimeout: delay } = require("node:timers/promises");
const { execute, createHostAdapter, parseBootClasspaths, GUEST_LIBS, HOST_LIBS } = require("./host-adapter");
const { createTouchAdapter } = require("./touch-adapter");
const { verifyProcess, stopTransient } = require("./session-probe");
const { createHelperClient } = require("../preview-gateway/display-helper-client");
const HOME = "/home/hilman/teslaandroid-waydroid";
const STAGE = HOME + "/dynamic-stage-20261004";
const ROOT = "/opt/teslaandroid-waydroid";
const LIBS = ROOT + "/libgbinder";
const GUEST_BASE = "/data/local/tmp/tesla-waydroid-test-20261003";
const SOCKET = "/run/teslaandroid-waydroid/display.sock";
const adapter = createHostAdapter({ captureMethod: "logical" });
const run = (file, args) => execute(file, args, { env: process.env });
const stop = unit => stopTransient(() => run("/usr/bin/systemctl", ["stop", unit]));
async function protect(directory) {
  const stat = await fs.lstat(directory);
  if (!stat.isDirectory() || stat.uid !== 0) throw new Error("Install directory is not root-owned");
  await fs.chmod(directory, 0o755);
  for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
    const filename = path.join(directory, entry.name);
    if (entry.isDirectory()) await protect(filename);
    else if (entry.isFile()) { await fs.chown(filename, 0, 0); await fs.chmod(filename, 0o644); }
    else throw new Error("Non-regular install artifact");
  }
}
async function currentPID() {
  const pid = (await run("/usr/bin/lxc-info", ["-P", "/var/lib/waydroid/lxc", "-n", "waydroid", "-pH"])).toString().trim();
  if (!/^\d+$/.test(pid) || Number(pid) < 2) throw new Error("Invalid container PID");
  return pid;
}
async function stopDisplay() {
  let pids;
  try { pids = (await adapter.guest(["/system/bin/toybox", "pidof", "tesla-android-x86-display-latest"])).toString().trim().split(/\s+/); }
  catch (error) { if (error.code !== 1) throw error; pids = []; }
  for (const pid of pids) {
    await verifyProcess("display", pid, args => adapter.guest(args));
    await adapter.guest(["/system/bin/toybox", "kill", "-TERM", pid]);
  }
  await stop("tesla-waydroid-display-preview");
}
async function startDisplay(logical) {
  const boot = logical ? parseBootClasspaths((await adapter.guest(
    ["/system/bin/toybox", "cat", "/data/system/environ/classpath"])).toString()) : [];
  await run("/usr/bin/systemd-run", ["--unit=tesla-waydroid-display-preview", `--setenv=LD_LIBRARY_PATH=${logical ? LIBS : HOST_LIBS}`,
    "/usr/bin/waydroid", "shell", "--", "/system/bin/env", `LD_LIBRARY_PATH=${GUEST_LIBS}`, ...boot,
    GUEST_BASE + "/bin/tesla-android-x86-display-latest", ...(logical ? [GUEST_BASE + "/logical-screencap"] : [])]);
}
async function startGateway(dynamic, video = false, audio = false, gps = false) {
  await run("/usr/bin/systemd-run", ["--unit=tesla-waydroid-gateway-preview", "/usr/bin/nsenter", "-t", await currentPID(), "-n",
    "/usr/sbin/runuser", "-u", "hilman", "--", "/usr/bin/env", "GATEWAY_ORIGIN=https://192.168.2.90:8443",
    "GATEWAY_BIND=192.168.240.112", "GATEWAY_ANDROID_LOOPBACK=1",
    "GATEWAY_TLS_KEY_FILE=/home/hilman/.config/teslaandroid-waydroid/key.pem",
    "GATEWAY_TLS_CERT_FILE=/home/hilman/.config/teslaandroid-waydroid/cert.pem",
    ...(dynamic ? ["GATEWAY_DISPLAY_SOCKET=" + SOCKET] : []),
    ...(video ? ["GATEWAY_H264_STREAM=1"] : []),
    ...(audio ? ["GATEWAY_AUDIO_STREAM=1"] : []),
    ...(gps ? ["GATEWAY_GPS_SOCKET=/run/teslaandroid-waydroid/gps.sock"] : []), "/usr/bin/node",
    HOME + "/runtime/preview-gateway/server.js", HOME + "/frontend", "8443"]);
}
async function prepare() {
  const state = await adapter.inspect();
  const backup = "/var/lib/teslaandroid-waydroid/backups/dynamic-" + Date.now();
  await fs.mkdir(backup, { recursive: true, mode: 0o700 }); await fs.chmod(backup, 0o700);
  await run("/usr/bin/tar", ["-cf", backup + "/preview.tar", "-C", HOME, "runtime/preview-gateway", "frontend"]);
  await fs.chmod(backup + "/preview.tar", 0o600);
  await fs.writeFile(backup + "/original.json", JSON.stringify(state) + "\n", { mode: 0o600, flag: "wx" });
  for (const unit of ["api", "display", "touch", "gateway"]) {
    await fs.writeFile(backup + "/" + unit + ".unit", await run("/usr/bin/systemctl", ["cat", "tesla-waydroid-" + unit + "-preview"]), { mode: 0o600 });
  }
  await fs.writeFile(backup + "/login.sha256", await run("/usr/bin/sha256sum", ["/home/hilman/.config/teslaandroid-waydroid/browser-access.json"]), { mode: 0o600 });
  await stop("tesla-waydroid-gateway-preview");
  let copied = false;
  try {
    await fs.mkdir(ROOT, { mode: 0o755 }).catch(error => { if (error.code !== "EEXIST") throw error; });
    const stat = await fs.lstat(ROOT); if (!stat.isDirectory() || stat.uid !== 0 || (stat.mode & 0o022)) throw new Error("Unsafe privileged install root");
    await fs.mkdir(ROOT + "/runtime", { mode: 0o755 }).catch(error => { if (error.code !== "EEXIST") throw error; });
    await fs.cp(STAGE + "/runtime/waydroid-display", ROOT + "/runtime/waydroid-display", { recursive: true, dereference: true });
    // The helper shares validation with the client, not gateway login material.
    await fs.mkdir(ROOT + "/runtime/preview-gateway", { recursive: true });
    await fs.copyFile(STAGE + "/runtime/preview-gateway/display-helper-client.js", ROOT + "/runtime/preview-gateway/display-helper-client.js");
    await protect(ROOT + "/runtime");
    await fs.mkdir(LIBS, { mode: 0o755 }).catch(error => { if (error.code !== "EEXIST") throw error; });
    await fs.copyFile(HOST_LIBS + "/libgbinder.so.1.1.45", LIBS + "/libgbinder.so.1.1.45");
    for (const name of ["libgbinder.so", "libgbinder.so.1"]) {
      try { await fs.symlink("libgbinder.so.1.1.45", LIBS + "/" + name); }
      catch (error) { if (error.code !== "EEXIST" || await fs.readlink(LIBS + "/" + name) !== "libgbinder.so.1.1.45") throw error; }
    }
    await fs.chown(LIBS + "/libgbinder.so.1.1.45", 0, 0);
    await fs.chmod(LIBS + "/libgbinder.so.1.1.45", 0o644);
    const guestRoot = "/proc/" + await currentPID() + "/root";
    await run("/usr/bin/install", ["-o", "root", "-g", "root", "-m", "0700",
      ROOT + "/runtime/waydroid-display/logical-screencap.sh", guestRoot + GUEST_BASE + "/logical-screencap"]);
    await run("/usr/bin/install", ["-o", "root", "-g", "root", "-m", "0600",
      HOME + "/logical-capture-calibration.jar", guestRoot + GUEST_BASE + "/logical-capture.jar"]);
    copied = true;
    await fs.cp(STAGE + "/runtime/preview-gateway", HOME + "/runtime/preview-gateway", { recursive: true });
    await run("/usr/bin/tar", ["-xzf", HOME + "/frontend-dynamic-preview.tar.gz", "-C", HOME + "/frontend"]);
    await run("/usr/bin/systemd-run", ["--unit=tesla-waydroid-display-helper-preview", "--property=TimeoutStopSec=35s",
      "/usr/bin/node", ROOT + "/runtime/waydroid-display/helper.js"]);
    const helper = createHelperClient(SOCKET); let ready;
    for (let i = 0; i < 50; i++) {
      try { ready = await helper.inspect(); break; } catch { await delay(250); }
    }
    if (!ready || ready.recoveryRequired) throw new Error("Privileged display helper did not become ready");
    await stopDisplay(); await startDisplay(true);
    await fs.writeFile(backup + "/prepared", "Logical capture and helper ready; production gateway remains stopped for verification\n", { mode: 0o600 });
    console.log(JSON.stringify({ prepared: true, backup, state: ready }));
  } catch (error) {
    await stop("tesla-waydroid-display-helper-preview");
    const touch = createTouchAdapter({ guest: adapter.guest, calibrate: adapter.calibrate });
    await touch.pause(); await adapter.restore(state); await touch.remap(state); await touch.verifyTouch(state);
    await stopDisplay(); await startDisplay(false);
    if (copied) await run("/usr/bin/tar", ["-xf", backup + "/preview.tar", "-C", HOME]);
    await startGateway(false);
    throw error;
  }
}
async function main() {
  if (process.getuid?.() !== 0) throw new Error("Preview deployment requires root on the disposable .90 VM");
  if (process.argv[2] === "--prepare") return prepare();
  if (process.argv[2] === "--enable") return startGateway(true);
  if (process.argv[2] === "--enable-video") return startGateway(true, true);
  if (process.argv[2] === "--enable-audio") return startGateway(true, true, true);
  if (process.argv[2] === "--enable-gps") return startGateway(true, true, true, true);
  throw new Error("Use --prepare, verify the isolated preview, then --enable");
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { startGateway, startDisplay, stopDisplay };
