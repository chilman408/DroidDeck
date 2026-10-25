// SPDX-License-Identifier: GPL-3.0-only
const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const https = require("node:https");
const os = require("node:os");
const path = require("node:path");
const { after, before, test } = require("node:test");
const { WebSocket, WebSocketServer } = require("ws");
const { createGateway, readProxyOrigin } = require("./server");
const { createBrowserAccess } = require("./browser-access");

const tempRoot = path.resolve(os.tmpdir());
const fixture = fs.mkdtempSync(path.join(tempRoot, "ta-secure-gateway-"));
fs.writeFileSync(path.join(fixture, "index.html"), "<!doctype html><title>Private preview</title>");
fs.writeFileSync(path.join(fixture, "openssl.cnf"), "[req]\ndistinguished_name=dn\n[dn]\n");
execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes",
  "-config", path.join(fixture, "openssl.cnf"),
  "-keyout", path.join(fixture, "key.pem"), "-out", path.join(fixture, "cert.pem"),
  "-days", "1", "-subj", "/CN=localhost", "-addext", "subjectAltName=IP:127.0.0.1"],
{ stdio: "ignore" });
const tls = {
  key: fs.readFileSync(path.join(fixture, "key.pem")),
  cert: fs.readFileSync(path.join(fixture, "cert.pem")),
};
const publicOrigin = "https://192.168.2.90:8443";
const auth = { username: "tesla", password: "test-only-password-with-at-least-32-bytes" };
const authorization = "Basic " + Buffer.from(`${auth.username}:${auth.password}`).toString("base64");
let api, display, gateway, base, options;

function request(urlPath, headers = {}, address = base) {
  return new Promise((resolve, reject) => {
    https.get(address + urlPath, { rejectUnauthorized: false,
      headers: { Host: "192.168.2.90:8443", Authorization: authorization, ...headers } }, (res) => {
      const chunks = [];
      res.on("data", (chunk) => chunks.push(chunk));
      res.on("end", () => resolve({ status: res.statusCode, headers: res.headers,
        body: Buffer.concat(chunks).toString() }));
    }).on("error", reject);
  });
}

function frame(headers = {}, origin = publicOrigin, channel = "display", address = base) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(address.replace("https:", "wss:") + `/sockets/${channel}`, {
      rejectUnauthorized: false, origin,
      headers: { Host: "192.168.2.90:8443", Authorization: authorization, ...headers },
    });
    socket.once("error", reject);
    socket.once("message", (data) => { socket.close(); resolve(data); });
  });
}

before(async () => {
  api = http.createServer((req, res) => {
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ authorization: req.headers.authorization || null, path: req.url }));
  });
  await new Promise((resolve) => api.listen(0, "127.0.0.1", resolve));
  display = new WebSocketServer({ host: "127.0.0.1", port: 0 });
  display.on("connection", (socket, req) => {
    assert.equal(req.headers.authorization, undefined);
    socket.send(Buffer.from("private-frame"));
  });
  await new Promise((resolve) => display.once("listening", resolve));
  options = { frontendDir: fixture, publicOrigin, auth, tls, targets: {
    api: `http://127.0.0.1:${api.address().port}`,
    display: `ws://127.0.0.1:${display.address().port}`,
    touchscreen: `ws://127.0.0.1:${display.address().port}`,
  } };
  gateway = createGateway(options);
  await new Promise((resolve) => gateway.listen(0, "127.0.0.1", resolve));
  base = `https://127.0.0.1:${gateway.address().port}`;
});

after(async () => {
  await Promise.all([gateway, api, display].filter(Boolean).map((server) =>
    new Promise((resolve) => server.close(resolve))));
  assert.ok(path.resolve(fixture).startsWith(tempRoot + path.sep));
  assert.ok(path.basename(fixture).startsWith("ta-secure-gateway-"));
  fs.rmSync(fixture, { recursive: true, force: true });
});

