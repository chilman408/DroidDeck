// SPDX-License-Identifier: GPL-3.0-only
const test = require("node:test"), assert = require("node:assert/strict");
const http = require("node:http");
const https = require("node:https"), fs = require("node:fs"), os = require("node:os"), path = require("node:path");
const { execFileSync } = require("node:child_process");
const { WebSocketServer } = require("ws");
const { probeHttp, probeTls, probeH264, hasAbstractListener, tlsTarget, readProbeConfiguration } = require("./runtime-probe");

test('portable probe metadata and TLS targets retain precise configured identity without credentials', () => {
  const raw = { user: 'deck', uid: 1200, gid: 1201, lanAddress: '10.20.30.40', port: 9443 };
  const configuration = readProbeConfiguration(JSON.stringify(raw));
  assert.deepEqual(tlsTarget('gateway', '192.168.240.112', configuration), {
    address: '192.168.240.112', port: 9443, certificateName: '10.20.30.40', certificateFile: '/home/deck/.config/droiddeck/cert.pem' });
  assert.equal(tlsTarget('lan-relay', undefined, configuration).address, '10.20.30.40');
  assert.equal(tlsTarget('gateway', '192.168.240.112').port, 8443);
  assert.equal(readProbeConfiguration(undefined), undefined);
  for (const value of ['{', '{"password":s3cr3t-value}', JSON.stringify({ ...raw, key: 'secret' }), 'x'.repeat(16385)]) {
    assert.throws(() => readProbeConfiguration(value), error => !/password|s3cr3t|secret/.test(error.message));
  }
  assert.throws(() => tlsTarget('gateway', '127.0.0.1', configuration));
});
test("API readiness requires bounded JSON health, not merely an open port", async t => {
  let body = '{"status":"ok"}';
  const server = http.createServer((req, res) => { assert.equal(req.url, "/api/health"); res.end(body); });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve)); t.after(() => new Promise(resolve => server.close(resolve)));
  const port = server.address().port; await probeHttp({ port });
  body = '{"status":"failed"}'; await assert.rejects(probeHttp({ port }), /not ready/);
  body = "x".repeat(2048); await assert.rejects(probeHttp({ port }), /Oversized/);
});
test("native capture readiness inspects its exact listening abstract socket without consuming it", () => {
  const line = "000000: 00000002 00000000 00010000 0001 01 123456 @scrcpy_54410001";
  assert.equal(hasAbstractListener(line, "scrcpy_54410001"), true);
  assert.equal(hasAbstractListener(line.replace("00010000", "00000000"), "scrcpy_54410001"), false);
  assert.equal(hasAbstractListener(line, "scrcpy_54410002"), false);
});
function packet(flag) { const data = Buffer.alloc(16); data[0] = flag; data.writeUInt32BE(4, 8); return data; }
test("H264 readiness requires a valid session, configuration and keyframe in order", async t => {
  const server = new WebSocketServer({ host: "127.0.0.1", port: 0 });
  await new Promise(resolve => server.once("listening", resolve)); t.after(() => new Promise(resolve => server.close(resolve)));
  server.on("connection", socket => {
    socket.send(JSON.stringify({ type: "scrcpy-session", width: 762, height: 720 }));
    socket.send(packet(0x40)); socket.send(packet(0x20));
  });
  await probeH264({ port: server.address().port });
});
test("metadata-only streams time out and release the probe viewer", async t => {
  const server = new WebSocketServer({ host: "127.0.0.1", port: 0 });
  await new Promise(resolve => server.once("listening", resolve)); t.after(() => new Promise(resolve => server.close(resolve)));
  server.on("connection", socket => socket.send(JSON.stringify({ type: "scrcpy-session", width: 762, height: 720 })));
  await assert.rejects(probeH264({ port: server.address().port, timeoutMs: 20 }), /not ready/);
});
test("already aborted probes cannot connect or leave timers running", async () => {
  const signal = AbortSignal.abort(new Error("cancelled"));
  assert.throws(() => probeH264({ signal }), /cancelled/); assert.throws(() => probeHttp({ signal }), /cancelled/);
});
test("gateway probe verifies both the pinned CA and LAN certificate identity across a namespace address", async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "ta-runtime-probe-"));
  t.after(() => {
    assert.equal(path.dirname(path.resolve(directory)), path.resolve(os.tmpdir()));
    assert.match(path.basename(directory), /^ta-runtime-probe-/);
    assert.equal(fs.lstatSync(directory).isDirectory(), true);
    fs.rmSync(directory, { recursive: true, force: true });
  });
  const config = path.join(directory, "openssl.cnf"), key = path.join(directory, "key.pem"), cert = path.join(directory, "cert.pem");
  fs.writeFileSync(config, "[req]\ndistinguished_name=dn\n[dn]\n");
  execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-config", config,
    "-keyout", key, "-out", cert, "-days", "1", "-subj", "/CN=TeslaAndroid", "-addext", "subjectAltName=IP:192.168.2.90"], { stdio: "ignore" });
  let status = 401; const hosts = [];
  const server = https.createServer({ key: fs.readFileSync(key), cert: fs.readFileSync(cert) }, (req, res) => {
    hosts.push(req.headers.host); res.writeHead(status).end();
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const options = { address: "127.0.0.1", port: server.address().port, ca: fs.readFileSync(cert) };
  await probeTls(options); assert.equal(hosts[0], '192.168.2.90:' + server.address().port);
  status = 200; await probeTls(options);
  status = 503; await assert.rejects(probeTls(options), /not ready/);
  await assert.rejects(probeTls({ ...options, certificateName: "192.168.2.91" }), /certificate|altnames/i);
  await assert.rejects(probeTls({ ...options, ca: undefined }), /self-signed|certificate/i);
});
