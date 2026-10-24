// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const { parseSyntheticDevice, verifyProcess, runSessionProbe, stopTransient,
  verifyRecovered, recoverOriginal, withinBudget } = require("./session-probe");
const { readOriginalSnapshot, makeRunnable } = require("./session-probe");
const { PNG } = require("pngjs");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

test("discovers only a single named synthetic device, never a physical mouse", () => {
  assert.equal(parseSyntheticDevice('N: Name="QEMU USB Mouse"\nH: Handlers=event5\n'), null);
  assert.equal(parseSyntheticDevice('I: Bus=0006 Vendor=5441 Product=0001 Version=0000\nN: Name="TeslaAndroid x86 Touchscreen"\nH: Handlers=event8\n'), "event8");
  assert.throws(() => parseSyntheticDevice('I: Bus=0003 Vendor=0627 Product=0001\nN: Name="TeslaAndroid x86 Touchscreen"\nH: Handlers=event8\n'), /identity/);
  assert.throws(() => parseSyntheticDevice('N: Name="TeslaAndroid x86 Touchscreen"\nH: Handlers=event8\n\nN: Name="TeslaAndroid x86 Touchscreen"\nH: Handlers=event9'), /ambiguous/);
});
test("refuses to terminate a process that does not match the exact staged executable", async () => {
  await assert.rejects(verifyProcess("touch", 123, async () => Buffer.from("/system/bin/surfaceflinger\n")), /identity/);
  await assert.rejects(verifyProcess("touch", "123; reboot", async () => Buffer.alloc(0)), /PID/);
  assert.equal(await verifyProcess("touch", 123, async () => Buffer.from(
    "/data/local/tmp/tesla-waydroid-test-20261003/bin/tesla-android-x86-touch\n")), 123);
});
test("session test restores the preview even when probing fails", async () => {
  const events = [];
  const ops = { pause: async () => events.push("pause"),
    probe: async () => { events.push("probe"); throw new Error("resize failed"); },
    resume: async () => events.push("resume") };
  await assert.rejects(runSessionProbe(ops), /resize failed/);
  assert.deepEqual(events, ["pause", "probe", "resume"]);
});
test("recovery still runs if pause only partially succeeds", async () => {
  let resumed = false;
  await assert.rejects(runSessionProbe({ pause: async () => { throw new Error("pause failed"); },
    probe: async () => {}, resume: async () => { resumed = true; } }), /pause failed/);
  assert.equal(resumed, true);
});
test("cleanup failure preserves the original failure and reports both errors", async () => {
  const original = new Error("resize failed");
  const cleanup = new Error("resume failed");
  await assert.rejects(runSessionProbe({ pause: async () => {},
    probe: async () => { throw original; }, resume: async () => { throw cleanup; } }),
  (error) => error instanceof AggregateError && error.errors[0] === original && error.errors[1] === cleanup);
});
test("already-unloaded transient unit is treated as stopped, but other errors propagate", async () => {
  await stopTransient(async () => { throw Object.assign(new Error("not loaded"), { code: 5 }); });
  await assert.rejects(stopTransient(async () => { throw Object.assign(new Error("permission denied"), { code: 1 }); }), /permission denied/);
});
test("recovery rejects a black frame even when dimensions match", async () => {
  const png = new PNG({ width: 1280, height: 768 });
  for (let i = 3; i < png.data.length; i += 4) png.data[i] = 255;
  await assert.rejects(verifyRecovered({ inspect: async () => ({ width: 1280, height: 768, density: 180 }),
    capture: async () => PNG.sync.write(png) }, { width: 1280, height: 768, density: 180 }), /black/);
});
test("explicit recovery uses saved original size, not the interrupted current state", async () => {
  const saved = { width: 1280, height: 768, density: 180 };
  let state = { width: 960, height: 640, density: 180 };
  const adapter = { restore: async (snapshot) => { state = { ...snapshot }; }, inspect: async () => state,
    capture: async () => { const png = new PNG({ width: state.width, height: state.height });
      png.data.fill(255); return PNG.sync.write(png); } };
  await recoverOriginal(adapter, saved);
  assert.deepEqual(state, saved);
});
test("two individually short phases cannot pass the total shipping budget", () => {
  assert.equal(withinBudget([{ forwardMs: 11000, restoreMs: 11000, totalMs: 22000 }]), false);
  assert.equal(withinBudget([{ forwardMs: 8000, restoreMs: 6000, totalMs: 20500 }]), false);
  assert.equal(withinBudget([{ forwardMs: 8000, restoreMs: 6000, totalMs: 14500 }]), true);
});
test("saved recovery evidence is explicit, private and validated before use", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "ta-evidence-"));
  const saved = { width: 1280, height: 768, density: 180, sizeOverride: null,
    properties: { width: "", height: "" } };
  try {
    assert.throws(() => readOriginalSnapshot("../other.json", directory), /evidence/);
    fs.writeFileSync(path.join(directory, "session-resize-evidence-123.json"), JSON.stringify({ snapshot: saved }), { mode: 0o600 });
    assert.deepEqual(readOriginalSnapshot("session-resize-evidence-123.json", directory), saved);
    fs.writeFileSync(path.join(directory, "session-resize-evidence-124.json"), JSON.stringify({ snapshot: { ...saved, width: -1 } }), { mode: 0o600 });
    assert.throws(() => readOriginalSnapshot("session-resize-evidence-124.json", directory), /dimensions/);
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});
test("session recovery makes a suspended container runnable and opens the actual UI", async () => {
  const events = [];
  await makeRunnable({ state: async () => "FROZEN", unfreeze: async () => events.push("unfreeze"),
    showUI: async () => events.push("show UI") });
  assert.deepEqual(events, ["unfreeze", "show UI"]);
});