test("LAN mode refuses plaintext, missing TLS, and weak explicit credentials", () => {
  assert.throws(() => createGateway({ ...options, publicOrigin: "http://192.168.2.90:8443" }), /HTTPS/);
  assert.throws(() => createGateway({ ...options, tls: undefined }), /TLS/);
  assert.throws(() => createGateway({ ...options, auth: { username: "tesla", password: "short" } }), /credentials/);
  assert.throws(() => createGateway({ ...options, publicOrigin: publicOrigin + "/another-path" }), /origin/);
});

test("requires authentication on frontend and API without caching the challenge", async () => {
  for (const urlPath of ["/", "/api/health"]) {
    const denied = await request(urlPath, { Authorization: "" });
    assert.equal(denied.status, 401);
    assert.match(denied.headers["www-authenticate"], /^Basic /);
    assert.equal(denied.headers["cache-control"], "no-store");
    assert.equal((await request(urlPath, { Authorization: "Basic !!!" })).status, 401);
  }
  const allowed = await request("/");
  assert.equal(allowed.status, 200);
  assert.match(allowed.body, /Private preview/);
});

test('static release identity needs no login but exposes no protected API or account state', async () => {
  const version = await request('/api/runtimeRelease', { Authorization: '' });
  assert.equal(version.status, 200);
  assert.equal(version.headers['cache-control'], 'no-store');
  assert.deepEqual(JSON.parse(version.body), { kind: 'droiddeck-runtime-release', version: require('./release-version.json').version, updateProtocol: 1 });
  assert.equal((await request('/api/runtimeRelease', { Authorization: '', Host: 'attacker.test' })).status, 403);
  assert.equal((await request('/api/runtimeRelease', { Authorization: '', Origin: 'https://attacker.test' })).status, 403);
  assert.equal((await request('/', { Authorization: '' })).status, 401);
  assert.equal((await request('/api/health', { Authorization: '' })).status, 401);
});

test("rejects hostile Host and API Origin and strips credentials upstream", async () => {
  assert.equal((await request("/", { Host: "attacker.test" })).status, 403);
  assert.equal((await request("/api/health", { Origin: "https://attacker.test" })).status, 403);
  const response = await request("/api/health", { Origin: publicOrigin });
  assert.equal(response.status, 200);
  assert.deepEqual(JSON.parse(response.body), { authorization: null, path: "/api/health" });
});

test("protects WebSocket authentication and origin, and streams allowed frames", async () => {
  await assert.rejects(frame({ Authorization: "" }), /Unexpected server response: 401/);
  await assert.rejects(frame({}, "https://attacker.test"), /Unexpected server response: 403/);
  await assert.rejects(frame({}, publicOrigin, "audio"), /Unexpected server response: 404/);
  assert.equal((await frame()).toString(), "private-frame");
});

