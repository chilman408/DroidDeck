// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const { WebSocket, WebSocketServer } = require("ws");
const { createGateway } = require("./server");
const { createDisplayControl } = require("./display-control");
const baseline = { width: 1280, height: 768, density: 180, resolutionPreset: 0, isResponsive: 1, recoveryRequired: false };
const listen = server => new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
async function fixture(t, resize, h264 = false) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "ta-display-routes-"));
  fs.writeFileSync(path.join(directory, "index.html"), "<!doctype html><title>Test</title>");
  let actual = { ...baseline }, writes = 0; const proxied = [], upstreamURLs = [];
  const api = http.createServer((req, res) => { proxied.push(req.url); res.end("{}"); }); await listen(api);
  const backend = new WebSocketServer({ host: "127.0.0.1", port: 0 });
  await new Promise(resolve => backend.once("listening", resolve));
  backend.on("connection", (socket, req) => { upstreamURLs.push(req.url); socket.send("frame"); });
  const video = h264 ? new WebSocketServer({ host: "127.0.0.1", port: 0 }) : null;
  if (video) {
    await new Promise(resolve => video.once("listening", resolve));
    video.on("connection", (socket, req) => { upstreamURLs.push("video:" + req.url); socket.send("encoded-frame"); });
  }
  const control = createDisplayControl({ helper: { inspect: async () => actual,
    resize: async (desired, options) => { writes++; actual = { ...await (resize ? resize(desired, options) : desired), recoveryRequired: false }; return actual; } } });
  await control.initialize();
  const gateway = createGateway({ frontendDir: directory, displayControl: control, targets: {
    api: `http://127.0.0.1:${api.address().port}`, display: `ws://127.0.0.1:${backend.address().port}`,
    touchscreen: `ws://127.0.0.1:${backend.address().port}`,
    ...(video ? { h264: `ws://127.0.0.1:${video.address().port}` } : {}) } });
  await listen(gateway); const base = `http://127.0.0.1:${gateway.address().port}`;
  const sockets = [];
  t.after(async () => {
    for (const socket of sockets) socket.terminate(); for (const socket of backend.clients) socket.terminate();
    if (video) { for (const socket of video.clients) socket.terminate(); await new Promise(resolve => video.close(resolve)); }
    control.close(); await new Promise(resolve => gateway.close(resolve)); await new Promise(resolve => api.close(resolve));
    await new Promise(resolve => backend.close(resolve)); fs.rmSync(directory, { recursive: true, force: true });
  });
  const headers = { Origin: base, "Content-Type": "application/json" };
  const create = async () => { const response = await fetch(base + "/api/displaySession", { method: "POST", headers, body: "{}" });
    assert.equal(response.status, 200); const session = await response.json(); assert.equal(typeof session.token, "string"); return session; };
  const open = (session, channel = "display", query = "") => new Promise((resolve, reject) => {
    const socket = new WebSocket(base.replace("http:", "ws:") + `/sockets/${channel}?session=${session.token}${query}`, { origin: base });
    sockets.push(socket); socket.once("open", () => resolve(socket)); socket.once("error", reject);
  });
  const get = session => fetch(base + "/api/displayState", { headers: { "X-Display-Session": session.token } });
  const post = (session, payload, overrides = {}) => fetch(base + "/api/displayState", { method: "POST",
    headers: { ...headers, "X-Display-Session": session.token, "X-Display-CSRF": session.csrfToken, ...overrides }, body: JSON.stringify(payload) });
  return { base, headers, create, open, get, post, control, writes: () => writes, proxied, upstreamURLs };
}
test("display routes require a session and same-origin JSON; unsupported methods never proxy", async t => {
  const f = await fixture(t);
  assert.equal((await fetch(f.base + "/api/displaySession", { method: "POST", body: "{}" })).status, 403);
  assert.equal((await fetch(f.base + "/api/displaySession", { method: "POST", headers: { ...f.headers, Origin: "https://attacker.test" }, body: "{}" })).status, 403);
  assert.equal((await fetch(f.base + "/api/displaySession", { method: "POST", headers: { Origin: f.base }, body: "{}" })).status, 415);
  assert.equal((await fetch(f.base + "/api/displaySession", { method: "GET" })).status, 405);
  assert.equal((await fetch(f.base + "/api/displaySession", { method: "POST", headers: f.headers, body: "{}" + " ".repeat(4096) })).status, 413);
  assert.equal((await fetch(f.base + "/api/displaySession", { method: "POST", headers: f.headers, body: "{" })).status, 400);
  assert.equal((await fetch(f.base + "/api/displayState")).status, 403);
  assert.equal((await fetch(f.base + "/api/%64isplayState", { method: "POST", headers: f.headers, body: "{}" })).status, 400);
  assert.deepEqual(f.proxied, []);
});

