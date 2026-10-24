// SPDX-License-Identifier: GPL-3.0-only
const fs = require("node:fs/promises");
const http = require("node:http"), net = require("node:net");
const https = require("node:https"), tls = require("node:tls");
const { WebSocket } = require("ws");
const { normalizeRuntimeConfig, deriveRuntimeLayout } = require('../portable/runtime-config');
const { readBoundedRegular } = require('../portable/read-bounded');
function readProbeConfiguration(value) {
  if (value === undefined) return undefined;
  try {
    if (typeof value !== 'string' || Buffer.byteLength(value) > 16384) throw new Error();
    return normalizeRuntimeConfig(JSON.parse(value));
  } catch (_) { throw new Error('Invalid portable probe configuration'); }
}
function tlsTarget(name, containerAddress, configuration) {
  if (!['gateway', 'lan-relay'].includes(name)) throw new Error('Unknown TLS probe role');
  if (name === 'gateway' && (typeof containerAddress !== 'string' || !/^192\.168\.240\.(?:[2-9]|[1-9]\d|1\d\d|2[0-4]\d|25[0-4])$/.test(containerAddress))) throw new Error('Invalid gateway address');
  const config = configuration === undefined ? undefined : normalizeRuntimeConfig(configuration);
  const layout = config ? deriveRuntimeLayout(config) : null;
  const certificateName = config?.lanAddress || '192.168.2.90';
  return { address: name === 'gateway' ? containerAddress : certificateName, port: config?.port || 8443,
    certificateName, certificateFile: layout?.certificateFile || '/home/hilman/.config/teslaandroid-waydroid/cert.pem' };
}
function probeHttp({ port = 8081, signal, timeoutMs = 2000 } = {}) {
  signal?.throwIfAborted();
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = error => {
      if (settled) return; settled = true; clearTimeout(timer); request.destroy();
      if (error) reject(error); else resolve();
    };
    const request = http.get({ hostname: "127.0.0.1", port, path: "/api/health", signal }, response => {
      let body = "";
      response.on("data", chunk => {
        body += chunk; if (body.length > 1024) finish(new Error("Oversized runtime health response"));
      });
      response.once("end", () => {
        if (settled) return;
        try { if (response.statusCode !== 200 || JSON.parse(body).status !== "ok") throw new Error("API is not ready"); finish(); }
        catch (error) { finish(error); }
      });
      response.once("error", finish);
      response.once("close", () => { if (!settled) finish(new Error("API probe disconnected")); });
    });
    const timer = setTimeout(() => finish(new Error("Runtime probe timed out")), timeoutMs);
    request.once("error", finish);
  });
}
function probeTcp(port, { signal, timeoutMs = 2000 } = {}) {
  signal?.throwIfAborted();
  return new Promise((resolve, reject) => {
    const socket = net.connect({ host: "127.0.0.1", port, signal });
    const timer = setTimeout(() => socket.destroy(new Error("Runtime probe timed out")), timeoutMs);
    socket.once("connect", () => { socket.destroy(); resolve(); });
    socket.once("error", reject); socket.once("close", () => clearTimeout(timer));
  });
}
function probeTls({ address, ca, port = 8443, certificateName = "192.168.2.90", signal, timeoutMs = 2000 } = {}) {
  signal?.throwIfAborted();
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = error => {
      if (settled) return; settled = true; clearTimeout(timer); request.destroy();
      if (error) reject(error); else resolve();
    };
    const request = https.get({ hostname: address, port, ca, signal, agent: false,
      headers: { Host: certificateName + ':' + port },
      checkServerIdentity: (_name, cert) => tls.checkServerIdentity(certificateName, cert),
    }, response => finish([200, 401].includes(response.statusCode) ? null : new Error("Gateway is not ready")));
    const timer = setTimeout(() => finish(new Error("Runtime TLS probe timed out")), timeoutMs);
    request.once("error", finish);
  });
}
function hasAbstractListener(text, address) {
  return text.split(/\r?\n/).some(line => {
    const fields = line.trim().split(/\s+/);
    return fields.length === 8 && fields[3] === "00010000" && fields[4] === "0001" && fields[7] === "@" + address;
  });
}
function probeH264({ port = 9092, signal, timeoutMs = 3000 } = {}) {
  signal?.throwIfAborted();
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(`ws://127.0.0.1:${port}/`, { maxPayload: 2 * 1024 * 1024 + 12 });
    let settled = false, session = false, configuration = false;
    const finish = error => {
      if (settled) return; settled = true; clearTimeout(timer); signal?.removeEventListener("abort", abort);
      socket.terminate(); if (error) reject(error); else resolve();
    };
    const abort = () => finish(new Error("Runtime probe cancelled"));
    const timer = setTimeout(() => finish(new Error("H264 is not ready")), timeoutMs);
    signal?.addEventListener("abort", abort, { once: true }); if (signal?.aborted) abort();
    socket.on("error", () => finish(new Error("H264 probe disconnected")));
    socket.once("close", () => finish(new Error("H264 probe disconnected")));
    socket.on("message", (data, binary) => {
      try {
        if (!binary) {
          if (data.length > 1024) throw new Error("Invalid H264 metadata");
          const value = JSON.parse(data.toString());
          if (value.type !== "scrcpy-session" || ![value.width, value.height].every(v =>
            Number.isInteger(v) && v >= 96 && v <= 1920 && v % 2 === 0) || value.width * value.height > 1600000) {
            throw new Error("Invalid H264 metadata");
          }
          session = true; configuration = false;
        } else {
          if (!session || data.length < 13 || data.readUInt32BE(8) !== data.length - 12) throw new Error("Invalid H264 packet");
          if (data[0] & 0x40) configuration = true;
          else if (configuration && (data[0] & 0x20)) finish();
        }
      } catch (error) { finish(error); }
    });
  });
}
async function main() {
  const name = process.argv[2];
  if (process.argv.length !== (name === "gateway" ? 4 : 3)) throw new Error("A fixed runtime probe is required");
  const configuration = readProbeConfiguration(process.env.TESLA_WAYDROID_PORTABLE_PROBE_CONFIG);
  switch (name) {
    case "api": await probeHttp(); break;
    case "display": await probeTcp(9091); break;
    case "video-relay": await probeH264(); break;
    case "audio-relay": await probeTcp(9093); break;
    case "gateway": case "lan-relay": {
      const target = tlsTarget(name, process.argv[3], configuration);
      await probeTls({ ...target, ca: readBoundedRegular(target.certificateFile, 65536) }); break;
    }
    case "video": case "audio": {
      const socket = name === "video" ? "scrcpy_54410001" : "scrcpy_54410002";
      if (!hasAbstractListener(await fs.readFile("/proc/net/unix", "utf8"), socket)) throw new Error("Native capture listener is not ready");
      break;
    }
    default: throw new Error("Unknown runtime probe");
  }
  console.log("READY");
}
if (require.main === module) main().catch(() => { console.error("Runtime worker not ready"); process.exitCode = 1; });
module.exports = { probeHttp, probeTcp, probeTls, probeH264, hasAbstractListener, tlsTarget, readProbeConfiguration };
