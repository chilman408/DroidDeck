// SPDX-License-Identifier: GPL-3.0-only
// Read only: asserts the browser's synthetic fixture while its GPS feed is active.
const assert = require("node:assert/strict");
const { createGpsHelperClient } = require("../preview-gateway/gps-helper-client");
const { createHostAdapter, execute, parseBootClasspaths, GUEST_ARGS } = require("./host-adapter");
const { GPS_JAR } = require("./location-adapter");
async function main() {
  if (process.getuid?.() !== 0) throw new Error("GPS readback requires root");
  const helper = createGpsHelperClient("/run/teslaandroid-waydroid/gps.sock");
  const state = await helper.inspect();
  assert.equal(state.enabled, true); assert.equal(state.active, true); assert.equal(state.recoveryRequired, false);
  const hostLibs = "/opt/teslaandroid-waydroid/libgbinder";
  const host = createHostAdapter({ hostLibs });
  const boot = parseBootClasspaths((await host.guest(["/system/bin/toybox", "cat", "/data/system/environ/classpath"])).toString());
  const output = await execute("/usr/bin/lxc-attach", ["-u", "1000", "-g", "1000", ...GUEST_ARGS,
    "/system/bin/env", "CLASSPATH=" + GPS_JAR, "ANDROID_ART_ROOT=/apex/com.android.art",
    "ANDROID_I18N_ROOT=/apex/com.android.i18n", "ANDROID_TZDATA_ROOT=/apex/com.android.tzdata", ...boot,
    "/system/bin/app_process", "/system/bin", "TeslaLocationBridge", "--verify", "0".repeat(32)],
    { env: { ...process.env, LD_LIBRARY_PATH: hostLibs } });
  assert.deepEqual(JSON.parse(output.toString()), { version: 1, verified: true, mock: true });
  console.log(JSON.stringify({ browserToAndroid: true, syntheticReadbackVerified: true, mock: true }));
}
if (require.main === module) main().catch(() => { console.error("Browser GPS readback failed"); process.exitCode = 1; });