test("optional H264 keeps display authority and strips format/session before the private backend", async t => {
  const f = await fixture(t, null, true), a = await f.create(), b = await f.create();
  assert.deepEqual((await (await f.get(a)).json()).streamFormats, ["png", "h264"]);
  await f.open(a, "display", "&format=h264"); await f.open(b);
  assert.deepEqual(f.upstreamURLs, ["video:/sockets/display", "/sockets/display"]);
  assert.equal((await (await f.get(a)).json()).canControl, true);
  await assert.rejects(f.open(b, "touchscreen", "&format=h264"), /403/);
  await assert.rejects(f.open(b, "display", "&format=unknown"), /403/);
  await assert.rejects(f.open(b, "display", "&format=h264&format=h264"), /403/);
  await assert.rejects(f.open(b, "display", "&format=h264&target=evil"), /403/);
  const disabled = await fixture(t);
  await assert.rejects(disabled.open(await disabled.create(), "display", "&format=h264"), /403/);
});
test("only owner can resize and touch; session secrets are removed before WS upstream", async t => {
  const f = await fixture(t), a = await f.create(), b = await f.create();
  await assert.rejects(f.open(b, "touchscreen"), /403/);
  await f.open(a); await f.open(b);
  assert.deepEqual(f.upstreamURLs, ["/sockets/display", "/sockets/display"]);
  const state = await (await f.get(a)).json(); assert.equal(state.canControl, true); assert.equal(state.density, 180);
  const desired = { ...state, width: 960, height: 640 };
  assert.equal((await f.post(b, desired)).status, 403);
  assert.equal((await f.post(a, desired, { "X-Display-CSRF": "bad" })).status, 403);
  assert.equal((await f.post(a, desired, { Origin: "https://attacker.test" })).status, 403);
  await assert.rejects(f.open(b, "touchscreen"), /403/); await f.open(a, "touchscreen");
  for (const invalid of [{ renderer: 1 }, { density: 200 }, { quality: 91 }, { isH264: 1 }, { refreshRate: 60 }, { isRearDisplayEnabled: 1 }, { evil: 1 }]) {
    assert.equal((await f.post(a, { ...desired, ...invalid })).status, 400);
  }
  const closed = new Promise(resolve => f.control.once("pause", resolve));
  const response = await f.post(a, desired); await closed; assert.equal(response.status, 200);
  const committed = await response.json(); assert.equal(committed.width, 960); assert.equal(committed.generation, 1);
  assert.equal((await f.post(a, desired)).status, 409); assert.equal(f.writes(), 1);
});
test("login revocation during an awaited resize rejects publication and old session", async t => {
  let finish; const f = await fixture(t, desired => new Promise(resolve => { finish = () => resolve(desired); }));
  const a = await f.create(); await f.open(a); const state = await (await f.get(a)).json();
  const response = f.post(a, { ...state, width: 960, height: 640 });
  while (!finish) await new Promise(resolve => setImmediate(resolve));
  f.control.revokeAll(); finish(); assert.equal((await response).status, 403);
  assert.equal((await f.get(a)).status, 403);
});
