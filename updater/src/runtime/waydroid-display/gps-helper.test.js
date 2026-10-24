// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const net = require("node:net");
const { createGpsHelperServer } = require("./gps-helper");
const { createGpsHelperClient } = require("../preview-gateway/gps-helper-client");
const state = { enabled: true, active: false, supported: true, mock: true, recoveryRequired: false };
async function fixture(t) {
  const calls = []; const controllers = [];
  const service = Object.fromEntries(["inspect", "setEnabled", "begin", "heartbeat", "deliver", "end"].map(operation =>
    [operation, async (...args) => { calls.push({ operation, args }); if (args.at(-1)?.signal) controllers.push(args.at(-1).signal); return state; }]));
  const server = createGpsHelperServer(service);
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  t.after(() => server.shutdown());
  const connect = () => net.connect(server.address().port, "127.0.0.1");
  const request = text => new Promise((resolve, reject) => {
    const socket = connect(); let data = "";
    socket.on("data", chunk => { data += chunk; }); socket.on("error", reject);
    socket.on("close", () => resolve(JSON.parse(data))); socket.write(text);
  });
  return { calls, controllers, request, client: createGpsHelperClient("unused", { connect }) };
}
test("GPS private client/server correlates fixed operations and actual state", async t => {
  const f = await fixture(t), lease = "a".repeat(32);
  assert.deepEqual(await f.client.inspect(), state);
  await f.client.setEnabled(false); await f.client.begin(lease); await f.client.heartbeat(lease);
  await f.client.deliver(lease, { latitude: 0, longitude: 0, accuracy: 5, timestamp: Date.now(), speed: null, bearing: null });
  await f.client.end(lease);
  assert.deepEqual(f.calls.map(call => call.operation), ["inspect", "setEnabled", "begin", "heartbeat", "deliver", "end"]);
  assert.equal(f.calls[4].args[0], lease); assert.equal(f.calls[4].args[1].speed, null);
});
test("GPS protocol rejects malformed, multiple, oversized and caller-supplied commands before mutation", async t => {
  const f = await fixture(t); const lease = "a".repeat(32);
  for (const text of ["bad\n", "x".repeat(4097), '{"version":1,"id":"a","operation":"inspect"}\n{}\n',
    JSON.stringify({ version: 1, id: "a", operation: "shell", command: "id" }) + "\n",
    JSON.stringify({ version: 1, id: "a", operation: "inspect", client: lease }) + "\n",
    JSON.stringify({ version: 1, id: "a", operation: "enabled", enabled: 1 }) + "\n",
    JSON.stringify({ version: 1, id: "a", operation: "begin", client: "gps" }) + "\n",
    JSON.stringify({ version: 1, id: "a", operation: "begin", client: [lease] }) + "\n",
    JSON.stringify({ version: 1, id: "a", operation: ["enabled"], enabled: false }) + "\n",
    JSON.stringify({ version: 1, id: "a", operation: "deliver", client: lease, fix: { latitude: 0 } }) + "\n"]) {
    assert.ok((await f.request(text)).error);
  }
  assert.equal(f.calls.length, 0);
});
test("GPS cleanup failure crosses the private boundary as a fixed recovery status", async t => {
  const service = { setEnabled: async () => { throw Object.assign(new Error("private backend details"), { recoveryRequired: true }); } };
  const server = createGpsHelperServer(service); await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  t.after(() => server.shutdown());
  const client = createGpsHelperClient("unused", { connect: () => net.connect(server.address().port, "127.0.0.1") });
  await assert.rejects(client.setEnabled(false), error => error.recoveryRequired === true && error.message === "GPS recovery required");
});
