// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const { createLocationAdapter, GPS_JAR, GPS_HASH, REPRODUCIBLE_GPS_HASH } = require("./location-adapter");
const HASH = "a".repeat(64), LEASE = "b".repeat(32);
function fixture({ ownership = "0:644", digest = HASH, response, expectedHash = HASH } = {}) {
  const calls = [];
  const guest = async (args, options) => {
    calls.push({ args, options });
    if (args[1] === "stat") return Buffer.from(ownership);
    if (args[1] === "sha256sum") return Buffer.from(digest + "  " + GPS_JAR);
    if (args.at(-1) === "/data/system/environ/classpath") return Buffer.from(
      "export BOOTCLASSPATH /system/framework/framework.jar\nexport DEX2OATBOOTCLASSPATH /apex/com.android.art/javalib/core-oj.jar\n");
    return Buffer.from(response || JSON.stringify({ version: 1, present: !args.includes("--close"), owned: !args.includes("--close") }));
  };
  return { calls, adapter: createLocationAdapter({ guest, expectedHash: expectedHash === null ? undefined : expectedHash }) };
}

test("default admission accepts only the legacy and independently reproduced helpers in nonwritable root modes", async () => {
  for (const digest of [GPS_HASH, REPRODUCIBLE_GPS_HASH]) {
    for (const ownership of ['0:600', '0:644']) {
      assert.equal((await fixture({ digest, ownership, expectedHash: null }).adapter.open(LEASE)).owned, true);
    }
  }
  for (const options of [{ digest: HASH }, { digest: REPRODUCIBLE_GPS_HASH, ownership: '0:640' },
    { digest: REPRODUCIBLE_GPS_HASH, ownership: '1000:600' }]) {
    await assert.rejects(fixture({ ...options, expectedHash: null }).adapter.inspect(LEASE), /Unsafe location helper/);
  }
});

test("GPS fixes are revalidated and sent through bounded stdin only to a fixed helper", async () => {
  const f = fixture();
  const fix = { latitude: 0, longitude: -180, accuracy: 5, timestamp: Date.now(), speed: null, bearing: 0 };
  assert.equal((await f.adapter.deliver(LEASE, fix)).owned, true);
  const call = f.calls.at(-1);
  assert.deepEqual(call.args.slice(-3), ["TeslaLocationBridge", "--apply", LEASE]);
  assert.ok(call.args.includes("CLASSPATH=" + GPS_JAR));
  assert.deepEqual(JSON.parse(call.options.input), fix);
  assert.equal(call.args.includes(String(fix.timestamp)), false);
  assert.equal(f.calls.slice(0, -1).some(call => call.options?.input), false);
});

test("GPS lifecycle acknowledges actual owned/absent state rather than provider write success", async () => {
  const f = fixture();
  assert.deepEqual(await f.adapter.open(LEASE), { present: true, owned: true });
  assert.deepEqual(await f.adapter.close(LEASE), { present: false, owned: false });
  const conflict = fixture({ response: JSON.stringify({ version: 1, present: true, owned: false }) });
  await assert.rejects(conflict.adapter.open(LEASE), /Location provider conflict/);
  await assert.rejects(conflict.adapter.close(LEASE), /Location provider conflict/);
});

test("invalid leases, stale fixes, extra fields and untrusted artifacts never launch Java", async () => {
  const f = fixture();
  for (const lease of ["", "gps", LEASE + ";id", [LEASE]]) await assert.rejects(f.adapter.open(lease), /Invalid location lease/);
  assert.equal(f.calls.length, 0);
  const fix = { latitude: 1, longitude: 2, accuracy: 5, timestamp: Date.now() - 31000, speed: null, bearing: null };
  await assert.rejects(f.adapter.deliver(LEASE, fix), /Invalid location/);
  await assert.rejects(f.adapter.deliver(LEASE, { ...fix, timestamp: Date.now(), extra: 1 }), /Invalid location/);
  assert.equal(f.calls.length, 0);
  for (const options of [{ ownership: "1000:644" }, { ownership: "0:666" }, { digest: "c".repeat(64) }]) {
    const unsafe = fixture(options); await assert.rejects(unsafe.adapter.inspect(LEASE), /Unsafe location helper/);
    assert.equal(unsafe.calls.some(call => call.args.includes("TeslaLocationBridge")), false);
  }
});

test("ambiguous or oversized replies fail closed and caller cancellation is forwarded", async () => {
  for (const response of ["garbage", "x".repeat(1025), JSON.stringify({ version: 1, present: false, owned: true }),
    JSON.stringify({ version: 1, present: true, owned: true, latitude: 1 })]) {
    await assert.rejects(fixture({ response }).adapter.inspect(LEASE), /Invalid location helper response/);
  }
  const f = fixture(); const controller = new AbortController();
  await f.adapter.open(LEASE, { signal: controller.signal });
  assert.equal(f.calls.at(-1).options.signal, controller.signal);
});
