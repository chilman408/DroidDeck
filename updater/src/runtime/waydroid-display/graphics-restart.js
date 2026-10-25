// SPDX-License-Identifier: GPL-3.0-only
const { setTimeout: delay } = require("node:timers/promises");
const { validateSize } = require("./host-adapter");

async function restartGraphics({ guest, capture, showUI,
  sleep = (signal) => delay(250, undefined, { signal }) }, desired, signal) {
  validateSize(desired);
  signal?.throwIfAborted();
  const get = async (name) => (await guest(["/system/bin/getprop", name], signal)).toString().trim();
  if (await get("init.svc.vendor.hwcomposer-2-1") !== "running") throw new Error("Expected composer is not running");
  const oldPid = await get("init.svc_debug_pid.vendor.hwcomposer-2-1");
  if (!/^\d+$/.test(oldPid) || Number(oldPid) < 2) throw new Error("Invalid composer identity");
  let requested = false;
  try {
    // Android init declares composer -> SurfaceFlinger -> zygote restart dependencies.
    requested = true;
    await guest(["/system/bin/setprop", "ctl.restart", "vendor.hwcomposer-2-1"], signal);
    let opened = false;
    for (;;) {
      signal?.throwIfAborted();
      const pid = await get("init.svc_debug_pid.vendor.hwcomposer-2-1");
      if (/^\d+$/.test(pid) && pid !== oldPid && await get("init.svc.bootanim") === "stopped") {
        const window = (await guest(["/system/bin/service", "check", "window"], signal)).toString();
        if (/\bfound\b/.test(window)) {
          if (!opened) { await showUI(signal); opened = true; }
          try {
            const frame = await capture(signal);
            if (frame.nonblack && frame.width === desired.width && frame.height === desired.height) return;
          } catch (error) { signal?.throwIfAborted(); if (error.code !== 1) throw error; }
        }
      }
      await sleep(signal);
    }
  } catch (error) {
    if (requested && signal?.aborted) error.quiescenceRequired = true;
    throw error;
  }
}

module.exports = { restartGraphics };
