// SPDX-License-Identifier: GPL-3.0-only
const fs = require("node:fs/promises");
const crypto = require("node:crypto");
const { spawn } = require("node:child_process");
const { createHostAdapter, parseBootClasspaths, execute } = require("../waydroid-display/host-adapter");
const { captureEnvironment, prepareCapture } = require("./capture-lease");
const { readVideoSettings, validateProfile } = require("./video-settings");
const { captureLayout } = require('./capture-layout');
const { loadWorkerConfiguration } = require('../portable/worker-config');
const { normalizeRuntimeConfig } = require('../portable/runtime-config');
const { createVideoCaptureController } = require("./video-capture-controller");
const SERVER_SHA256 = "84924bd564a1eb6089c872c7521f968058977f91f5ff02514a8c74aff3210f3a";
const JAR = "/data/local/tmp/tesla-waydroid-test-20261003/scrcpy-server-v4.0.jar";
async function settingsUserUid(run = execute, configuration) {
  if (configuration !== undefined) return normalizeRuntimeConfig(configuration).uid;
  const uid = Number((await run("/usr/bin/id", ["-u", "hilman"], { env: process.env })).toString().trim());
  if (!Number.isSafeInteger(uid) || uid < 1) throw new Error("Invalid video settings user");
  return uid;
}
function serverArgs(boot, owner, profile = { version: 1, revision: 0, bitRate: 4000000, maxFps: 30 }) {
  profile = validateProfile(profile);
  const verified = parseBootClasspaths(boot.map(v => `export ${v.replace("=", " ")}`).join("\n"));
  return ["-P", "/var/lib/waydroid/lxc", "-n", "waydroid", "--clear-env",
    "--set-var", "PATH=/system/bin:/apex/com.android.runtime/bin", "--set-var", "ANDROID_ROOT=/system",
    "--set-var", "ANDROID_DATA=/data", "--set-var", "ANDROID_STORAGE=/storage", "--", "/system/bin/env",
    `CLASSPATH=${JAR}`, "ANDROID_ART_ROOT=/apex/com.android.art", "ANDROID_I18N_ROOT=/apex/com.android.i18n",
    "ANDROID_TZDATA_ROOT=/apex/com.android.tzdata", ...verified, ...captureEnvironment(owner, "video"), "/system/bin/app_process", "/system/bin",
    "com.genymobile.scrcpy.Server", "4.0", "scid=54410001", "tunnel_forward=true", "audio=false", "control=false",
    "cleanup=false", "send_device_meta=false", "send_dummy_byte=false", "video_codec=h264",
    "video_encoder=c2.android.avc.encoder", `max_fps=${profile.maxFps}`, `video_bit_rate=${profile.bitRate}`, "max_size=0",
    "video_codec_options=i-frame-interval=1", "log_level=info"];
}
async function main({ configuration, args = process.argv.slice(2) } = {}) {
  if (process.getuid?.() !== 0 || !Array.isArray(args) || args.length) throw new Error("Fixed video launcher requires root, no arguments");
  configuration = await loadWorkerConfiguration({ role: 'privileged', read: () => configuration });
  const layout = captureLayout(configuration);
  const dynamic = process.env.TESLA_WAYDROID_VIDEO_SETTINGS === "1";
  if (process.env.TESLA_WAYDROID_VIDEO_SETTINGS !== undefined && !dynamic) throw new Error("Invalid video settings flag");
  const owner = await prepareCapture("video", { configuration });
  if (dynamic && !owner) throw new Error("Video settings require a supervised native lease");
  const pid = (await execute("/usr/bin/lxc-info", ["-P", "/var/lib/waydroid/lxc", "-n", "waydroid", "-pH"], { env: process.env })).toString().trim();
  if (!/^\d+$/.test(pid) || Number(pid) < 2) throw new Error("Invalid container PID");
  const jar = `/proc/${pid}/root${JAR}`, stat = await fs.lstat(jar);
  if (!stat.isFile() || stat.uid !== 0 || (stat.mode & 0o022) || stat.size > 1024 * 1024 ||
    crypto.createHash("sha256").update(await fs.readFile(jar)).digest("hex") !== SERVER_SHA256) throw new Error("Unsafe video server artifact");
  const adapter = createHostAdapter({ captureMethod: "logical", hostLibs: layout.hostLibraries });
  const boot = parseBootClasspaths((await adapter.guest(["/system/bin/toybox", "cat", "/data/system/environ/classpath"])).toString());
  if (!dynamic) {
    const child = spawn("/usr/bin/lxc-attach", serverArgs(boot, owner), { stdio: "inherit", shell: false, env: process.env });
    for (const signal of ["SIGTERM", "SIGINT"]) process.once(signal, () => child.kill(signal));
    child.once("error", error => { console.error(error.message); process.exitCode = 1; });
    child.once("exit", (code, signal) => { process.exitCode = signal ? 1 : code; });
    return;
  }
  const uid = await settingsUserUid(execute, configuration);
  let timer, stopping = false, polling = false;
  const stop = async error => {
    if (stopping) return;
    stopping = true; clearInterval(timer);
    if (error) { console.error(error.message); process.exitCode = 1; }
    try { await controller.stop(); } catch (failure) { console.error(failure.message); process.exitCode = 1; }
  };
  const controller = createVideoCaptureController({
    readProfile: () => readVideoSettings(layout.videoSettingsFile, { uid }),
    prepare: async () => {
      const live = (await execute("/usr/bin/lxc-info", ["-P", "/var/lib/waydroid/lxc", "-n", "waydroid", "-pH"], { env: process.env })).toString().trim();
      if (live !== pid) throw new Error("Video container identity changed");
      const lease = await prepareCapture("video", { configuration });
      if (lease !== owner) throw new Error("Video native lease changed");
    },
    launch: profile => {
      console.log(`Video encoder target: ${profile.maxFps} fps, ${profile.bitRate} bps, revision ${profile.revision}`);
      return spawn("/usr/bin/lxc-attach", serverArgs(boot, owner, profile), { stdio: "inherit", shell: false, env: process.env });
    },
    onFailure: error => { void stop(error); },
  });
  for (const signal of ["SIGTERM", "SIGINT"]) process.once(signal, () => { void stop(); });
  try { await controller.reconcile(); }
  catch (error) { await stop(error); return; }
  if (!stopping) timer = setInterval(async () => {
    if (polling || stopping) return;
    polling = true;
    try { await controller.reconcile(); } catch (error) { await stop(error); }
    finally { polling = false; }
  }, 500);
}
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
module.exports = { serverArgs, SERVER_SHA256, settingsUserUid, main };
