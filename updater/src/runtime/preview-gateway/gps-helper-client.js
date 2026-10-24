// SPDX-License-Identifier: GPL-3.0-only
const net = require("node:net");
const crypto = require("node:crypto");
const { validateRequest, validateState } = require("../waydroid-display/gps-protocol");
function createGpsHelperClient(socketPath, { connect = () => net.connect(socketPath), timeoutMs = 45000 } = {}) {
  async function request(operation, fields = {}, { signal } = {}) {
    const id = crypto.randomBytes(16).toString("hex");
    const message = validateRequest({ version: 1, id, operation, ...fields });
    signal?.throwIfAborted();
    return new Promise((resolve, reject) => {
      const socket = connect(); let buffer = Buffer.alloc(0), settled = false;
      const finish = (error, value) => {
        if (settled) return; settled = true; clearTimeout(timer);
        signal?.removeEventListener("abort", abort); socket.destroy();
        if (error) reject(error); else resolve(value);
      };
      const abort = () => finish(new Error("GPS request cancelled"));
      const timer = setTimeout(() => finish(new Error("GPS helper timed out")), timeoutMs);
      signal?.addEventListener("abort", abort, { once: true }); if (signal?.aborted) abort();
      socket.once("connect", () => { if (!settled) socket.write(JSON.stringify(message) + "\n"); });
      socket.on("error", () => finish(new Error("GPS helper unavailable")));
      socket.on("data", chunk => {
        if (buffer.length + chunk.length > 2048) { finish(new Error("GPS reply too large")); return; }
        buffer = Buffer.concat([buffer, chunk]);
      });
      socket.once("end", () => {
        try {
          if (!buffer.length || buffer.indexOf(10) !== buffer.length - 1) throw new Error();
          const response = JSON.parse(buffer.toString("utf8"));
          if (response.version !== 1 || response.id !== id ||
              Object.keys(response).some(key => !["version", "id", "state", "error", "recoveryRequired"].includes(key))) throw new Error();
          if (Object.hasOwn(response, "error")) {
            if (Object.keys(response).length !== 4 || typeof response.recoveryRequired !== "boolean" ||
                !["gps-request-failed", "gps-recovery-required"].includes(response.error) ||
                response.recoveryRequired !== (response.error === "gps-recovery-required")) throw new Error();
            finish(Object.assign(new Error(response.recoveryRequired ? "GPS recovery required" : "GPS request failed"),
              { recoveryRequired: response.recoveryRequired, status: 503 })); return;
          }
          if (Object.keys(response).length !== 3) throw new Error();
          finish(null, validateState(response.state));
        } catch { finish(new Error("GPS request failed")); }
      });
      socket.once("close", () => { if (!settled) finish(new Error("GPS helper disconnected")); });
    });
  }
  return { inspect: options => request("inspect", {}, options),
    setEnabled: (enabled, options) => request("enabled", { enabled }, options),
    begin: (client, options) => request("begin", { client }, options),
    heartbeat: (client, options) => request("heartbeat", { client }, options),
    deliver: (client, fix, options) => request("deliver", { client, fix }, options),
    end: (client, options) => request("end", { client }, options) };
}
module.exports = { createGpsHelperClient };
