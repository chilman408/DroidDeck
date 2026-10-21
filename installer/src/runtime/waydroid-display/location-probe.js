// SPDX-License-Identifier: GPL-3.0-only
const crypto = require("node:crypto");
const { createHostAdapter, parseBootClasspaths, execute, GUEST_ARGS } = require("./host-adapter");
const { setTimeout: delay } = require("node:timers/promises");
const PROBE_JAR = "/data/local/tmp/tesla-waydroid-test-20261003/location-probe.jar";
const PROBE_HASH = "18d05aa2d00a476d887eff2952e4550e09d4e88463f709fe1e8f0c9d440d128d";

async function runLocationProbe({ guest, observe, provider = "teslaandroid_probe_" + crypto.randomBytes(8).toString("hex"), expectedHash = PROBE_HASH, intervalMs = 250 } = {}) {
  if (typeof guest !== "function" || typeof observe !== "function" || !/^teslaandroid_probe_[a-f0-9]{16}$/.test(provider) ||
      !Number.isInteger(intervalMs) || intervalMs < 0 || intervalMs > 1000) throw new Error("Invalid location probe");
  const ownership = (await guest(["/system/bin/toybox", "stat", "-c", "%u:%a", PROBE_JAR])).toString().trim();
  const digest = (await guest(["/system/bin/toybox", "sha256sum", PROBE_JAR])).toString().trim();
  if (ownership !== "0:644" || digest !== expectedHash + "  " + PROBE_JAR) throw new Error("Unsafe location probe artifact");
  const boot = parseBootClasspaths((await guest(["/system/bin/toybox", "cat", "/data/system/environ/classpath"])).toString());
  const command = operation => ["/system/bin/env", "CLASSPATH=" + PROBE_JAR,
      "ANDROID_ART_ROOT=/apex/com.android.art", "ANDROID_I18N_ROOT=/apex/com.android.i18n",
      "ANDROID_TZDATA_ROOT=/apex/com.android.tzdata", ...boot,
      "/system/bin/app_process", "/system/bin", "TeslaLocationProbe", operation, provider];
  let reader, result, readerError, finished = false, cleanup = true;
  const controller = new AbortController();
  try {
    const created = (await guest(command("--create"))).toString().trim();
    if (created === "EXISTS") { cleanup = false; throw new Error("Location probe already exists"); }
    if (created !== "OK") throw new Error("Location probe creation failed");
    reader = Promise.resolve().then(() => observe(command("--observe"), { signal: controller.signal }))
      .then(output => { result = output; }, error => { readerError = error; }).finally(() => { finished = true; });
    for (let i = 0; i < 12 && !finished; i++) {
      if ((await guest(command("--inject"))).toString().trim() !== "OK") throw new Error("Location probe injection failed");
      if (!finished) await delay(intervalMs);
    }
    await reader;
    if (readerError) throw readerError;
    const output = result;
    if (output.length > 1024) throw new Error("Invalid location probe result");
    const state = JSON.parse(output.toString());
    if (!state || state.version !== 1 || !["delivered", "motion", "mock"].every(key => state[key] === true) ||
        Object.keys(state).length !== 4) throw new Error("Invalid location probe result");
    return { ...state, removed: true };
  } finally {
    controller.abort(new Error("Location probe complete"));
    if (reader) await reader;
    // Independent cleanup also handles a killed JVM; the nonce never names a real provider.
    if (cleanup) {
      await guest(["/system/bin/cmd", "location", "providers", "remove-test-provider", provider]);
      if ((await guest(command("--absent"))).toString().trim() !== "OK") throw new Error("Location probe cleanup unverified");
    }
  }
}
if (require.main === module) {
  if (process.getuid?.() !== 0) throw new Error("Location probe requires root on the disposable Waydroid VM");
  const adapter = createHostAdapter({ hostLibs: "/opt/teslaandroid-waydroid/libgbinder" });
  const observe = (args, { signal }) => execute("/usr/bin/lxc-attach", ["-u", "1000", "-g", "1000", ...GUEST_ARGS, ...args],
    { env: { ...process.env, LD_LIBRARY_PATH: "/opt/teslaandroid-waydroid/libgbinder" }, signal });
  runLocationProbe({ guest: adapter.guest, observe }).then(result => console.log(JSON.stringify(result)))
    .catch(() => { console.error("Location delivery probe or cleanup failed"); process.exitCode = 1; });
}
module.exports = { runLocationProbe, PROBE_JAR, PROBE_HASH };
