// SPDX-License-Identifier: GPL-3.0-only
const assert = require("node:assert/strict");
const test = require("node:test");
const { PNG } = require("pngjs");
const { createHostAdapter, execute } = require("./host-adapter");

function fixture(override = false, options = {}, status = "RUNNING") {
  const calls = [];
  const png = new PNG({ width: 1280, height: 768 });
  png.data.fill(255);
  const run = async (file, args, options) => {
    calls.push({ file, args, options });
    if (file === "/usr/bin/lxc-info") return Buffer.from(status + "\n");
    if (args.includes("/system/bin/screencap")) return PNG.sync.write(png);
    if (args.at(-1) === "size") return Buffer.from("Physical size: 1280x768\n" +
      (override ? "Override size: 1024x640\n" : ""));
    if (args.at(-1) === "density") return Buffer.from("Physical density: 180\n" +
      (override ? "Override density: 220\n" : ""));
    if (args.at(-1) === "/system/bin/getprop") return Buffer.from(
      "[persist.waydroid.width]: []\n[persist.waydroid.height]: []\n[ro.waydroid.software_rendering]: [1]\n");
    if (args.includes("stat")) return Buffer.from("d:45:660:1000:1004\n");
    return Buffer.alloc(0);
  };
  return { adapter: createHostAdapter({ run, ...options }), calls };
}

test("inspection reports capture dimensions and actual density, retaining overrides", async () => {
  const { adapter } = fixture(true);
  const state = await adapter.inspect();
  assert.equal(state.width, 1280);
  assert.equal(state.height, 768);
  assert.equal(state.density, 220);
  assert.equal(state.sizeOverride, "1024x640");
  assert.equal(state.densityOverride, "220");
  assert.deepEqual(state.properties, { width: "", height: "", software: "1", subsurface: "" });
});
test("logical resize uses validated argv and restoration removes only added overrides", async () => {
  const { adapter, calls } = fixture();
  const before = await adapter.inspect();
  await adapter.applySize({ width: 960, height: 640, density: 180 });
  await adapter.restore(before);
  const commands = calls.filter(({ args }) => args.includes("/system/bin/wm"));
  assert.deepEqual(commands.slice(-2).map(({ args }) => args.slice(-2)), [["size", "960x640"], ["size", "reset"]]);
  const capture = calls.find(({ args }) => args.includes("/system/bin/screencap"));
  assert.ok(capture.args.includes("LD_LIBRARY_PATH=/data/local/tmp/tesla-waydroid-test-20261003/lib64"));
  assert.equal(capture.options.env.LD_LIBRARY_PATH, "/home/hilman/libgbinder-1.1.45/build/release");
  assert.equal(capture.options.shell, false);
});
test("unsafe dimensions cannot become host or guest commands", async () => {
  const { adapter, calls } = fixture();
  for (const width of ["960; reboot", -1, 0, 1922, 961, 1.5]) {
    await assert.rejects(adapter.applySize({ width, height: 640, density: 180 }), /dimensions/);
  }
  await assert.rejects(adapter.applySize({ width: 1920, height: 1920, density: 180 }), /dimensions/);
  assert.equal(calls.length, 0);
});
test("restoration preserves an existing user size override", async () => {
  const { adapter, calls } = fixture(true);
  await adapter.restore(await adapter.inspect());
  assert.deepEqual(calls.at(-1).args.slice(-2), ["size", "1024x640"]);
});
test("property resize restores saved properties and reinitializes the session", async () => {
  let restarts = 0;
  const { adapter, calls } = fixture(false, {
    method: "properties", restartSession: async () => { restarts++; },
  });
  const snapshot = await adapter.inspect();
  await adapter.applySize({ width: 960, height: 640, density: 180 });
  await adapter.restore(snapshot);
  const writes = calls.filter(({ args }) => args.includes("/system/bin/setprop"));
  assert.deepEqual(writes.map(({ args }) => args.slice(-2)), [
    ["persist.waydroid.width", "960"], ["persist.waydroid.height", "640"],
    ["persist.waydroid.width", ""], ["persist.waydroid.height", ""],
  ]);
  assert.equal(restarts, 2);
});
test("guest commands use direct lxc exit status rather than Waydroid's success-only shell wrapper", async () => {
  const { adapter, calls } = fixture();
  await adapter.capture();
  const attach = calls.find(({ file }) => file === "/usr/bin/lxc-attach");
  assert.ok(attach);
  assert.ok(attach.args.includes("--clear-env"));
  await assert.rejects(execute(process.execPath, ["-e", "process.exit(23)"], { shell: false }),
    (error) => error.code === 23);
});
test("unfreezes a suspended container before executing guest commands", async () => {
  const { adapter, calls } = fixture(false, {}, "FROZEN");
  await adapter.capture();
  assert.deepEqual(calls.slice(0, 3).map(({ file }) => file),
    ["/usr/bin/lxc-info", "/usr/bin/lxc-unfreeze", "/usr/bin/lxc-attach"]);
});
