// SPDX-License-Identifier: GPL-3.0-only
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const { WebSocket, WebSocketServer } = require("ws");
const { createGateway } = require("./server");
const { createDisplayControl } = require("./display-control");
const { createRearDisplaySettings } = require("./rear-display-settings");
const baseline = { width: 852, height: 676, density: 180, resolutionPreset: 1, isResponsive: 1, recoveryRequired: false };
async function fixture(t, { enabled = true, retireTouch = async () => baseline, gpsControl, graceMs, clock } = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "ta-rear-routes-"));
  fs.writeFileSync(path.join(directory, "index.html"), "<!doctype html><title>Test</title>");
  const proxied = [], sockets = [];
  const api = http.createServer((req, res) => { proxied.push(req.url); res.end("{}"); });
  await new Promise(resolve => api.listen(0, "127.0.0.1", resolve));
  const backend = new WebSocketServer({ host: "127.0.0.1", port: 0 });
  await new Promise(resolve => backend.once("listening", resolve));
  backend.on("connection", socket => { socket.send("frame"); });
  const rearSettings = enabled ? createRearDisplaySettings(path.join(directory, "rear.json")) : undefined;
  let gateway;
  const control = createDisplayControl({ rearSettings, graceMs, clock,
    ...(enabled ? { retireAuthority: (...args) => gateway.retireDisplayAuthority(...args) } : {}),
    helper: { inspect: async () => baseline, resize: async desired => ({ ...desired, recoveryRequired: false }) } });
  await control.initialize();
  const target = `ws://127.0.0.1:${backend.address().port}`;
  gateway = createGateway({ frontendDir: directory, displayControl: control, gpsControl,
    ...(enabled ? { retireTouch } : {}),
    targets: { api: `http://127.0.0.1:${api.address().port}`, display: target, touchscreen: target } });
  await new Promise(resolve => gateway.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${gateway.address().port}`;
  t.after(async () => {
    for (const socket of sockets) socket.terminate();
    for (const socket of backend.clients) socket.terminate();
    await new Promise(resolve => gateway.close(resolve));
    await new Promise(resolve => backend.close(resolve));
    await new Promise(resolve => api.close(resolve));
    assert.ok(directory.startsWith(path.resolve(os.tmpdir()) + path.sep));
    assert.ok(path.basename(directory).startsWith("ta-rear-routes-"));
    fs.rmSync(directory, { recursive: true, force: true });
  });
  const post = (pathname, session, payload, headers = {}) => fetch(base + pathname, { method: "POST",
    headers: { Origin: base, "Content-Type": "application/json", "X-Display-Session": session?.token,
      "X-Display-CSRF": session?.csrfToken, ...headers }, body: JSON.stringify(payload) });
  const create = async () => (await (await post("/api/displaySession", null, {})).json());
  const get = (pathname, session) => fetch(base + pathname, { headers: { "X-Display-Session": session.token } });
  const open = (session, channel = "display") => new Promise((resolve, reject) => {
    const socket = new WebSocket(base.replace("http:", "ws:") + `/sockets/${channel}?session=${session.token}`, { origin: base });
    sockets.push(socket); socket.once("error", reject); socket.once("open", () => resolve(socket));
  });
  return { base, control, post, create, get, open, proxied };
}

test('explicit role claim requires canonical generation/CSRF and closes old touch without closing either display', async t => {
  let retired = 0;
  const f = await fixture(t, { retireTouch: async () => { retired++; return baseline; } });
  const a = await f.create(), b = await f.create(), first = await f.open(a), second = await f.open(b), touch = await f.open(a, 'touchscreen');
  const closed = new Promise(resolve => touch.once('close', resolve));
  const generation = f.control.snapshot(b.token).generation;
  for (const payload of [{ role: 'primary', claim: false, generation }, { role: 'primary', claim: true, generation: '0' },
    { role: 'primary', claim: true }, { role: 'primary', claim: true, generation, extra: 1 }]) assert.equal((await f.post('/api/displayRole', b, payload)).status, 400);
  assert.equal((await f.post('/api/displayRole', b, { role: 'primary', claim: true, generation }, { 'X-Display-CSRF': 'wrong' })).status, 403);
  assert.equal(retired, 0);
  const response = await f.post('/api/displayRole', b, { role: 'primary', claim: true, generation });
  assert.equal(response.status, 200); await closed;
  assert.equal((await response.json()).canControl, true); assert.equal(retired, 1);
  assert.equal(f.control.snapshot(a.token).displayRole, 'rear');
  assert.equal(first.readyState, WebSocket.OPEN); assert.equal(second.readyState, WebSocket.OPEN);
  assert.equal((await f.open(b, 'touchscreen')).readyState, WebSocket.OPEN);
});

test("rear role and settings routes require session CSRF, same origin and bounded canonical JSON", async t => {
  const f = await fixture(t), a = await f.create(), b = await f.create();
  await f.open(a); await f.open(b);
  for (const [headers, status] of [[{ Origin: "https://attacker.test" }, 403],
    [{ "X-Display-CSRF": "wrong" }, 403], [{ "Content-Type": "text/plain" }, 415]]) {
    assert.equal((await f.post("/api/displayRole", a, { role: "rear" }, headers)).status, status);
  }
  assert.equal((await f.post("/api/displayRole", a, { role: "admin" })).status, 400);
  assert.equal((await f.post("/api/displayRole", a, { role: "primary", extra: true })).status, 400);
  assert.equal((await f.post("/api/displayRole?role=rear", a, { role: "primary" })).status, 400);
  assert.equal((await f.post("/api/%64isplayRole", a, { role: "primary" })).status, 400);
  assert.equal((await f.post("/api/rearDisplayState", b, { revision: 0, enabled: true, prioritised: true })).status, 403);
  assert.equal((await f.post("/api/displayRole", a, { role: "x".repeat(260) })).status, 413);
  assert.equal((await f.post("/api/displayRole", b, { role: "rear" })).status, 200);
  assert.deepEqual(await (await f.get("/api/rearDisplayState", b)).json(),
    { version: 1, revision: 0, enabled: false, prioritised: false });
  assert.deepEqual(f.proxied, []);
});

test("real socket handoff closes old touch but keeps both display streams, awaiting GPS and native acknowledgement", async t => {
  let finish, entered, gpsRetired = false;
  const started = new Promise(resolve => { entered = resolve; });
  const gpsControl = { retireFeed: async () => { gpsRetired = true; }, close: async () => {} };
  const f = await fixture(t, { gpsControl, retireTouch: async () => {
    assert.equal(gpsRetired, true); entered(); await new Promise(resolve => { finish = resolve; }); return baseline;
  } });
  const a = await f.create(), b = await f.create();
  await f.post("/api/displayRole", a, { role: "primary" });
  await f.post("/api/displayRole", b, { role: "rear" });
  const primaryVideo = await f.open(a), rearVideo = await f.open(b), oldTouch = await f.open(a, "touchscreen");
  const oldClosed = new Promise(resolve => oldTouch.once("close", resolve));
  const pending = f.post("/api/rearDisplayState", a, { revision: 0, enabled: true, prioritised: true });
  await started; await oldClosed;
  assert.equal(f.control.snapshot(a.token).canControl, false);
  assert.equal(f.control.snapshot(b.token).transactionStatus, "transferring");
  await assert.rejects(f.open(b, "touchscreen"), /503/);
  assert.equal(primaryVideo.readyState, WebSocket.OPEN); assert.equal(rearVideo.readyState, WebSocket.OPEN);
  finish(); assert.equal((await pending).status, 200);
  assert.equal(f.control.snapshot(b.token).canControl, true);
  assert.equal(f.control.snapshot(a.token).canControl, false);
  assert.equal((await f.open(b, "touchscreen")).readyState, WebSocket.OPEN);
  assert.equal((await f.post("/api/rearDisplayState", b, { revision: 0, enabled: true, prioritised: false })).status, 409);
});

test("native geometry mismatch fails closed without giving either browser authority", async t => {
  const f = await fixture(t, { retireTouch: async () => ({ ...baseline, width: 960 }) });
  const a = await f.create(), b = await f.create(); await f.open(a); await f.open(b);
  await f.post("/api/displayRole", a, { role: "primary" }); await f.post("/api/displayRole", b, { role: "rear" });
  assert.equal((await f.post("/api/rearDisplayState", a, { revision: 0, enabled: true, prioritised: true })).status, 503);
  assert.equal(f.control.snapshot(a.token).transactionStatus, "authority_failed");
  assert.equal(f.control.snapshot(b.token).canControl, false);
});

test("feature-off routes stay unavailable and never reach the legacy native API", async t => {
  const f = await fixture(t, { enabled: false }), a = await f.create(); await f.open(a);
  assert.equal((await f.post("/api/displayRole", a, { role: "rear" })).status, 404);
  assert.equal((await f.get("/api/rearDisplayState", a)).status, 404);
  assert.deepEqual(f.proxied, []);
  assert.equal(f.control.snapshot(a.token).canControl, true);
});

test("an expired owner retires at committed geometry before a connected viewer is promoted", async t => {
  let finish, entered;
  let now = 0, id = 0;
  const timers = new Map();
  const clock = { now: () => now,
    setTimeout(fn, ms) { timers.set(++id, { fn, at: now + ms }); return id; },
    clearTimeout(key) { timers.delete(key); },
    tick(ms) { now += ms; for (const [key, timer] of [...timers]) {
      if (timer.at <= now) { timers.delete(key); timer.fn(); }
    } } };
  const started = new Promise(resolve => { entered = resolve; });
  const f = await fixture(t, { clock, graceMs: 100, retireTouch: async () => {
    entered(); await new Promise(resolve => { finish = resolve; }); return baseline;
  } });
  const a = await f.create(), b = await f.create();
  const oldVideo = await f.open(a); await f.open(b);
  await f.post("/api/displayRole", a, { role: "primary" });
  await f.post("/api/displayRole", b, { role: "primary" });
  const disconnect = f.control.disconnect.bind(f.control);
  const disconnected = new Promise(resolve => {
    f.control.disconnect = (token, channel) => {
      disconnect(token, channel);
      if (token === a.token && channel === "display") resolve();
    };
  });
  oldVideo.close();
  await disconnected;
  // Expire only after the real socket disconnect, not during HTTP setup under load.
  clock.tick(99);
  assert.equal(f.control.snapshot(a.token).canControl, true);
  clock.tick(1);
  await Promise.race([started, new Promise((_, reject) => setTimeout(() => reject(new Error("Retirement did not start")), 1500))]);
  assert.throws(() => f.control.snapshot(a.token), /session/);
  assert.equal(f.control.snapshot(b.token).canControl, false);
  assert.equal(f.control.snapshot(b.token).transactionStatus, "transferring");
  finish();
  for (let attempt = 0; attempt < 20 && !f.control.snapshot(b.token).canControl; attempt++) await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(f.control.snapshot(b.token).canControl, true);
  assert.equal(f.control.snapshot(b.token).transactionStatus, "idle");
});