test("browser login defaults off, can be enabled with chosen credentials, and persists", async () => {
  const accessFile = path.join(fixture, "browser-access.json");
  const dynamicOptions = { ...options, auth: undefined, accessFile };
  let server = createGateway(dynamicOptions);
  const start = async () => {
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    return `https://127.0.0.1:${server.address().port}`;
  };
  let address = await start();
  const chosen = { username: "hilman", password: "user-selected-test-password" };
  const basic = (credentials) => "Basic " + Buffer.from(`${credentials.username}:${credentials.password}`).toString("base64");
  const call = (route, { headers = {}, method = "GET", body } = {}) => new Promise((resolve, reject) => {
    const req = https.request(address + route, { method, rejectUnauthorized: false,
      headers: { Host: "192.168.2.90:8443", ...headers } }, (res) => {
      let text = "";
      res.on("data", (data) => { text += data; });
      res.on("end", () => resolve({ status: res.statusCode, text, headers: res.headers }));
    });
    req.on("error", reject);
    req.end(body);
  });
  const ws = (headers = {}) => new WebSocket(address.replace("https:", "wss:") + "/sockets/display", {
    origin: publicOrigin, rejectUnauthorized: false, headers: { Host: "192.168.2.90:8443", ...headers },
  });
  const update = (payload, csrfToken, authorization, extraHeaders = {}) => call("/api/browserAccess", {
    method: "POST", body: JSON.stringify(payload), headers: {
      "Content-Type": "application/json", Origin: publicOrigin, "X-CSRF-Token": csrfToken,
      ...(authorization ? { Authorization: authorization } : {}), ...extraHeaders,
    },
  });
  try {
    assert.equal((await call("/")).status, 200);
    assert.equal((await call("/api/health")).status, 200);
    let state = JSON.parse((await call("/api/browserAccess")).text);
    assert.equal(state.enabled, false);
    assert.equal(state.username, "");
    assert.ok(state.csrfToken);
    assert.equal((await update({ enabled: true, ...chosen }, state.csrfToken, null,
      { Origin: "https://attacker.test" })).status, 403);
    assert.equal((await update({ enabled: true, ...chosen }, "bad-token")).status, 403);
    assert.equal((await update({ enabled: true, username: "hilman", password: "short" }, state.csrfToken)).status, 400);
    assert.equal((await call("/")).status, 200);
    const socket = ws();
    await new Promise((resolve, reject) => { socket.once("message", resolve); socket.once("error", reject); });
    const revoked = new Promise((resolve) => socket.once("close", resolve));
    const enabled = await update({ enabled: true, ...chosen }, state.csrfToken);
    assert.equal(enabled.status, 200);
    await revoked;
    assert.equal((await call("/")).status, 401);
    assert.equal((await call("/api/browserAccess")).status, 401);
    assert.equal((await call("/api/health", { headers: { Authorization: basic({ ...chosen, password: "wrong-password" }) } })).status, 401);
    const persisted = fs.readFileSync(accessFile, "utf8");
    assert.ok(!persisted.includes(chosen.password));
    assert.ok(!enabled.text.includes(chosen.password));
    assert.equal((await call("/", { headers: { Authorization: basic(chosen) } })).status, 200);
    await assert.rejects(new Promise((resolve, reject) => {
      const denied = ws(); denied.once("open", () => { denied.close(); resolve(); }); denied.once("error", reject);
    }), /401/);
    await new Promise((resolve) => server.close(resolve));
    server = createGateway(dynamicOptions);
    address = await start();
    assert.equal((await call("/")).status, 401);
    const concurrent = await Promise.all(Array.from({ length: 8 }, () => call("/", {
      headers: { Authorization: basic(chosen) },
    })));
    assert.deepEqual(concurrent.map((response) => response.status), Array(8).fill(200));
    state = JSON.parse((await call("/api/browserAccess", { headers: { Authorization: basic(chosen) } })).text);
    assert.equal(state.enabled, true);
    assert.equal(state.username, "hilman");
    assert.equal((await update({ enabled: false }, state.csrfToken)).status, 401);
    const newCredentials = { username: "driver", password: "another-chosen-test-password" };
    assert.equal((await update({ enabled: true, ...newCredentials }, state.csrfToken, basic(chosen))).status, 200);
    assert.equal((await call("/", { headers: { Authorization: basic(chosen) } })).status, 401);
    const rotatedRequests = await Promise.all(Array.from({ length: 8 }, () => call("/", {
      headers: { Authorization: basic(newCredentials) },
    })));
    assert.deepEqual(rotatedRequests.map((response) => response.status), Array(8).fill(200));
    state = JSON.parse((await call("/api/browserAccess", { headers: { Authorization: basic(newCredentials) } })).text);
    assert.equal(state.username, "driver");
    assert.equal((await update({ enabled: false }, state.csrfToken, basic(newCredentials))).status, 200);
    assert.equal((await call("/")).status, 200);
    await new Promise((resolve) => server.close(resolve));
    server = createGateway(dynamicOptions);
    address = await start();
    assert.equal((await call("/")).status, 200);
    assert.equal(JSON.parse((await call("/api/browserAccess")).text).enabled, false);
    assert.ok(!fs.readFileSync(accessFile, "utf8").includes(chosen.username));
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test("verification saturation reports retryable overload instead of invalid credentials", async () => {
  const store = createBrowserAccess(path.join(fixture, "overload-access.json"));
  await store.update({ enabled: true, username: "driver", password: "test-selected-password" }, store.snapshot().csrfToken);
  const header = (password) => "Basic " + Buffer.from(`driver:${password}`).toString("base64");
  const pending = Array.from({ length: 4 }, (_, index) => store.authorized(header(`incorrect-${index}`)));
  await assert.rejects(store.authorized(header("test-selected-password")), (error) => error.status === 503);
  assert.deepEqual(await Promise.all(pending), [false, false, false, false]);
  assert.equal(await store.authorized(header("test-selected-password")), true);
});

test("corrupt saved login state fails closed rather than silently disabling authentication", () => {
  const filename = path.join(fixture, "corrupt-access.json");
  fs.writeFileSync(filename, '{"version":1,"enabled":true}', { mode: 0o600 });
  assert.throws(() => createGateway({ ...options, auth: undefined, accessFile: filename }), /Invalid browser access/);
});

const proxyOrigin = "https://teslavm.mondriandsm.synology.me";
const proxyHost = new URL(proxyOrigin).host;

test("explicit proxy hostname accepts authenticated HTTPS and WebSockets alongside LAN", async () => {
  const server = createGateway({ ...options, proxyOrigin });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = `https://127.0.0.1:${server.address().port}`;
  try {
    assert.equal((await request("/", { Host: proxyHost }, address)).status, 200);
    assert.equal((await request("/api/health", { Host: proxyHost, Origin: proxyOrigin }, address)).status, 200);
    assert.equal((await frame({ Host: proxyHost }, proxyOrigin, "display", address)).toString(), "private-frame");
    assert.equal((await request("/", {}, address)).status, 200);
    assert.equal((await frame({}, publicOrigin, "display", address)).toString(), "private-frame");
    assert.equal((await request("/", { Host: proxyHost, Authorization: "" }, address)).status, 401);
    await assert.rejects(frame({ Host: proxyHost, Authorization: "" }, proxyOrigin, "display", address), /401/);
  } finally { await new Promise(resolve => server.close(resolve)); }
});

test("proxy hostname does not trust forwarded hosts or cross-origin LAN requests", async () => {
  const server = createGateway({ ...options, proxyOrigin });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = `https://127.0.0.1:${server.address().port}`;
  try {
    assert.equal((await request("/", { Host: "attacker.test", "X-Forwarded-Host": proxyHost }, address)).status, 403);
    assert.equal((await request("/api/health", { Host: proxyHost, Origin: publicOrigin }, address)).status, 403);
    assert.equal((await request("/api/health", { Origin: proxyOrigin }, address)).status, 403);
    await assert.rejects(frame({ Host: proxyHost }, publicOrigin, "display", address), /403/);
    await assert.rejects(frame({}, proxyOrigin, "display", address), /403/);
  } finally { await new Promise(resolve => server.close(resolve)); }
});

test("public proxy follows optional browser login, including after disabling it", async () => {
  const accessFile = path.join(fixture, "proxy-access.json");
  const store = createBrowserAccess(accessFile);
  const server = createGateway({ ...options, auth: undefined, accessFile, proxyOrigin });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = `https://127.0.0.1:${server.address().port}`;
  try {
    assert.equal((await request("/", { Authorization: "" }, address)).status, 200);
    assert.equal((await request("/", { Host: proxyHost, Authorization: "" }, address)).status, 200);
    assert.equal((await frame({ Host: proxyHost, Authorization: "" }, proxyOrigin, "display", address)).toString(), "private-frame");
    assert.equal((await request("/api/health", { Host: proxyHost, Origin: proxyOrigin, Authorization: "" }, address)).status, 200);
    // Configure a fresh gateway from a persisted enabled login, never live user credentials.
  } finally { await new Promise(resolve => server.close(resolve)); }
  await store.update({ enabled: true, ...auth }, store.snapshot().csrfToken);
  const enabled = createGateway({ ...options, auth: undefined, accessFile, proxyOrigin });
  await new Promise(resolve => enabled.listen(0, "127.0.0.1", resolve));
  const next = `https://127.0.0.1:${enabled.address().port}`;
  let socket;
  try {
    assert.equal((await request("/", { Host: proxyHost, Authorization: "" }, next)).status, 401);
    await assert.rejects(frame({ Host: proxyHost, Authorization: "" }, proxyOrigin, "display", next), /401/);
    assert.equal((await request("/", { Host: proxyHost }, next)).status, 200);
    const state = JSON.parse((await request("/api/browserAccess", { Host: proxyHost, Origin: proxyOrigin }, next)).body);
    socket = new WebSocket(next.replace("https:", "wss:") + "/sockets/display", { rejectUnauthorized: false,
      origin: proxyOrigin, headers: { Host: proxyHost, Authorization: authorization } });
    await new Promise((resolve, reject) => { socket.once("message", resolve); socket.once("error", reject); });
    const closed = new Promise(resolve => socket.once("close", resolve));
    const status = await new Promise((resolve, reject) => {
      const req = https.request(next + "/api/browserAccess", { method: "POST", rejectUnauthorized: false,
        headers: { Host: proxyHost, Origin: proxyOrigin, Authorization: authorization,
          "Content-Type": "application/json", "X-CSRF-Token": state.csrfToken } }, res => {
        res.resume(); res.once("end", () => resolve(res.statusCode));
      }); req.on("error", reject); req.end(JSON.stringify({ enabled: false }));
    });
    assert.equal(status, 200); await closed;
    assert.equal((await request("/", { Host: proxyHost, Authorization: "" }, next)).status, 200);
    assert.equal((await frame({ Host: proxyHost, Authorization: "" }, proxyOrigin, "display", next)).toString(), "private-frame");
    assert.equal((await request("/", { Authorization: "" }, next)).status, 200);
  } finally { socket?.terminate(); await new Promise(resolve => enabled.close(resolve)); }
});

test("proxy configuration rejects plaintext, paths, credentials, wildcards and missing primary TLS", () => {
  for (const value of ["http://tesla.test", "https://tesla.test/", "https://user:pass@tesla.test", "https://*.tesla.test",
    "https://192.168.2.90:8443", [proxyOrigin], ""]) {
    assert.throws(() => createGateway({ ...options, proxyOrigin: value }), /proxy/i);
  }
  assert.throws(() => createGateway({ frontendDir: fixture, targets: options.targets, proxyOrigin }), /proxy/i);
});

test("proxy origin file is optional but refuses symlinks, oversized data and invalid origins", () => {
  const filename = path.join(fixture, "proxy-origin");
  assert.equal(readProxyOrigin(filename), undefined);
  fs.writeFileSync(filename, proxyOrigin + "\n", { mode: 0o644 });
  // POSIX ownership checks are exercised by the root Linux acceptance run.
  if (process.platform !== "win32" && process.getuid() !== 0) {
    assert.throws(() => readProxyOrigin(filename), /root-owned/);
    return;
  }
  assert.equal(readProxyOrigin(filename), proxyOrigin);
  fs.writeFileSync(filename, "http://attacker.test\n");
  assert.throws(() => readProxyOrigin(filename), /Proxy origin/);
  fs.writeFileSync(filename, "x".repeat(257));
  assert.throws(() => readProxyOrigin(filename), /regular file/);
  fs.unlinkSync(filename);
  fs.mkdirSync(filename);
  assert.throws(() => readProxyOrigin(filename), /regular file/);
  fs.rmdirSync(filename);
  if (process.platform !== "win32") {
    fs.symlinkSync(path.join(fixture, "index.html"), filename);
    assert.throws(() => readProxyOrigin(filename), /regular file/);
    fs.unlinkSync(filename);
    fs.writeFileSync(filename, proxyOrigin, { mode: 0o666 });
    fs.chmodSync(filename, 0o666);
    assert.throws(() => readProxyOrigin(filename), /write access/);
  }
});
