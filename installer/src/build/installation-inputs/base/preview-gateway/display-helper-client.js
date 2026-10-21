// SPDX-License-Identifier: GPL-3.0-only
const net = require("node:net");
const crypto = require("node:crypto");
const { validateDesired } = require("../waydroid-display/transaction");

function validateState(value) {
  const { recoveryRequired, ...desired } = value || {};
  if (typeof recoveryRequired !== "boolean") throw new Error("Invalid helper state");
  return { ...validateDesired(desired), recoveryRequired };
}
function createHelperClient(socketPath, { timeoutMs = 25000, connect = () => net.connect(socketPath) } = {}) {
  function request(operation, desired, { signal } = {}) {
    return new Promise((resolve, reject) => {
      if (signal?.aborted) { reject(signal.reason); return; }
      const id = crypto.randomBytes(16).toString("hex");
      const socket = connect();
      let buffer = Buffer.alloc(0), settled = false;
      const finish = (error, value) => {
        if (settled) return; settled = true;
        clearTimeout(timer); signal?.removeEventListener("abort", abort); socket.destroy();
        if (error) reject(error); else resolve(value);
      };
      const abort = () => finish(signal.reason || new Error("Display request cancelled"));
      const timer = setTimeout(() => finish(new Error("Display helper timed out")), timeoutMs);
      signal?.addEventListener("abort", abort, { once: true });
      socket.once("error", error => finish(error));
      socket.once("connect", () => socket.write(JSON.stringify({ version: 1, id, operation,
        ...(operation === "resize" ? { desired } : {}) }) + "\n"));
      socket.on("data", chunk => {
        if (buffer.length + chunk.length > 4096) { finish(new Error("Display helper reply too large")); return; }
        buffer = Buffer.concat([buffer, chunk]);
      });
      socket.once("end", () => {
        try {
          if (buffer.indexOf(10) !== buffer.length - 1 || !buffer.length) throw new Error("Invalid display helper reply");
          const response = JSON.parse(buffer.toString("utf8"));
          if (response.version !== 1 || response.id !== id ||
              Object.keys(response).some(key => !["version", "id", "state", "error", "recoveryRequired", "status"].includes(key))) {
            throw new Error("Uncorrelated display helper reply");
          }
          if (response.error) throw Object.assign(new Error(String(response.error)), {
            status: response.status === 409 ? 409 : 503, recoveryRequired: response.recoveryRequired === true });
          finish(null, validateState(response.state));
        } catch (error) { finish(error); }
      });
      socket.once("close", () => { if (!settled) finish(new Error("Display helper disconnected")); });
    });
  }
  return { inspect: options => request("inspect", undefined, options),
    retireTouch: options => request("retire_touch", undefined, options),
    resize: (desired, options) => request("resize", validateDesired(Object.fromEntries(
      ["width", "height", "density", "resolutionPreset", "isResponsive"].map(key => [key, desired[key]]))), options) };
}
module.exports = { createHelperClient, validateState };
