// SPDX-License-Identifier: GPL-3.0-only
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const { setTimeout: delay } = require("node:timers/promises");
const { createGpsHelperClient } = require("../preview-gateway/gps-helper-client");
const { createLocationAdapter, GPS_JAR } = require("./location-adapter");
const { createHostAdapter, execute, parseBootClasspaths, GUEST_ARGS } = require("./host-adapter");
async function restoreGps(client, lease, enabled) {
  const errors = [];
  try { await client.end(lease); } catch (error) { errors.push(error); }
  try { await client.setEnabled(enabled); } catch (error) { errors.push(error); }
  if (errors.length) throw new AggregateError(errors, "GPS probe restoration failed");
}
async function main() {
  if (process.getuid?.() !== 0) throw new Error("GPS helper probe requires root");
  const client = createGpsHelperClient("/run/teslaandroid-waydroid/gps.sock");
  const initial = await client.inspect();
  assert.equal(initial.supported, true); assert.equal(initial.active, false); assert.equal(initial.recoveryRequired, false);
  const lease = crypto.randomBytes(16).toString("hex"), foreign = crypto.randomBytes(16).toString("hex");
  const hostLibs = "/opt/teslaandroid-waydroid/libgbinder", host = createHostAdapter({ hostLibs });
  const adapter = createLocationAdapter({ guest: host.guest });
  let leavingForRestart = false;
  try {
    await client.setEnabled(true); assert.equal((await client.begin(lease)).active, true);
    await assert.rejects(client.deliver(foreign, { latitude: 0, longitude: 0, accuracy: 5,
      timestamp: Date.now(), speed: 2, bearing: 90 }));
    await client.deliver(lease, { latitude: 0, longitude: 0, accuracy: 5, timestamp: Date.now(), speed: 2, bearing: 90 });
    const boot = parseBootClasspaths((await host.guest(["/system/bin/toybox", "cat", "/data/system/environ/classpath"])).toString());
    const output = await execute("/usr/bin/lxc-attach", ["-u", "1000", "-g", "1000", ...GUEST_ARGS,
      "/system/bin/env", "CLASSPATH=" + GPS_JAR, "ANDROID_ART_ROOT=/apex/com.android.art",
      "ANDROID_I18N_ROOT=/apex/com.android.i18n", "ANDROID_TZDATA_ROOT=/apex/com.android.tzdata", ...boot,
      "/system/bin/app_process", "/system/bin", "TeslaLocationBridge", "--verify", lease],
      { env: { ...process.env, LD_LIBRARY_PATH: hostLibs } });
    assert.deepEqual(JSON.parse(output.toString()), { version: 1, verified: true, mock: true });
    if (process.argv.includes("--leave-for-restart")) {
      leavingForRestart = true;
      console.log(JSON.stringify({ delivered: true, active: true, restartProbe: true })); return;
    }
    await delay(16000);
    assert.equal((await client.inspect()).active, false);
    assert.equal((await adapter.inspect(foreign)).present, false);
    await client.begin(lease); await client.setEnabled(false);
    assert.equal((await adapter.inspect(foreign)).present, false);
    await assert.rejects(client.begin(lease));
  } finally {
    if (!leavingForRestart) {
      await restoreGps(client, lease, initial.enabled);
      assert.equal((await adapter.inspect(foreign)).present, false);
    }
  }
  console.log(JSON.stringify({ delivered: true, mock: true, expired: true, disabled: true, removed: true }));
}
if (require.main === module) main().catch(() => { console.error("GPS helper or cleanup probe failed"); process.exitCode = 1; });
module.exports = { restoreGps };
