// SPDX-License-Identifier: GPL-3.0-only
// Disposable GPS acceptance preview; run only while the public coordinator is stopped.
const fs = require("node:fs/promises");
const crypto = require("node:crypto");
const { createGateway } = require("./server");
const { createDisplayControl } = require("./display-control");
const { createHelperClient } = require("./display-helper-client");
const { createGpsControl } = require("./gps-control");
const { createGpsHelperClient } = require("./gps-helper-client");
const HOME = "/home/hilman/teslaandroid-waydroid";
async function main() {
  if (process.getuid?.() !== 1000) throw new Error("Isolated preview requires the VM user");
  const file = HOME + "/gps-stage-20261007/gps-test-auth-20261008.json";
  let auth;
  try { const fd = await fs.open(file, "wx", 0o600);
    try { auth = { username: "gps-test", password: crypto.randomBytes(32).toString("hex") }; await fd.writeFile(JSON.stringify(auth)); }
    finally { await fd.close(); }
  } catch (error) {
    if (error.code !== "EEXIST") throw error;
    const stat = await fs.lstat(file);
    if (!stat.isFile() || stat.uid !== 1000 || stat.nlink !== 1 || (stat.mode & 0o077) || stat.size > 512) throw new Error("Unsafe test credentials");
    auth = JSON.parse(await fs.readFile(file, "utf8"));
    if (auth.username !== "gps-test" || typeof auth.password !== "string" || !/^[a-f0-9]{64}$/.test(auth.password)) throw new Error("Invalid test credentials");
  }
  const displayControl = createDisplayControl({ helper: createHelperClient("/run/teslaandroid-waydroid/display.sock") });
  await displayControl.initialize();
  const gpsControl = createGpsControl({ helper: createGpsHelperClient("/run/teslaandroid-waydroid/gps.sock") });
  await gpsControl.initialize();
  const server = createGateway({ frontendDir: HOME + "/frontend-gps-test", publicOrigin: "https://127.0.0.1:18445", auth,
    tls: { key: await fs.readFile("/home/hilman/.config/teslaandroid-waydroid/key.pem"),
      cert: await fs.readFile("/home/hilman/.config/teslaandroid-waydroid/cert.pem") }, displayControl, gpsControl,
    targets: { api: "http://127.0.0.1:8081", display: "ws://127.0.0.1:9091", touchscreen: "ws://127.0.0.1:9999", h264: "ws://127.0.0.1:9092" } });
  server.listen(18444, "192.168.240.112", () => console.log("Isolated authenticated GPS preview ready"));
  for (const signal of ["SIGTERM", "SIGINT"]) process.once(signal, () => {
    displayControl.close(); void gpsControl.close().finally(() => { server.close(() => process.exit(0)); server.closeAllConnections(); });
  });
}
if (require.main === module) main().catch(() => { console.error("Isolated GPS preview failed"); process.exitCode = 1; });
