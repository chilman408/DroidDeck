// SPDX-License-Identifier: GPL-3.0-only
const fs = require("node:fs/promises");
const net = require("node:net");
const { setTimeout: delay } = require("node:timers/promises");
const { createHostAdapter } = require("./host-adapter");
const { createLocationAdapter } = require("./location-adapter");
const { createGpsLifecycle } = require("./gps-lifecycle");
const { createGpsStore } = require("./gps-store");
const { validateRequest, validateState } = require("./gps-protocol");
const { loadWorkerConfiguration } = require('../portable/worker-config');
const { deriveRuntimeLayout } = require('../portable/runtime-config');
function createGpsHelperServer(service) {
  const sockets = new Set(); let closing = false, pending = 0;
  const server = net.createServer(socket => {
    if (closing || sockets.size >= 32) { socket.destroy(); return; }
    sockets.add(socket); const controller = new AbortController();
    let buffer = Buffer.alloc(0), started = false, replied = false;
    const reply = value => {
      if (replied || socket.destroyed) return; replied = true;
      socket.end(JSON.stringify({ version: 1, ...value }) + "\n");
    };
    socket.on("error", () => {}); socket.setTimeout(45000, () => socket.destroy());
    socket.once("close", () => { sockets.delete(socket); if (!replied) controller.abort(); });
    socket.on("data", async chunk => {
      if (started) { controller.abort(); socket.destroy(); return; }
      if (buffer.length + chunk.length > 4096) { started = true; reply({ error: "Invalid GPS request" }); return; }
      buffer = Buffer.concat([buffer, chunk]); const newline = buffer.indexOf(10);
      if (newline < 0) return;
      started = true; pending++; let request;
      try {
        if (newline !== buffer.length - 1 || closing) throw new Error();
        request = validateRequest(JSON.parse(buffer.subarray(0, newline).toString("utf8")));
        const options = { signal: controller.signal }; let state;
        switch (request.operation) {
          case "inspect": state = await service.inspect(); break;
          case "enabled": state = await service.setEnabled(request.enabled, options); break;
          case "deliver": state = await service.deliver(request.client, request.fix, options); break;
          default: state = await service[request.operation](request.client, options);
        }
        reply({ id: request.id, state: validateState(state) });
      } catch (error) { reply({ ...(request ? { id: request.id } : {}),
        error: error.recoveryRequired === true ? "gps-recovery-required" : "gps-request-failed",
        recoveryRequired: error.recoveryRequired === true }); }
      finally { pending--; }
    });
  });
  server.shutdown = async () => {
    closing = true; for (const socket of sockets) socket.destroy();
    await new Promise(resolve => server.close(resolve)); while (pending) await delay(25);
  };
  return server;
}
function acquireGpsLock(name = "\0teslaandroid-waydroid-gps-v1") {
  return new Promise((resolve, reject) => {
    const lock = net.createServer(socket => socket.destroy());
    lock.once("error", () => reject(new Error("GPS helper already active")));
    lock.listen(name, () => resolve(lock));
  });
}
async function main({ configuration } = {}) {
  if (process.getuid?.() !== 0) throw new Error("GPS helper requires root");
  if (process.platform !== "linux") throw new Error("GPS helper requires Linux");
  configuration = await loadWorkerConfiguration({ role: 'privileged', read: () => configuration });
  // Abstract Unix sockets are exclusive and kernel-released even after SIGKILL.
  const lock = await acquireGpsLock();
  try { await startHelper(lock, configuration); }
  catch (error) { await new Promise(resolve => lock.close(resolve)); throw error; }
}
async function startHelper(lock, configuration) {
  const layout = configuration && deriveRuntimeLayout(configuration), gid = configuration?.gid ?? 1000;
  const stateDirectory = layout?.state ?? "/var/lib/teslaandroid-waydroid", socketDirectory = layout?.sockets ?? "/run/teslaandroid-waydroid";
  for (const [directory, mode] of [[stateDirectory, 0o700], [socketDirectory, 0o750], [socketDirectory + "/gps", 0o700]]) {
    await fs.mkdir(directory, { mode }).catch(error => { if (error.code !== "EEXIST") throw error; });
    const stat = await fs.lstat(directory);
    if (!stat.isDirectory() || stat.uid !== 0 || (stat.mode & (mode === 0o750 ? 0o027 : 0o077))) {
      throw new Error("Unsafe GPS directory");
    }
  }
  await fs.chown(socketDirectory, 0, gid);
  const adapter = createLocationAdapter({ guest: createHostAdapter({ hostLibs: layout?.hostLibraries ?? "/opt/teslaandroid-waydroid/libgbinder" }).guest });
  const service = createGpsLifecycle({ adapter, preferences: createGpsStore(stateDirectory + "/gps.json", "preferences"),
    leases: createGpsStore(socketDirectory + "/gps/lease.json", "journal") });
  const socketPath = socketDirectory + "/gps.sock";
  try {
    const stat = await fs.lstat(socketPath);
    if (!stat.isSocket() || stat.uid !== 0) throw new Error("Unsafe GPS socket");
    const active = await new Promise(resolve => {
      const client = net.connect(socketPath);
      client.once("connect", () => { client.destroy(); resolve(true); });
      client.once("error", error => { client.destroy(); resolve(error.code !== "ECONNREFUSED"); });
      client.setTimeout(1000, () => { client.destroy(); resolve(true); });
    });
    if (active) throw new Error("GPS helper already active"); await fs.unlink(socketPath);
  } catch (error) { if (error.code !== "ENOENT") throw error; }
  await service.reconcile();
  process.umask(0o077); const server = createGpsHelperServer(service);
  await new Promise((resolve, reject) => { server.once("error", reject); server.listen(socketPath, resolve); });
  await fs.chown(socketPath, 0, gid); await fs.chmod(socketPath, 0o660);
  const timer = setInterval(() => { void service.sweep().catch(() => {}); }, 1000);
  console.log("Private Waydroid GPS helper ready");
  for (const signal of ["SIGTERM", "SIGINT"]) process.once(signal, () => {
    clearInterval(timer);
    const stopped = service.shutdown();
    void server.shutdown().then(() => stopped).then(() => new Promise(resolve => lock.close(resolve))).then(() => process.exit(0))
      .catch(() => { console.error("GPS shutdown requires recovery"); process.exit(1); });
  });
}
if (require.main === module) main().catch(() => { console.error("GPS helper startup failed"); process.exitCode = 1; });
module.exports = { createGpsHelperServer, acquireGpsLock, main };
