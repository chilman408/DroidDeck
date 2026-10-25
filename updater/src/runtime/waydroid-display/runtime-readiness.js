// SPDX-License-Identifier: GPL-3.0-only
const { setTimeout: delay } = require("node:timers/promises");
const { execute } = require("./host-adapter");
const { runtimeUnit, ROOT, NODE_PATH } = require("./runtime-commands");
const { createHelperClient } = require("../preview-gateway/display-helper-client");
const { createGpsHelperClient } = require("../preview-gateway/gps-helper-client");
const { normalizeRuntimeConfig, deriveRuntimeLayout } = require('../portable/runtime-config');
function createRuntimeReadiness({ run = execute, display, gps, timeoutMs = 30000,
  sleep = (ms, options) => delay(ms, undefined, options), configuration } = {}) {
  const config = configuration === undefined ? undefined : normalizeRuntimeConfig(configuration);
  const layout = config ? deriveRuntimeLayout(config) : null, root = layout?.root || ROOT;
  const nodePath = config ? root + '/runtime/preview-gateway/node_modules:' + root + '/runtime/waydroid-display/node_modules' : NODE_PATH;
  const sockets = layout?.sockets || '/run/teslaandroid-waydroid';
  display ||= createHelperClient(sockets + '/display.sock');
  gps ||= createGpsHelperClient(sockets + '/gps.sock');
  return async (name, state, { signal } = {}) => {
    runtimeUnit(name);
    const deadline = AbortSignal.timeout(timeoutMs), bounded = signal ? AbortSignal.any([signal, deadline]) : deadline;
    const options = { signal: bounded, env: { ...process.env, NODE_PATH: nodePath, LD_LIBRARY_PATH: root + "/libgbinder" } };
    for (;;) {
      bounded.throwIfAborted();
      try {
        if (name === "display-helper") {
          const actual = await display.inspect({ signal: bounded });
          if (actual.recoveryRequired) throw new Error("Display helper recovery required");
        } else if (name === "gps") {
          const actual = await gps.inspect({ signal: bounded });
          if (!actual.supported || actual.recoveryRequired) throw new Error("GPS helper recovery required");
        } else {
          const user = ["/usr/sbin/runuser", "-u", config?.user || "hilman", "--", "/usr/bin/env", "NODE_PATH=" + nodePath,
            ...(config ? ['TESLA_WAYDROID_PORTABLE_PROBE_CONFIG=' + JSON.stringify(config)] : []),
            "/usr/bin/node", root + "/runtime/preview-gateway/runtime-probe.js", name,
            ...(name === "gateway" ? [state.containerAddress] : [])];
          const host = name === "lan-relay";
          const file = host ? user[0] : "/usr/bin/nsenter";
          const args = host ? user.slice(1) : ["-t", String(state.pid), "-n", ...user];
          if ((await run(file, args, options)).toString().trim() !== "READY") throw new Error("Invalid runtime readiness acknowledgement");
        }
        bounded.throwIfAborted(); return;
      } catch (error) {
        if (bounded.aborted || error.recoveryRequired) throw error;
        await sleep(250, { signal: bounded });
      }
    }
  };
}
module.exports = { createRuntimeReadiness };
