// SPDX-License-Identifier: GPL-3.0-only
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { after, before, test } = require("node:test");
const { WebSocket, WebSocketServer } = require("ws");
const { createGateway } = require("./server");

const frontendDir = fs.mkdtempSync(path.join(os.tmpdir(), "ta-gateway-"));
fs.writeFileSync(path.join(frontendDir, "index.html"), "<!doctype html><title>Preview</title>");
fs.mkdirSync(path.join(frontendDir, "js"));
fs.writeFileSync(path.join(frontendDir, "js", "app.js"), "window.preview = true;");
fs.mkdirSync(path.join(frontendDir, ".git"));
fs.writeFileSync(path.join(frontendDir, ".git", "config"), "secret repository metadata");
fs.writeFileSync(path.join(frontendDir, "README.md"), "not a public asset");

const api = http.createServer((req, res) => {
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify({ path: req.url, method: req.method }));
});
let display;
let touch;
let gateway;
let base;

function listen(server) {
  return new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
}

function socketMessage(url) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(url, { origin: base });
    socket.once("error", reject);
    socket.once("message", (data, isBinary) => {
      socket.close();
      resolve({ data, isBinary });
    });
    socket.once("open", () => {
      if (url.endsWith("touchscreen")) socket.send("0,100,200,1");
    });
  });
}

before(async () => {
  await listen(api);
  display = new WebSocketServer({ host: "127.0.0.1", port: 0 });
  touch = new WebSocketServer({ host: "127.0.0.1", port: 0 });
  display.on("connection", (socket) => socket.send(Buffer.from([0x89, 0x50, 0x4e, 0x47])));
  touch.on("connection", (socket) => socket.on("message", (data) => socket.send(data.toString())));
  await Promise.all([display, touch].map((server) =>
    new Promise((resolve) => server.once("listening", resolve))));
  gateway = createGateway({
    frontendDir,
    targets: {
      api: `http://127.0.0.1:${api.address().port}`,
      display: `ws://127.0.0.1:${display.address().port}`,
      touchscreen: `ws://127.0.0.1:${touch.address().port}`,
    },
  });
  await listen(gateway);
  base = `http://127.0.0.1:${gateway.address().port}`;
});

after(async () => {
  await Promise.all([
    new Promise((resolve) => gateway.close(resolve)),
    new Promise((resolve) => api.close(resolve)),
    new Promise((resolve) => display.close(resolve)),
    new Promise((resolve) => touch.close(resolve)),
  ]);
  fs.rmSync(frontendDir, { recursive: true, force: true });
});

test("serves static frontend, without directory listing", async () => {
  assert.equal(gateway.address().address, "127.0.0.1");
  const root = await fetch(base);
  assert.equal(root.status, 200);
  assert.match(await root.text(), /Preview/);
  assert.equal(root.headers.get("cache-control"), "no-store");
  assert.match(await (await fetch(base + "/js/app.js")).text(), /preview = true/);
  assert.equal((await fetch(base + "/js/")).status, 404);
  assert.equal((await fetch(base + "/.git/config")).status, 404);
  assert.equal((await fetch(base + "/%2egit/config")).status, 404);
  assert.equal((await fetch(base + "/README.md")).status, 404);
});

test("proxies API path and rejects foreign Origin and Host", async () => {
  const response = await fetch(base + "/api/health?live=1");
  assert.deepEqual(await response.json(), { path: "/api/health?live=1", method: "GET" });
  assert.equal((await fetch(base + "/api/health", {
    method: "POST", headers: { Origin: "https://attacker.test" },
  })).status, 403);
  const hostileHost = await new Promise((resolve, reject) => {
    http.get(base, { headers: { Host: "attacker.test" } }, (res) => {
      res.resume();
      resolve(res.statusCode);
    }).on("error", reject);
  });
  assert.equal(hostileHost, 403);
});

test('running gateway release identity is static, uncached, read-only and exact-origin', async () => {
  const response = await fetch(base + '/api/runtimeRelease');
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.equal(response.headers.get('content-type'), 'application/json');
  assert.deepEqual(await response.json(), { kind: 'droiddeck-runtime-release', version: require('./release-version.json').version, updateProtocol: 1 });
  assert.equal((await fetch(base + '/api/runtimeRelease?diagnostic=1')).status, 400);
  assert.equal((await fetch(base + '/api/runtimeRelease', { headers: { Origin: 'https://foreign.example' } })).status, 403);
  const denied = await fetch(base + '/api/runtimeRelease', { method: 'POST', body: 'restart' });
  assert.equal(denied.status, 405);
  assert.equal(denied.headers.get('allow'), 'GET');
});

test("proxies binary display and text touch WebSockets", async () => {
  const displayFrame = await socketMessage(base.replace("http:", "ws:") + "/sockets/display");
  assert.equal(displayFrame.isBinary, true);
  assert.deepEqual([...displayFrame.data], [0x89, 0x50, 0x4e, 0x47]);
  const touchReply = await socketMessage(base.replace("http:", "ws:") + "/sockets/touchscreen");
  assert.equal(touchReply.isBinary, false);
  assert.equal(touchReply.data.toString(), "0,100,200,1");
});

test("rejects unsupported channels and cross-origin touch upgrades", async () => {
  for (const channel of ["audio", "gps"]) {
    await assert.rejects(socketMessage(base.replace("http:", "ws:") + `/sockets/${channel}`),
      /Unexpected server response: 404/);
  }
  await assert.rejects(new Promise((resolve, reject) => {
    const socket = new WebSocket(base.replace("http:", "ws:") + "/sockets/touchscreen",
      { origin: "https://attacker.test" });
    socket.once("open", resolve);
    socket.once("error", reject);
  }), /Unexpected server response: 403/);
  assert.equal((await fetch(base + "/sockets/touchscreen")).status, 426);
});

test("refuses non-loopback backend targets", () => {
  assert.throws(() => createGateway({ frontendDir, targets: {
    api: "http://192.168.2.125:8081",
    display: "ws://127.0.0.1:19091",
    touchscreen: "ws://127.0.0.1:19999",
  } }), /backend must/);
});
