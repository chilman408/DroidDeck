// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const { restartGraphics } = require("./graphics-restart");

function fixture() {
  const calls = [];
  let requested = false, captureCount = 0, sleeps = 0;
  const ops = {
    guest: async (args) => {
      calls.push(args);
      if (args[1] === "init.svc.vendor.hwcomposer-2-1") return Buffer.from("running\n");
      if (args[1] === "init.svc_debug_pid.vendor.hwcomposer-2-1") return Buffer.from(requested ? "225\n" : "224\n");
      if (args[1] === "ctl.restart") { requested = true; return Buffer.alloc(0); }
      if (args[1] === "init.svc.bootanim") return Buffer.from("stopped\n");
      if (args[1] === "check") return Buffer.from("Service window: found\n");
      throw new Error("Unexpected guest command");
    },
    capture: async () => (++captureCount === 1 ? { width: 1280, height: 768, nonblack: true } :
      { width: 960, height: 640, nonblack: true }),
    showUI: async () => calls.push(["UI"]), sleep: async () => { sleeps++; },
  };
  return { ops, calls, sleeps: () => sleeps };
}

test("restarts only the verified graphics service and waits for the actual target frame", async () => {
  const { ops, calls, sleeps } = fixture();
  await restartGraphics(ops, { width: 960, height: 640, density: 180 });
  assert.equal(sleeps(), 1);
  assert.deepEqual(calls.filter((args) => args[1] === "ctl.restart"),
    [["/system/bin/setprop", "ctl.restart", "vendor.hwcomposer-2-1"]]);
  assert.ok(calls.some((args) => args[0] === "UI"));
});

test("refuses a missing composer before requesting any restart", async () => {
  const { ops, calls } = fixture();
  const guest = ops.guest;
  ops.guest = async (args) => args[1] === "init.svc.vendor.hwcomposer-2-1" ? Buffer.from("stopped") : guest(args);
  await assert.rejects(restartGraphics(ops, { width: 960, height: 640, density: 180 }), /composer/);
  assert.equal(calls.some((args) => args[1] === "ctl.restart"), false);
});

test("black target frames do not pass and cancelled restart requires quiescence", async () => {
  const { ops } = fixture();
  const controller = new AbortController();
  ops.capture = async () => ({ width: 960, height: 640, nonblack: false });
  ops.sleep = async () => controller.abort(new Error("cancelled"));
  await assert.rejects(restartGraphics(ops, { width: 960, height: 640, density: 180 }, controller.signal),
    (error) => error.message === "cancelled" && error.quiescenceRequired === true);
});
