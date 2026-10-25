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
const listen = s => new Promise(r => s.listen(0, "127.0.0.1", r));
for (const audioPcmGain of [false, true]) test("audio routes preserve configuration, owner CSRF and stream gates with gain=" + audioPcmGain, async t => {
  assert.ok(fs.existsSync(__dirname + "/audio-settings.js"), "audio settings not implemented");
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "ta-audio-routes-")); fs.writeFileSync(path.join(directory, "index.html"), "test");
  assert.throws(() => createGateway({ frontendDir: directory, audioPcmGain: true }), /audio backend/);
  assert.throws(() => createGateway({ frontendDir: directory, audioPcmGain: '1' }), /audio backend/);
  const audioSettings = require("./audio-settings").createAudioSettings(path.join(directory, "audio.json"));
  const api = http.createServer((_req, res) => res.end(JSON.stringify({ unchanged: 42 }))); await listen(api);
  const backend = new WebSocketServer({ host: "127.0.0.1", port: 0 }); await once(backend, "listening");
  const paths = []; backend.on("connection", (client, req) => { paths.push(req.url); client.send("audio bytes"); });
  const expiry = [];
  const display = createDisplayControl({ clock: { now: Date.now, setTimeout(fn) { expiry.push(fn); return fn; }, clearTimeout() {} }, helper: { inspect: async () => ({ width: 976, height: 720, density: 180,
    resolutionPreset: 1, isResponsive: 1, recoveryRequired: false }) } }); await display.initialize();
  const gateway = createGateway({ frontendDir: directory, displayControl: display, audioSettings, audioPcmGain, targets: {
    api: `http://127.0.0.1:${api.address().port}`, display: `ws://127.0.0.1:${backend.address().port}`,
    touchscreen: `ws://127.0.0.1:${backend.address().port}`, audio: `ws://127.0.0.1:${backend.address().port}` } }); await listen(gateway);
  const base = `http://127.0.0.1:${gateway.address().port}`, clients = [];
  t.after(async () => { for (const c of clients) c.terminate(); for (const c of backend.clients) c.terminate();
    await new Promise(r => gateway.close(r)); await new Promise(r => backend.close(r)); await new Promise(r => api.close(r));
    fs.rmSync(directory, { recursive: true, force: true }); });
  const config = await (await fetch(base + "/api/configuration")).json();
  assert.equal(config.unchanged, 42); assert.equal(config.browserAudioSupported, 1);
  assert.equal(config.browserAudioGainSupported, audioPcmGain ? 1 : 0);
  const owner = display.createSession(), viewer = display.createSession(); display.connect(owner.token, "display");
  const post = (route, value, s = owner, overrides = {}) => fetch(base + "/api/" + route, { method: "POST", body: String(value),
    headers: { Origin: base, "Content-Type": "text/plain", "X-Display-Session": s.token, "X-Display-CSRF": s.csrfToken, ...overrides } });
  assert.equal((await post("browserAudioVolume", 50, viewer)).status, 403);
  assert.equal((await post("browserAudioVolume", 50, owner, { "X-Display-CSRF": "wrong" })).status, 403);
  assert.equal((await post("browserAudioVolume", 50, owner, { Origin: "https://evil.test" })).status, 403);
  assert.equal((await post("browserAudioVolume", 151)).status, 400);
  assert.equal((await post("%62rowserAudioVolume", 50)).status, 400);
  assert.equal((await post("browserAudioVolume", 50, owner, { "Content-Type": "application/json" })).status, 415);
  assert.equal((await post("browserAudioVolume", "1".repeat(9))).status, 413);
  assert.equal((await fetch(base + "/api/browserAudioVolume")).status, 405);
  assert.equal((await post("browserAudioVolume", 50)).status, 200);
  assert.equal(audioSettings.snapshot().volume, 50);
  const open = (s, query = "", origin = base) => new Promise((resolve, reject) => {
    const client = new WebSocket(base.replace("http:", "ws:") + "/sockets/audio?session=" + s.token + query, { origin });
    clients.push(client); client.once("open", () => resolve(client)); client.once("error", reject);
  });
  await assert.rejects(open(viewer, "&format=h264"), /403/);
  await assert.rejects(open(viewer, "", "https://evil.test"), /403/);
  const client = await open(viewer); assert.deepEqual(paths, ["/sockets/audio"]);
  const closed = once(client, "close"); assert.equal((await post("browserAudioState", 0)).status, 200); await closed;
  await assert.rejects(open(viewer), /503/);
  assert.equal((await post("browserAudioState", 1)).status, 200);
  const reopened = await open(viewer), expired = once(reopened, "close"); expiry[1]();
  await Promise.race([expired, new Promise((_r, reject) => setTimeout(() => reject(new Error("Expired audio remained open")), 100))]);
  await assert.rejects(open(viewer), /403/);
  const nextViewer = display.createSession(), next = await open(nextViewer), revoked = once(next, "close"); display.revokeAll(); await revoked;
  await assert.rejects(open(nextViewer), /403/);
});
