// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const { runLocationProbe, PROBE_JAR } = require("./location-probe");
const PROVIDER = "teslaandroid_probe_0123456789abcdef";
const CLASS_PATHS = "export BOOTCLASSPATH /system/framework/framework.jar\nexport DEX2OATBOOTCLASSPATH /apex/com.android.art/javalib/core-oj.jar\n";
const SUCCESS = { version: 1, delivered: true, motion: true, mock: true, removed: true };
function fixture(output = JSON.stringify({ version: 1, delivered: true, motion: true, mock: true })) {
  const calls = [];
  const guest = async args => {
    calls.push(args);
    if (args.at(-1) === "/data/system/environ/classpath") return Buffer.from(CLASS_PATHS);
    if (args[1] === "stat") return Buffer.from("0:644\n");
    if (args[1] === "sha256sum") return Buffer.from("hash  " + PROBE_JAR + "\n");
    if (args.includes("TeslaLocationProbe")) {
      if (!args.includes("--observe")) return Buffer.from("OK\n");
      if (output instanceof Error) throw output;
      return Buffer.from(output);
    }
    return Buffer.alloc(0);
  };
  return { calls, guest, observe: guest };
}

test("location probe runs only fixed protected helper and cleans up its unique provider", async () => {
  const f = fixture();
  assert.deepEqual(await runLocationProbe({ guest: f.guest, observe: f.observe, provider: PROVIDER, expectedHash: "hash", intervalMs: 0 }), SUCCESS);
  const launch = f.calls.find(args => args.includes("TeslaLocationProbe"));
  assert.equal(launch.at(-1), PROVIDER); assert.ok(launch.includes("CLASSPATH=" + PROBE_JAR));
  assert.deepEqual(f.calls.at(-2), ["/system/bin/cmd", "location", "providers", "remove-test-provider", PROVIDER]);
  assert.ok(f.calls.at(-1).includes("--absent"));
  assert.equal(f.calls.some(args => args.includes("appops") || args.includes("set-location-enabled")), false);
});

test("failed or malformed delivery still cleans up only the probe provider", async () => {
  for (const output of [new Error("subprocess failed"), "not-json", JSON.stringify({ ...SUCCESS, motion: false }),
    JSON.stringify({ ...SUCCESS, extra: true })]) {
    const f = fixture(output);
    await assert.rejects(runLocationProbe({ guest: f.guest, observe: f.observe, provider: PROVIDER, expectedHash: "hash", intervalMs: 0 }));
    assert.equal(f.calls.at(-1).at(-1), PROVIDER);
    assert.ok(f.calls.at(-2).includes("remove-test-provider"));
  }
});

test("unsafe artifact or provider never launches or modifies Android providers", async () => {
  for (const provider of ["gps", "teslaandroid_probe_bad", PROVIDER + ";id"]) {
    const f = fixture(); await assert.rejects(runLocationProbe({ guest: f.guest, observe: f.observe, provider, expectedHash: "hash" }));
    assert.equal(f.calls.length, 0);
  }
  const f = fixture();
  await assert.rejects(runLocationProbe({ guest: f.guest, observe: f.observe, provider: PROVIDER, expectedHash: "different" }), /Unsafe location probe artifact/);
  assert.equal(f.calls.some(args => args.includes("TeslaLocationProbe") || args.includes("remove-test-provider")), false);
});

test("cleanup failure is surfaced even after successful delivery", async () => {
  const f = fixture();
  await assert.rejects(runLocationProbe({ provider: PROVIDER, expectedHash: "hash", observe: f.observe, intervalMs: 0, guest: async args => {
    if (args.includes("remove-test-provider")) throw new Error("cleanup unavailable");
    return f.guest(args);
  } }), /cleanup unavailable/);
});

test("a pre-existing probe name is never injected into or removed", async () => {
  const f = fixture();
  await assert.rejects(runLocationProbe({ provider: PROVIDER, expectedHash: "hash", observe: f.observe,
    guest: async args => args.includes("--create") ? Buffer.from("EXISTS\n") : f.guest(args) }), /already exists/);
  assert.equal(f.calls.some(args => args.includes("--inject") || args.includes("remove-test-provider")), false);
});

test("cleanup must verify absence before claiming removed", async () => {
  const f = fixture();
  await assert.rejects(runLocationProbe({ provider: PROVIDER, expectedHash: "hash", intervalMs: 0, observe: f.observe,
    guest: async args => args.includes("--absent") ? Buffer.from("PRESENT\n") : f.guest(args) }), /cleanup unverified/);
});
