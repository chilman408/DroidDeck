// SPDX-License-Identifier: GPL-3.0-only
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const { createLocationAdapter, GPS_JAR } = require("./location-adapter");
const { createHostAdapter, execute, parseBootClasspaths, GUEST_ARGS } = require("./host-adapter");

async function probe({ adapter, verify, rejectNative } = {}) {
  const lease = crypto.randomBytes(16).toString("hex"), foreign = crypto.randomBytes(16).toString("hex");
  assert.equal((await adapter.inspect(lease)).present, false, "Existing GPS provider: probe must not run");
  try {
    assert.deepEqual(await adapter.open(lease), { present: true, owned: true });
    assert.deepEqual(await adapter.inspect(foreign), { present: true, owned: false }, "Lease tag must constrain ownership");
    await assert.rejects(adapter.close(foreign), "A foreign lease must not remove the provider");
    await assert.rejects(adapter.deliver(foreign, { latitude: 0, longitude: 0, accuracy: 5,
      timestamp: Date.now(), speed: 2, bearing: 90 }), "A foreign lease must not inject");
    assert.equal((await adapter.inspect(lease)).owned, true);
    await adapter.deliver(lease, { latitude: 0, longitude: 0, accuracy: 5, timestamp: Date.now(), speed: 2, bearing: 90 });
    const result = await verify(lease, true);
    assert.deepEqual(result, { version: 1, verified: true, mock: true });
    if (rejectNative) await rejectNative(lease);
    await adapter.deliver(lease, { latitude: 0, longitude: 0, accuracy: 5, timestamp: Date.now(), speed: null, bearing: null });
    assert.deepEqual(await verify(lease, false), { version: 1, verified: true, mock: true });
    return { verified: true, mock: true, ownershipChecked: true, motionCleared: true,
      ...(rejectNative ? { nativeValidationChecked: true } : {}), removed: true };
  } finally {
    await adapter.close(lease);
    assert.deepEqual(await adapter.inspect(lease), { present: false, owned: false });
  }
}
if (require.main === module) {
  if (process.getuid?.() !== 0) throw new Error("Location adapter probe requires root");
  const hostLibs = "/opt/teslaandroid-waydroid/libgbinder";
  const host = createHostAdapter({ hostLibs });
  const adapter = createLocationAdapter({ guest: host.guest });
  const command = async (operation, lease) => {
    const boot = parseBootClasspaths((await host.guest(["/system/bin/toybox", "cat", "/data/system/environ/classpath"])).toString());
    return ["/system/bin/env", "CLASSPATH=" + GPS_JAR, "ANDROID_ART_ROOT=/apex/com.android.art",
      "ANDROID_I18N_ROOT=/apex/com.android.i18n", "ANDROID_TZDATA_ROOT=/apex/com.android.tzdata", ...boot,
      "/system/bin/app_process", "/system/bin", "TeslaLocationBridge", operation, lease];
  };
  const verify = async (lease, motion) => {
    const output = await execute("/usr/bin/lxc-attach", ["-u", "1000", "-g", "1000", ...GUEST_ARGS,
      ...await command(motion ? "--verify" : "--verify-no-motion", lease)],
      { env: { ...process.env, LD_LIBRARY_PATH: hostLibs } });
    if (output.length > 1024) throw new Error("Location verification exceeded limit");
    return JSON.parse(output.toString());
  };
  const rejectNative = async lease => {
    const fix = { latitude: 0, longitude: 0, accuracy: 5, timestamp: Date.now(), speed: 2, bearing: 90 };
    const { bearing, ...missing } = fix;
    const invalid = ["{", "x".repeat(2049), JSON.stringify({ ...fix, latitude: "0" }),
      JSON.stringify({ ...fix, accuracy: -1 }), JSON.stringify({ ...fix, speed: 151 }),
      JSON.stringify({ ...fix, timestamp: Date.now() - 60000 }), JSON.stringify({ ...missing, unknown: 1 }),
      JSON.stringify({ ...fix, unknown: 1 })];
    for (const input of invalid) {
      await assert.rejects(host.guest(await command("--apply", lease), { input }), "Malformed native input must be rejected");
    }
  };
  probe({ adapter, verify, rejectNative }).then(state => console.log(JSON.stringify(state)))
    .catch(() => { console.error("GPS adapter or cleanup probe failed"); process.exitCode = 1; });
}
module.exports = { probe };
