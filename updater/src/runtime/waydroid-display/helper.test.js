// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const net = require("node:net");
const { createHelperServer } = require("./helper");
const state = { width: 960, height: 640, density: 180, resolutionPreset: 0, isResponsive: 1, recoveryRequired: false };
async function fixture(t, apply = async () => state, retireTouch = async () => state) {
  const server = createHelperServer({ readState: async () => state, apply, retireTouch });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  return async text => {
    const socket = net.connect(server.address().port, "127.0.0.1");
    let data = "";
    const response = new Promise((resolve, reject) => {
      socket.on("data", chunk => { data += chunk; });
      socket.on("error", reject);
      socket.on("close", () => resolve(JSON.parse(data)));
    });
    socket.write(text);
    return response;
  };
}
test("bounded helper protocol returns correlated actual state", async t => {
  const request = await fixture(t);
  assert.deepEqual(await request('{"version":1,"id":"abc","operation":"inspect"}\n'), { version: 1, id: "abc", state });
});

test("touch-only retirement is acknowledged and rejects desired or command arguments", async t => {
  let retired = 0;
  const request = await fixture(t, async () => { throw new Error("Unexpected resize"); }, async () => { retired++; return state; });
  for (const extra of [{ desired: stateWithoutRecovery() }, { command: "reboot" }]) {
    assert.ok((await request(JSON.stringify({ version: 1, id: "retire", operation: "retire_touch", ...extra }) + "\n")).error);
  }
  assert.equal(retired, 0);
  assert.deepEqual(await request('{"version":1,"id":"retire","operation":"retire_touch"}\n'),
    { version: 1, id: "retire", state });
  assert.equal(retired, 1);
});
test("helper rejects malformed, oversized, multiple or unknown command requests without applying", async t => {
  let mutations = 0;
  const request = await fixture(t, async () => { mutations++; return state; });
  for (const text of ['no json\n', 'x'.repeat(4097),
    '{"version":1,"id":"a","operation":"inspect"}\n{}\n',
    '{"version":1,"id":"a","operation":"shell","command":"reboot"}\n',
    '{"version":1,"id":"a","operation":"resize","desired":{"width":960}}\n']) {
    assert.ok((await request(text)).error);
  }
  assert.equal(mutations, 0);
});
test("helper reports failed recovery honestly", async t => {
  const request = await fixture(t, async () => { throw Object.assign(new Error("rollback failed"), { recoveryRequired: true }); });
  const reply = await request(JSON.stringify({ version: 1, id: "a", operation: "resize", desired: stateWithoutRecovery() }) + "\n");
  assert.equal(reply.recoveryRequired, true);
  assert.equal(reply.error, "rollback failed");
});
function stateWithoutRecovery() { const { recoveryRequired, ...desired } = state; return desired; }

test("not-ready helper refuses mutations while startup reconciliation is incomplete", async t => {
  let mutations = 0;
  const server = createHelperServer({ readState: async () => state, apply: async () => { mutations++; return state; } },
    { ready: () => false });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const response = await new Promise((resolve, reject) => {
    const socket = net.connect(server.address().port, "127.0.0.1");
    let data = "";
    socket.on("data", chunk => { data += chunk; });
    socket.on("close", () => resolve(JSON.parse(data)));
    socket.on("error", reject);
    socket.write(JSON.stringify({ version: 1, id: "a", operation: "resize", desired: stateWithoutRecovery() }) + "\n");
  });
  assert.equal(response.error, "Display helper is starting");
  assert.equal(mutations, 0);
});
