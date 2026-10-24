// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const { once } = require("node:events");
const { WebSocket, WebSocketServer } = require("ws");
const { createGateway } = require("./server");
const { createDisplayControl } = require("./display-control");
const { createGpsControl } = require("./gps-control");
const listen = server => new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
test("gateway.close ends an open GPS feed and waits for acknowledged native removal", async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "ta-gps-close-")); fs.writeFileSync(path.join(directory, "index.html"), "test");
  let release, ending = false, complete = false;
  const removal = new Promise(resolve => { release = resolve; });
  const state = { enabled: true, active: false, supported: true, mock: true, recoveryRequired: false };
  const gps = createGpsControl({ helper: { inspect: async () => state, begin: async () => ({ ...state, active: true }),
    heartbeat: async () => state, end: async () => { ending = true; await removal; return state; } } }); await gps.initialize();
  const display = createDisplayControl({ helper: { inspect: async () => ({ width: 980, height: 720, density: 180,
    resolutionPreset: 1, isResponsive: 1, recoveryRequired: false }) } }); await display.initialize();
  const gateway = createGateway({ frontendDir: directory, displayControl: display, gpsControl: gps }); await listen(gateway);
  const base = `http://127.0.0.1:${gateway.address().port}`, owner = display.createSession(); display.connect(owner.token, "display");
  const socket = new WebSocket(base.replace("http:", "ws:") + "/sockets/gps?session=" + owner.token + "&csrf=" + owner.csrfToken, { origin: base });
  await once(socket, "message");
  const closed = new Promise(resolve => gateway.close(() => { complete = true; resolve(); }));
  await new Promise(resolve => setTimeout(resolve, 30)); const attempted = ending, early = complete;
  release(); await gps.close(); socket.terminate(); await closed; fs.rmSync(directory, { recursive: true, force: true });
  assert.equal(attempted, true); assert.equal(early, false); assert.equal(complete, true);
});
test("GPS configuration/routes enforce owner CSRF, reject aliases and revoke live location feeds", async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "ta-gps-routes-")); fs.writeFileSync(path.join(directory, "index.html"), "test");
  let state = { enabled: true, active: false, supported: true, mock: true, recoveryRequired: false }, positions = [];
  const helper = { inspect: async () => state, setEnabled: async enabled => (state = { ...state, enabled, active: false }),
    begin: async () => (state = { ...state, active: true }), heartbeat: async () => state,
    deliver: async (_lease, fix) => { positions.push(fix); return state; }, end: async () => (state = { ...state, active: false }) };
  const gps = createGpsControl({ helper, minimumIntervalMs: 0 }); await gps.initialize();
  const api = http.createServer((_req, res) => res.end(JSON.stringify({ unchanged: 42 }))); await listen(api);
  const backend = new WebSocketServer({ host: "127.0.0.1", port: 0 }); await once(backend, "listening");
  const display = createDisplayControl({ helper: { inspect: async () => ({ width: 980, height: 720, density: 180,
    resolutionPreset: 1, isResponsive: 1, recoveryRequired: false }) } }); await display.initialize();
  const gateway = createGateway({ frontendDir: directory, displayControl: display, gpsControl: gps, targets: {
    api: `http://127.0.0.1:${api.address().port}`, display: `ws://127.0.0.1:${backend.address().port}`,
    touchscreen: `ws://127.0.0.1:${backend.address().port}` } }); await listen(gateway);
  const base = `http://127.0.0.1:${gateway.address().port}`, clients = [];
  t.after(async () => { for (const socket of clients) socket.terminate(); await gps.close();
    await new Promise(resolve => gateway.close(resolve)); await new Promise(resolve => backend.close(resolve));
    await new Promise(resolve => api.close(resolve)); fs.rmSync(directory, { recursive: true, force: true }); });
  const config = await (await fetch(base + "/api/configuration")).json();
  assert.equal(config.unchanged, 42); assert.equal(config.browserGpsSupported, 1); assert.equal(config.browserGpsMock, 1);
  assert.equal(config["persist.tesla-android.gps.is_active"], 1);
  const owner = display.createSession(), viewer = display.createSession(); display.connect(owner.token, "display");
  const post = (value, session = owner, overrides = {}) => fetch(base + "/api/gpsState", { method: "POST", body: String(value),
    headers: { Origin: base, "Content-Type": "text/plain", "X-Display-Session": session.token, "X-Display-CSRF": session.csrfToken, ...overrides } });
  assert.equal((await post(0, viewer)).status, 403);
  assert.equal((await post(0, owner, { "X-Display-CSRF": "wrong" })).status, 403);
  assert.equal((await post(0, owner, { Origin: "https://evil.test" })).status, 403);
  assert.equal((await post(2)).status, 400);
  assert.equal((await post("1".repeat(9))).status, 413);
  assert.equal((await fetch(base + "/api/%67psState", { method: "POST", body: "0" })).status, 400);
  assert.equal((await fetch(base + "/api/gpsState")).status, 405);
  const open = (session, query = "", origin = base, csrf = session.csrfToken) => new Promise((resolve, reject) => {
    const socket = new WebSocket(base.replace("http:", "ws:") + "/sockets/gps?session=" + session.token + "&csrf=" + csrf + query, { origin });
    clients.push(socket); socket.once("error", reject);
    socket.once("message", data => { assert.deepEqual(JSON.parse(data), { version: 1, ready: true, mock: true }); resolve(socket); });
  });
  await assert.rejects(open(viewer), /403/); await assert.rejects(open(owner, "", "https://evil.test"), /403/);
  await assert.rejects(open(owner, "", base, "wrong"), /403/); await assert.rejects(open(owner, "&target=x"), /403/);
  const socket = await open(owner); socket.send(JSON.stringify({ latitude: 0, longitude: 0, vertical_accuracy: 5,
    timestamp: Date.now(), speed: null, bearing: null }));
  await new Promise(resolve => setTimeout(resolve, 20)); assert.equal(positions.length, 1);
  const stopped = once(socket, "close"); assert.equal((await post(0)).status, 200); await stopped;
  await assert.rejects(open(owner), /503/); assert.equal((await post(1)).status, 200);
  const next = await open(owner), revoked = once(next, "close"); display.revokeAll(); await revoked;
  await gps.close(); assert.equal(state.active, false);
});
