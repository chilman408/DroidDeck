// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const net = require("node:net");
const { createHelperClient } = require("./display-helper-client");
const state = { width: 960, height: 640, density: 180, resolutionPreset: 0, isResponsive: 1, recoveryRequired: false };
async function fixture(t, respond) {
  const sockets = new Set();
  const server = net.createServer(socket => { sockets.add(socket); socket.on("close", () => sockets.delete(socket));
    socket.on("data", data => { const request = JSON.parse(data); respond(socket, request); }); });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  t.after(() => { for (const socket of sockets) socket.destroy(); return new Promise(resolve => server.close(resolve)); });
  return createHelperClient(null, { timeoutMs: 100, connect: () => net.connect(server.address().port, "127.0.0.1") });
}
test("helper client correlates inspect and resize responses", async t => {
  const client = await fixture(t, (socket, req) => socket.end(JSON.stringify({ version: 1, id: req.id, state }) + "\n"));
  assert.deepEqual(await client.inspect(), state); assert.deepEqual(await client.resize(state), state);
});

test("touch retirement uses a correlated no-argument private request", async t => {
  const client = await fixture(t, (socket, req) => {
    assert.equal(req.operation, "retire_touch"); assert.equal("desired" in req, false);
    socket.end(JSON.stringify({ version: 1, id: req.id, state }) + "\n");
  });
  assert.deepEqual(await client.retireTouch(), state);
});
test("helper client rejects wrong IDs/versions, malformed and oversized replies", async t => {
  for (const response of [{ version: 1, id: "wrong", state }, { version: 2, state }, { version: 1, state: { width: 1 } }, "x".repeat(4097)]) {
    const client = await fixture(t, (socket, req) => socket.end(typeof response === "string" ? response : JSON.stringify({ id: req.id, ...response }) + "\n"));
    await assert.rejects(client.inspect());
  }
});
test("helper timeout and caller cancellation close the socket", async t => {
  const client = await fixture(t, () => {}); await assert.rejects(client.inspect(), /timed out/);
  const controller = new AbortController(); const pending = client.resize(state, { signal: controller.signal });
  controller.abort(new Error("revoked")); await assert.rejects(pending, /revoked/);
});
test("helper failures preserve recovery-required and busy status", async t => {
  const client = await fixture(t, (socket, req) => socket.end(JSON.stringify({ version: 1, id: req.id, error: "busy", status: 409, recoveryRequired: true }) + "\n"));
  await assert.rejects(client.inspect(), error => error.status === 409 && error.recoveryRequired);
});
