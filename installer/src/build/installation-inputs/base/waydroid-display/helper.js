// SPDX-License-Identifier: GPL-3.0-only
const fs = require("node:fs/promises");
const net = require("node:net");
const { createHostAdapter } = require("./host-adapter");
const { createTouchAdapter } = require("./touch-adapter");
const { createTransactions, validateDesired } = require("./transaction");
const { createStore } = require("./store");
const { setTimeout: delay } = require("node:timers/promises");
const { loadWorkerConfiguration } = require('../portable/worker-config');
const { deriveRuntimeLayout } = require('../portable/runtime-config');

function createHelperServer(transactions, { ready = () => true } = {}) {
  const sockets = new Set();
  let pending = 0, closing = false;
  const server = net.createServer(socket => {
    if (closing || sockets.size >= 64) { socket.destroy(); return; }
    sockets.add(socket);
    const controller = new AbortController();
    let buffer = Buffer.alloc(0), started = false, replied = false;
    const reply = value => {
      if (replied || socket.destroyed) return;
      replied = true;
      socket.end(JSON.stringify({ version: 1, ...value }) + "\n");
    };
    socket.setTimeout(25000, () => socket.destroy());
    socket.on("error", () => {});
    socket.on("close", () => {
      sockets.delete(socket);
      if (!replied) controller.abort(new Error("Display requester disconnected"));
    });
    socket.on("data", async chunk => {
      if (started) { controller.abort(new Error("Multiple helper requests")); reply({ error: "One request per connection" }); return; }
      if (buffer.length + chunk.length > 4096) { started = true; reply({ error: "Helper request too large" }); return; }
      buffer = Buffer.concat([buffer, chunk]);
      const newline = buffer.indexOf(10);
      if (newline < 0) return;
      started = true;
      pending++;
      let request;
      try {
        if (newline !== buffer.length - 1) throw new Error("One request per connection");
        request = JSON.parse(buffer.subarray(0, newline).toString("utf8"));
        if (!request || Array.isArray(request) || request.version !== 1 ||
            typeof request.id !== "string" || !/^[a-zA-Z0-9_-]{1,64}$/.test(request.id) ||
            !["inspect", "resize", "retire_touch"].includes(request.operation) ||
            Object.keys(request).some(key => !["version", "id", "operation", "desired"].includes(key)) ||
            (request.operation !== "resize" && "desired" in request)) throw new Error("Invalid display helper request");
        if (request.operation === "resize") validateDesired(request.desired);
        if (!ready() || closing) throw new Error("Display helper is starting");
        const state = request.operation === "inspect" ? await transactions.readState() :
          request.operation === "retire_touch" ? await transactions.retireTouch({ signal: controller.signal }) :
            await transactions.apply(request.desired, { signal: controller.signal });
        reply({ id: request.id, state });
      } catch (error) { reply({ id: request?.id, error: error.message, recoveryRequired: error.recoveryRequired === true,
        ...(error.status === 409 ? { status: 409 } : {}) }); }
      finally { pending--; }
    });
  });
  server.on("close", () => { for (const socket of sockets) socket.destroy(); });
  server.shutdown = async () => {
    closing = true;
    for (const socket of sockets) socket.destroy();
    await new Promise(resolve => server.close(resolve));
    while (pending) await delay(25);
  };
  return server;
}

async function main({ configuration } = {}) {
  if (process.getuid?.() !== 0) throw new Error("Display helper requires root on the disposable Waydroid VM");
  configuration = await loadWorkerConfiguration({ role: 'privileged', read: () => configuration });
  const layout = configuration && deriveRuntimeLayout(configuration), gid = configuration?.gid ?? 1000;
  const stateDirectory = layout?.state ?? "/var/lib/teslaandroid-waydroid";
  const socketDirectory = layout?.sockets ?? "/run/teslaandroid-waydroid";
  for (const [directory, mode] of [[stateDirectory, 0o700], [socketDirectory, 0o750]]) {
    await fs.mkdir(directory, { mode }).catch(error => { if (error.code !== "EEXIST") throw error; });
    const stat = await fs.lstat(directory);
    if (!stat.isDirectory() || stat.uid !== 0 || (stat.mode & (mode === 0o750 ? 0o027 : 0o077))) throw new Error("Unsafe helper directory");
  }
  await fs.chown(socketDirectory, 0, gid);
  const hostLibs = layout?.hostLibraries ?? "/opt/teslaandroid-waydroid/libgbinder";
  const adapter = createHostAdapter({ captureMethod: "logical", hostLibs });
  Object.assign(adapter, createTouchAdapter({ guest: adapter.guest, calibrate: adapter.calibrate, hostLibs,
    supervised: process.env.TESLA_WAYDROID_SUPERVISED === "1" }));
  const transactions = createTransactions({ adapter, store: createStore(stateDirectory + "/display.json") });
  const socketPath = socketDirectory + "/display.sock";
  try {
    const stat = await fs.lstat(socketPath);
    if (!stat.isSocket() || stat.uid !== 0) throw new Error("Unsafe existing helper socket");
    // Never unlink an active helper; a service restart must close the predecessor first.
    const active = await new Promise(resolve => {
      const client = net.connect(socketPath);
      client.once("connect", () => { client.destroy(); resolve(true); });
      client.once("error", error => { resolve(error.code !== "ECONNREFUSED"); });
      client.setTimeout(1000, () => { client.destroy(); resolve(true); });
    });
    if (active) throw new Error("Display helper socket is already active");
    await fs.unlink(socketPath);
  } catch (error) { if (error.code !== "ENOENT") throw error; }
  let ready = false;
  const server = createHelperServer(transactions, { ready: () => ready });
  process.umask(0o077);
  await new Promise((resolve, reject) => { server.once("error", reject); server.listen(socketPath, resolve); });
  await fs.chown(socketPath, 0, gid);
  await fs.chmod(socketPath, 0o660);
  try { await transactions.reconcile(); ready = true; }
  catch (error) { server.close(); throw error; }
  console.log("Private Waydroid display helper ready");
  for (const signal of ["SIGTERM", "SIGINT"]) process.once(signal, () => {
    void server.shutdown().then(() => process.exit(0));
  });
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { createHelperServer, main };
