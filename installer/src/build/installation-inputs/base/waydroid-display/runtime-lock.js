// SPDX-License-Identifier: GPL-3.0-only
const net = require("node:net");
function acquireRuntimeLock(name = "\0teslaandroid-waydroid-runtime-v1") {
  if (process.platform !== "linux") return Promise.reject(new Error("Runtime lock requires Linux"));
  if (typeof name !== "string" || !/^\0teslaandroid-waydroid-runtime-[a-z0-9-]{1,80}$/.test(name)) {
    return Promise.reject(new Error("Invalid runtime lock identity"));
  }
  return new Promise((resolve, reject) => {
    const server = net.createServer(socket => socket.destroy());
    server.once("error", () => reject(new Error("Runtime supervisor already active")));
    server.listen(name, () => resolve(server));
  });
}
module.exports = { acquireRuntimeLock };
