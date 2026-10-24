// SPDX-License-Identifier: GPL-3.0-only
const fs = require("node:fs/promises");
const crypto = require("node:crypto");
const { spawn } = require("node:child_process");
const { createHostAdapter, parseBootClasspaths, execute } = require("../waydroid-display/host-adapter");
const { SERVER_SHA256 } = require("./video-launcher");
const { captureEnvironment, prepareCapture } = require("./capture-lease");
const { captureLayout } = require('./capture-layout');
const { loadWorkerConfiguration } = require('../portable/worker-config');
const JAR = "/data/local/tmp/tesla-waydroid-test-20261003/scrcpy-server-v4.0.jar";
const SOCKET = "\0scrcpy_54410002";
function serverArgs(boot, owner) {
  const verified = parseBootClasspaths(boot.map(v => `export ${v.replace("=", " ")}`).join("\n"));
  return ["-P", "/var/lib/waydroid/lxc", "-n", "waydroid", "--clear-env",
    "--set-var", "PATH=/system/bin:/apex/com.android.runtime/bin", "--set-var", "ANDROID_ROOT=/system",
    "--set-var", "ANDROID_DATA=/data", "--set-var", "ANDROID_STORAGE=/storage", "--", "/system/bin/env",
    `CLASSPATH=${JAR}`, "ANDROID_ART_ROOT=/apex/com.android.art", "ANDROID_I18N_ROOT=/apex/com.android.i18n",
    "ANDROID_TZDATA_ROOT=/apex/com.android.tzdata", ...verified, ...captureEnvironment(owner, "audio"), "/system/bin/app_process", "/system/bin",
    "com.genymobile.scrcpy.Server", "4.0", "scid=54410002", "tunnel_forward=true", "video=false", "audio=true",
    "audio_source=output", "audio_codec=raw", "control=false", "cleanup=false", "power_on=false",
    "send_device_meta=false", "send_dummy_byte=false", "send_frame_meta=false", "send_stream_meta=true", "log_level=info"];
}
async function main({ configuration, args = process.argv.slice(2) } = {}) {
  if (process.getuid?.() !== 0 || !Array.isArray(args) || args.length) throw new Error("Fixed audio launcher requires root, no arguments");
  configuration = await loadWorkerConfiguration({ role: 'privileged', read: () => configuration });
  const layout = captureLayout(configuration);
  const owner = await prepareCapture("audio", { configuration });
  const pid = (await execute("/usr/bin/lxc-info", ["-P", "/var/lib/waydroid/lxc", "-n", "waydroid", "-pH"], { env: process.env })).toString().trim();
  if (!/^\d+$/.test(pid) || Number(pid) < 2) throw new Error("Invalid container PID");
  const jar = `/proc/${pid}/root${JAR}`, stat = await fs.lstat(jar);
  if (!stat.isFile() || stat.uid !== 0 || (stat.mode & 0o022) || stat.size > 1024 * 1024 ||
      crypto.createHash("sha256").update(await fs.readFile(jar)).digest("hex") !== SERVER_SHA256) throw new Error("Unsafe audio server artifact");
  const adapter = createHostAdapter({ captureMethod: "logical", hostLibs: layout.hostLibraries });
  const boot = parseBootClasspaths((await adapter.guest(["/system/bin/toybox", "cat", "/data/system/environ/classpath"])).toString());
  const child = spawn("/usr/bin/lxc-attach", serverArgs(boot, owner), { stdio: "inherit", shell: false, env: process.env });
  for (const signal of ["SIGTERM", "SIGINT"]) process.once(signal, () => child.kill(signal));
  child.once("error", error => { console.error(error.message); process.exitCode = 1; });
  child.once("exit", (code, signal) => { process.exitCode = signal ? 1 : code; });
}
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
module.exports = { serverArgs, SOCKET, main };
