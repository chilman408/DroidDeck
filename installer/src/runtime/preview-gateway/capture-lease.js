// SPDX-License-Identifier: GPL-3.0-only
const { createNativeRuntime } = require("../waydroid-display/runtime-native");
const { normalizeRuntimeConfig } = require('../portable/runtime-config');
function captureEnvironment(owner, role) {
  if (!["video", "audio"].includes(role)) throw new Error("Invalid capture role");
  if (owner === undefined) return [];
  if (typeof owner !== "string" || !/^[a-f0-9]{32}$/.test(owner)) throw new Error("Invalid capture lease");
  return ["TESLA_WAYDROID_RUNTIME_OWNER=" + owner, "TESLA_WAYDROID_RUNTIME_ROLE=" + role];
}
async function prepareCapture(role, { environment = process.env, native, createNative = createNativeRuntime, configuration } = {}) {
  const config = configuration === undefined ? undefined : normalizeRuntimeConfig(configuration);
  const owner = environment.TESLA_WAYDROID_RUNTIME_OWNER, actualRole = environment.TESLA_WAYDROID_RUNTIME_ROLE;
  captureEnvironment(owner, role);
  if (owner === undefined && actualRole === undefined) return;
  if (owner === undefined || actualRole !== role) throw new Error("Invalid capture lease role");
  native ??= createNative({ configuration: config });
  // A restarted host launcher must retire its own escaped guest capture first.
  await native.adopt(owner);
  await native.stop(role);
  return owner;
}
module.exports = { captureEnvironment, prepareCapture };
