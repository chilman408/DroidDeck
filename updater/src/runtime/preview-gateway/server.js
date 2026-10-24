// SPDX-License-Identifier: GPL-3.0-only
const fs = require("node:fs");
const crypto = require("node:crypto");
const http = require("node:http");
const https = require("node:https");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const proxy = require("http-proxy");
const serve = require("serve-handler");
const { createBrowserAccess } = require("./browser-access");
const { validateState } = require("./display-helper-client");
const { loadWorkerConfiguration } = require('../portable/worker-config');
const { deriveRuntimeLayout } = require('../portable/runtime-config');

// Static public build identity lets a pinned-TLS updater verify the NEW process
// even when browser login is enabled. No host, account or session state here.
const RUNTIME_RELEASE = Object.freeze({ kind: 'droiddeck-runtime-release', version: '0.1.1-rc.5', updateProtocol: 1 });

const DEFAULT_TARGETS = {
  api: "http://127.0.0.1:18081",
  display: "ws://127.0.0.1:19091",
  touchscreen: "ws://127.0.0.1:19999",
};

function localTarget(value, protocol) {
  const url = new URL(value);
  if (url.protocol !== protocol || url.hostname !== "127.0.0.1" ||
      url.username || url.password || url.pathname !== "/" || url.search || url.hash) {
    throw new Error(`backend must be a ${protocol}//127.0.0.1 port: ${value}`);
  }
  return url.origin;
}

function rejectUpgrade(socket, status, reason) {
  const challenge = status === 401 ? 'WWW-Authenticate: Basic realm="TeslaAndroid private test", charset="UTF-8"\r\n' : "";
  socket.end(`HTTP/1.1 ${status} ${reason}\r\n${challenge}Cache-Control: no-store\r\nConnection: close\r\n\r\n`);
}

function validateProxyOrigin(value) {
  if (value === undefined) return undefined;
  let url;
  try { url = new URL(value); } catch { throw new Error("Invalid proxy HTTPS origin"); }
  if (typeof value !== "string" || url.protocol !== "https:" || url.origin !== value ||
      net.isIP(url.hostname) || !url.hostname.includes(".") || url.hostname.length > 253 ||
      !url.hostname.split(".").every(label => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label))) {
    throw new Error("Proxy origin must be an exact HTTPS DNS origin without credentials or a path");
  }
  return value;
}

function readProxyOrigin(filename) {
  const stat = fs.lstatSync(filename, { throwIfNoEntry: false });
  if (!stat) return undefined;
  if (!stat.isFile() || stat.size > 256 || (process.platform !== "win32" && (stat.uid !== 0 || (stat.mode & 0o022)))) {
    throw new Error("Proxy origin file must be a root-owned regular file without group or other write access");
  }
  return validateProxyOrigin(fs.readFileSync(filename, "utf8").trim());
}

function createGateway({ frontendDir, targets = DEFAULT_TARGETS, publicOrigin, proxyOrigin, auth, tls, accessFile, displayControl, audioSettings, gpsControl, videoSettings, connectionControl, audioPcmGain = false, deviceInfoProvider, retireTouch } = {}) {
  proxyOrigin = validateProxyOrigin(proxyOrigin);
  if (proxyOrigin && !publicOrigin) throw new Error("Proxy origin requires a primary HTTPS gateway");
  if (!frontendDir || !fs.statSync(path.join(frontendDir, "index.html"), { throwIfNoEntry: true }).isFile()) {
    throw new Error("frontendDir must contain index.html");
  }
  const backends = {
    api: localTarget(targets.api, "http:"),
    display: localTarget(targets.display, "ws:"),
    touchscreen: localTarget(targets.touchscreen, "ws:"),
    h264: targets.h264 ? localTarget(targets.h264, "ws:") : null,
    audio: targets.audio ? localTarget(targets.audio, "ws:") : null,
  };
  if (backends.audio && (!displayControl || !audioSettings)) throw new Error("Audio requires session authority and preferences");
  if (typeof audioPcmGain !== "boolean" || (audioPcmGain && !backends.audio)) throw new Error("Audio PCM gain requires an audio backend");
  if (deviceInfoProvider !== undefined && typeof deviceInfoProvider !== "function") throw new Error("Invalid device information provider");
  if (backends.h264 && !displayControl) throw new Error("H264 requires display session authority");
  if (gpsControl && !displayControl) throw new Error("GPS requires display session authority");
  if (videoSettings && (!displayControl || !backends.h264)) throw new Error("Video settings require supervised H264 session authority");
  if (retireTouch !== undefined && (typeof retireTouch !== "function" || !displayControl)) throw new Error("Rear display requires native touch retirement");
  if (connectionControl && !displayControl) throw new Error('Browser connection requires session authority');
  const connectionSnapshot = () => require('../browser-connection/helper').safeSnapshot(connectionControl.snapshot());
  if (connectionControl) connectionSnapshot();
  const gpsServer = gpsControl ? new (require("ws").WebSocketServer)({ noServer: true, maxPayload: 2048, perMessageDeflate: false }) : null;
  const streamState = state => ({ ...state, ...(backends.h264 ? { streamFormats: ["png", "h264"] } : {}),
    ...(videoSettings ? { videoControlSupported: 1, videoTargets: videoSettings.snapshot() } : {}) });
  let allowedHost, credentialHash;
  if (publicOrigin) {
    const url = new URL(publicOrigin);
    if (url.protocol !== "https:") throw new Error("LAN gateway requires HTTPS");
    if (url.origin !== publicOrigin) throw new Error("publicOrigin must be an origin without a path");
    if (!tls?.key || !tls?.cert) throw new Error("LAN gateway requires a TLS key and certificate");
    if (auth && (typeof auth.username !== "string" || !auth.username ||
        /[:\x00-\x1f\x7f]/.test(auth.username) || typeof auth.password !== "string" ||
        Buffer.byteLength(auth.password) < 32 || /[\x00-\x1f\x7f]/.test(auth.password))) {
      throw new Error("LAN gateway requires credentials with a random password of at least 32 bytes");
    }
    allowedHost = url.host;
    if (auth) credentialHash = crypto.createHash("sha256").update(`${auth.username}:${auth.password}`).digest();
  } else if (auth || tls || accessFile) {
    throw new Error("TLS and credentials require an explicit publicOrigin");
  }
  if (auth && accessFile) throw new Error("Choose browser settings or static credentials, not both");
  const access = accessFile ? createBrowserAccess(accessFile) : null;
  const upgradedSockets = new Set();
  const audioSockets = new Set();
  const audioBySession = new Map();
  const gpsBySession = new Map();
  const touchBySession = new Map();
  const sessionExpired = token => {
    for (const socket of audioBySession.get(token) || []) socket.destroy();
    for (const socket of gpsBySession.get(token) || []) socket.destroy();
  };
  displayControl?.on("expired", sessionExpired);
  const audioChanged = state => { if (!state.enabled) for (const socket of audioSockets) socket.destroy(); };
  audioSettings?.on("change", audioChanged);
  const closeStreams = () => { for (const socket of upgradedSockets) socket.destroy(); };
  displayControl?.on("pause", closeStreams);
  const proxyForHost = host => {
    if (proxyOrigin && host === new URL(proxyOrigin).host) return proxyOrigin;
    if (connectionControl) return [connectionSnapshot().origin, connectionSnapshot().previousOrigin].filter(Boolean).find(origin => host === new URL(origin).host);
    return undefined;
  };
  const hostAllowed = (host) => typeof host === "string" &&
    (publicOrigin ? host === allowedHost || Boolean(proxyForHost(host)) : /^127\.0\.0\.1:\d+$/.test(host));
  const originFor = (host) => proxyForHost(host) || publicOrigin || `http://${host}`;
  const authorized = (req) => {
    if (access) return access.authorized(req.headers.authorization);
    if (!credentialHash) return true;
    const match = /^Basic ([a-zA-Z0-9+/]+={0,2})$/i.exec(req.headers.authorization || "");
    if (!match) return false;
    const supplied = crypto.createHash("sha256").update(Buffer.from(match[1], "base64")).digest();
    return crypto.timingSafeEqual(supplied, credentialHash);
  };
  const upstream = proxy.createProxyServer({ changeOrigin: true });
  let closing = false;
  upstream.on("error", () => {});
  upstream.on("proxyReqWs", (proxyRequest, _req, socket) => {
    socket.once("close", () => proxyRequest.destroy());
    proxyRequest.once("upgrade", (_res, proxySocket) => {
      if (socket.destroyed) proxySocket.destroy();
      else socket.once("close", () => proxySocket.destroy());
    });
  });

  const handler = async (req, res) => {
    if (closing) { res.writeHead(503).end(); return; }
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.setHeader("Referrer-Policy", "no-referrer");
    res.setHeader("Content-Security-Policy", "frame-ancestors 'none'");
    const host = req.headers.host;
    if (!hostAllowed(host)) {
      res.writeHead(403).end();
      return;
    }
    if (req.url === '/api/runtimeRelease' || req.url.startsWith('/api/runtimeRelease?')) {
      if (req.url !== '/api/runtimeRelease') { res.writeHead(400).end(); return; }
      if (req.headers.origin && req.headers.origin !== originFor(host)) { res.writeHead(403).end(); return; }
      if (req.method !== 'GET') { res.setHeader('Allow', 'GET'); res.writeHead(405).end(); return; }
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify(RUNTIME_RELEASE) + '\n');
      return;
    }
    const generation = access?.snapshot().csrfToken;
    if (!(await authorized(req)) || (access && generation !== access.snapshot().csrfToken)) {
      res.setHeader("WWW-Authenticate", 'Basic realm="TeslaAndroid private test", charset="UTF-8"');
      res.writeHead(401).end();
      return;
    }
    if (!req.url.startsWith("/") || req.url.startsWith("//")) {
      res.writeHead(400).end();
      return;
    }
    const pathname = new URL(req.url, `http://${host}`).pathname;
    if (deviceInfoProvider) {
      let decoded;
      try { decoded = decodeURIComponent(pathname); } catch { res.writeHead(400).end(); return; }
      if (decoded === "/api/deviceInfo") {
        if (decoded !== pathname) { res.writeHead(400).end(); return; }
        if (req.headers.origin && req.headers.origin !== originFor(host)) { res.writeHead(403).end(); return; }
        if (req.method !== "GET") { res.setHeader("Allow", "GET"); res.writeHead(405).end(); return; }
        try {
          const info = deviceInfoProvider();
          if (!info || typeof info !== "object" || Array.isArray(info) || info.deviceInfoVersion !== 1) throw new Error("Invalid device information");
          const body = JSON.stringify(info);
          if (Buffer.byteLength(body) > 4096) throw new Error("Device information exceeded limit");
          res.setHeader("Content-Type", "application/json"); res.end(body);
        }
        catch { res.writeHead(503).end(JSON.stringify({ error: "Device information unavailable" })); }
        return;
      }
    }
    if ((backends.audio || gpsControl) && pathname === "/api/configuration") {
      if (req.method !== "GET") { res.writeHead(405).end(); return; }
      if (req.headers.origin && req.headers.origin !== originFor(host)) { res.writeHead(403).end(); return; }
      try {
        const response = await fetch(backends.api + "/api/configuration", { signal: AbortSignal.timeout(3000), redirect: "error" });
        if (!response.ok) throw new Error("Configuration unavailable");
        const chunks = []; let size = 0;
        for await (const chunk of response.body) { size += chunk.length; if (size > 16384) throw new Error("Configuration exceeded limit"); chunks.push(chunk); }
        const config = JSON.parse(Buffer.concat(chunks).toString());
        if (!config || typeof config !== "object" || Array.isArray(config)) throw new Error("Invalid configuration");
        const state = backends.audio ? audioSettings.snapshot() : null;
        let gps;
        if (gpsControl) {
          try { gps = await gpsControl.refresh(); } catch { gps = gpsControl.snapshot(); }
        }
        res.setHeader("Content-Type", "application/json");
        res.end(JSON.stringify({ ...config, ...(state ? { browserAudioSupported: 1, browserAudioGainSupported: audioPcmGain ? 1 : 0,
          "persist.tesla-android.browser_audio.is_enabled": state.enabled ? 1 : 0,
          "persist.tesla-android.browser_audio.volume": state.volume } : {}),
          ...(gps ? { browserGpsSupported: gps.supported && !gps.recoveryRequired ? 1 : 0, browserGpsMock: 1,
            "persist.tesla-android.gps.is_active": gps.enabled ? 1 : 0 } : {}) }));
      } catch { res.writeHead(502).end("Configuration unavailable"); }
      return;
    }
    if (gpsControl && pathname === "/api/gpsState") {
      if (req.method !== "POST") { res.writeHead(405).end(); return; }
      if (req.headers.origin !== originFor(host)) { res.writeHead(403).end(); return; }
      if (req.headers["content-type"] !== "text/plain") { res.writeHead(415).end(); return; }
      const permitted = () => {
        if (access && generation !== access.snapshot().csrfToken) return false;
        try { displayControl.assertOwner(req.headers["x-display-session"], req.headers["x-display-csrf"]); return true; }
        catch { return false; }
      };
      try {
        if (!permitted()) throw Object.assign(new Error(), { status: 403 });
        const chunks = []; let size = 0;
        for await (const chunk of req) { size += chunk.length; if (size > 8) { res.writeHead(413).end(); return; } chunks.push(chunk); }
        const value = Buffer.concat(chunks).toString();
        if (!/^[01]$/.test(value)) throw Object.assign(new Error(), { status: 400 });
        const state = await gpsControl.update(value === "1", { authorized: permitted });
        res.setHeader("Content-Type", "application/json"); res.end(JSON.stringify(state));
      } catch (error) { res.writeHead([400, 403, 409].includes(error.status) ? error.status : 503).end("GPS setting unavailable"); }
      return;
    }
    if (backends.audio && ["/api/browserAudioState", "/api/browserAudioVolume"].includes(pathname)) {
      if (req.method !== "POST") { res.writeHead(405).end(); return; }
      if (req.headers.origin !== originFor(host)) { res.writeHead(403).end(); return; }
      if (req.headers["content-type"] !== "text/plain") { res.writeHead(415).end(); return; }
      try {
        displayControl.assertOwner(req.headers["x-display-session"], req.headers["x-display-csrf"]);
        const chunks = []; let size = 0;
        for await (const chunk of req) { size += chunk.length; if (size > 8) { res.writeHead(413).end(); return; } chunks.push(chunk); }
        if (access && generation !== access.snapshot().csrfToken) throw Object.assign(new Error("Access changed"), { status: 403 });
        displayControl.assertOwner(req.headers["x-display-session"], req.headers["x-display-csrf"]);
        const text = Buffer.concat(chunks).toString();
        if (!/^\d{1,3}$/.test(text)) throw Object.assign(new Error("Invalid audio value"), { status: 400 });
        const state = audioSettings.update(pathname.endsWith("State") ? "enabled" : "volume", Number(text));
        res.setHeader("Content-Type", "application/json"); res.end(JSON.stringify(state));
      } catch (error) { res.writeHead([400, 403].includes(error.status) ? error.status : 500).end("Audio setting unavailable"); }
      return;
    }
    if (displayControl && pathname.startsWith("/api/")) {
      let decoded;
      try { decoded = decodeURIComponent(pathname); } catch { res.writeHead(400).end(); return; }
      const protectedPaths = ["/api/displaySession", "/api/displayState", "/api/videoState", "/api/browserConnection", "/api/rearDisplayState", "/api/displayRole",
        ...(gpsControl ? ["/api/configuration", "/api/gpsState"] : []),
        ...(backends.audio ? ["/api/configuration", "/api/browserAudioState", "/api/browserAudioVolume"] : [])];
      if (protectedPaths.includes(decoded) && decoded !== pathname) {
        res.writeHead(400).end(); return;
      }
    }
    if (["/api/rearDisplayState", "/api/displayRole"].includes(pathname)) {
      if (!retireTouch) { res.writeHead(404).end(); return; }
      if (new URL(req.url, `http://${host}`).search) { res.writeHead(400).end(); return; }
      res.setHeader("Content-Type", "application/json");
      const token = req.headers["x-display-session"], csrf = req.headers["x-display-csrf"];
      try {
        if (req.headers.origin && req.headers.origin !== originFor(host)) throw Object.assign(new Error("Foreign Origin"), { status: 403 });
        if (req.method === "GET" && pathname === "/api/rearDisplayState") {
          res.end(JSON.stringify(displayControl.rearSnapshot(token))); return;
        }
        if (req.method !== "POST") { res.setHeader("Allow", pathname === "/api/rearDisplayState" ? "GET, POST" : "POST"); res.writeHead(405).end(); return; }
        if (req.headers.origin !== originFor(host)) throw Object.assign(new Error("Origin required"), { status: 403 });
        if (req.headers["content-type"] !== "application/json") { res.writeHead(415).end(); return; }
        const chunks = []; let size = 0;
        for await (const chunk of req) { size += chunk.length; if (size > 256) { res.writeHead(413).end(); return; } chunks.push(chunk); }
        if (access && generation !== access.snapshot().csrfToken) throw Object.assign(new Error("Access changed"), { status: 403 });
        let payload;
        try { payload = JSON.parse(Buffer.concat(chunks).toString("utf8")); }
        catch { throw Object.assign(new Error("Invalid JSON"), { status: 400 }); }
        let state;
        if (pathname === "/api/displayRole") {
          const fields = payload && Object.keys(payload).sort().join(',');
          if (!payload || Array.isArray(payload) || !['role', 'claim,generation,role'].includes(fields)) {
            throw Object.assign(new Error("One display role required"), { status: 400 });
          }
          state = await displayControl.registerRole(token, payload.role, csrf,
            fields === 'role' ? {} : { claim: payload.claim, generation: payload.generation });
        } else state = await displayControl.updateRearSettings(token, payload, csrf);
        if (access && generation !== access.snapshot().csrfToken) throw Object.assign(new Error("Access changed"), { status: 403 });
        displayControl.snapshot(token);
        res.end(JSON.stringify(streamState(state)));
      } catch (error) {
        res.writeHead([400, 403, 404, 409, 503].includes(error.status) ? error.status : 503)
          .end(JSON.stringify({ error: error.status ? error.message : "Rear display unavailable" }));
      }
      return;
    }
    if (pathname === '/api/browserConnection') {
      if (!connectionControl) { res.writeHead(404).end(); return; }
      res.setHeader('Content-Type', 'application/json');
      try {
        if (new URL(req.url, `http://${host}`).search) throw Object.assign(new Error('Unexpected query'), { status: 400 });
        if (req.headers.origin && req.headers.origin !== originFor(host)) throw Object.assign(new Error('Foreign Origin'), { status: 403 });
        if (req.method === 'GET') {
          displayControl.snapshot(req.headers['x-display-session']);
          if (typeof connectionControl.refresh === 'function') await connectionControl.refresh();
          res.end(JSON.stringify(connectionSnapshot())); return;
        }
        if (req.method !== 'POST') { res.setHeader('Allow', 'GET, POST'); res.writeHead(405).end(); return; }
        if (req.headers.origin !== originFor(host)) throw Object.assign(new Error('Origin required'), { status: 403 });
        if (req.headers['content-type'] !== 'application/json') { res.writeHead(415).end(); return; }
        const owner = () => displayControl.assertOwner(req.headers['x-display-session'], req.headers['x-display-csrf']);
        owner(); const chunks = []; let size = 0;
        for await (const chunk of req) { size += chunk.length; if (size > 512) { res.writeHead(413).end(); return; } chunks.push(chunk); }
        owner();
        if (access && generation !== access.snapshot().csrfToken) throw Object.assign(new Error('Access changed'), { status: 403 });
        let payload;
        try { payload = JSON.parse(Buffer.concat(chunks).toString('utf8')); }
        catch (_) { throw Object.assign(new Error('Invalid JSON'), { status: 400 }); }
        await connectionControl.update(payload);
        res.end(JSON.stringify(connectionSnapshot()));
      } catch (error) {
        res.writeHead([400, 403, 409].includes(error.status) ? error.status : 503)
          .end(JSON.stringify({ error: error.status ? error.message : 'Browser connection unavailable' }));
      }
      return;
    }
    if (pathname === "/api/videoState") {
      if (!videoSettings) { res.writeHead(404).end(); return; }
      res.setHeader("Content-Type", "application/json");
      try {
        if (req.headers.origin && req.headers.origin !== originFor(host)) throw Object.assign(new Error("Foreign Origin"), { status: 403 });
        if (req.method === "GET") {
          displayControl.snapshot(req.headers["x-display-session"]);
          res.end(JSON.stringify(videoSettings.snapshot())); return;
        }
        if (req.method !== "POST") { res.setHeader("Allow", "GET, POST"); res.writeHead(405).end(); return; }
        if (req.headers.origin !== originFor(host)) throw Object.assign(new Error("Origin required"), { status: 403 });
        if (req.headers["content-type"] !== "application/json") { res.writeHead(415).end(); return; }
        displayControl.assertOwner(req.headers["x-display-session"], req.headers["x-display-csrf"]);
        const chunks = []; let size = 0;
        for await (const chunk of req) { size += chunk.length; if (size > 256) { res.writeHead(413).end(); return; } chunks.push(chunk); }
        if (access && generation !== access.snapshot().csrfToken) throw Object.assign(new Error("Access changed"), { status: 403 });
        displayControl.assertOwner(req.headers["x-display-session"], req.headers["x-display-csrf"]);
        let payload;
        try { payload = JSON.parse(Buffer.concat(chunks).toString("utf8")); }
        catch { throw Object.assign(new Error("Invalid JSON"), { status: 400 }); }
        res.end(JSON.stringify(videoSettings.update(payload)));
      } catch (error) { res.writeHead([400, 403, 409].includes(error.status) ? error.status : 503)
        .end(JSON.stringify({ error: error.status ? error.message : "Video settings unavailable" })); }
      return;
    }
    if (displayControl && ["/api/displaySession", "/api/displayState"].includes(pathname)) {
      res.setHeader("Content-Type", "application/json");
      const sessionToken = req.headers["x-display-session"];
      try {
        if (req.headers.origin && req.headers.origin !== originFor(host)) throw Object.assign(new Error("Foreign Origin"), { status: 403 });
        if (req.method === "GET" && pathname === "/api/displayState") {
          res.end(JSON.stringify(streamState(displayControl.snapshot(sessionToken)))); return;
        }
        if (req.method !== "POST") {
          res.setHeader("Allow", pathname === "/api/displayState" ? "GET, POST" : "POST"); res.writeHead(405).end(); return;
        }
        if (req.headers.origin !== originFor(host)) throw Object.assign(new Error("Origin required"), { status: 403 });
        if (req.headers["content-type"] !== "application/json") { res.writeHead(415).end(); return; }
        const chunks = []; let size = 0;
        for await (const chunk of req) {
          size += chunk.length; if (size > 4096) { res.writeHead(413).end(); return; } chunks.push(chunk);
        }
        let payload;
        try { payload = JSON.parse(Buffer.concat(chunks).toString("utf8")); }
        catch { throw Object.assign(new Error("Invalid JSON"), { status: 400 }); }
        if (access && generation !== access.snapshot().csrfToken) throw Object.assign(new Error("Access changed"), { status: 403 });
        if (pathname === "/api/displaySession") {
          if (!payload || Array.isArray(payload) || typeof payload !== "object" || Object.keys(payload).length) {
            throw Object.assign(new Error("Empty session request required"), { status: 400 });
          }
          res.end(JSON.stringify(displayControl.createSession())); return;
        }
        const state = await displayControl.resize(sessionToken, payload, req.headers["x-display-csrf"]);
        if (access && generation !== access.snapshot().csrfToken) throw Object.assign(new Error("Access changed"), { status: 403 });
        displayControl.snapshot(sessionToken);
        res.end(JSON.stringify(streamState(state)));
      } catch (error) {
        const status = [400, 403, 409, 503].includes(error.status) ? error.status : 400;
        if (status === 503) res.setHeader("Retry-After", "1");
        res.writeHead(status).end(JSON.stringify({ error: error.message, recoveryRequired: error.recoveryRequired === true }));
      }
      return;
    }
    if (pathname === "/api/browserAccess" && access) {
      res.setHeader("Content-Type", "application/json");
      if (req.headers.origin && req.headers.origin !== originFor(host)) {
        res.writeHead(403).end();
        return;
      }
      if (req.method === "GET") {
        res.end(JSON.stringify(access.snapshot()));
        return;
      }
      if (req.method !== "POST") {
        res.setHeader("Allow", "GET, POST");
        res.writeHead(405).end();
        return;
      }
      if (req.headers.origin !== originFor(host) || req.headers["x-csrf-token"] !== generation) {
        res.writeHead(403).end();
        return;
      }
      if (req.headers["content-type"] !== "application/json") {
        res.writeHead(415).end();
        return;
      }
      try {
        const chunks = [];
        let size = 0;
        for await (const chunk of req) {
          size += chunk.length;
          if (size > 4096) {
            res.writeHead(413).end();
            return;
          }
          chunks.push(chunk);
        }
        let payload;
        try { payload = JSON.parse(Buffer.concat(chunks).toString("utf8")); }
        catch { res.writeHead(400).end(JSON.stringify({ error: "Invalid JSON" })); return; }
        const next = await access.update(payload, generation);
        displayControl?.revokeAll();
        res.end(JSON.stringify(next));
        // Revoke existing control/stream connections whenever access changes.
        closeStreams();
      } catch (error) {
        res.writeHead(error.status || 500).end(JSON.stringify({ error:
          error.status ? error.message : "Could not save browser access settings" }));
      }
      return;
    }
    if (pathname === "/api" || pathname.startsWith("/api/")) {
      const origin = req.headers.origin;
      if (origin && origin !== originFor(host)) {
        res.writeHead(403).end();
        return;
      }
      delete req.headers.authorization;
      upstream.web(req, res, { target: backends.api }, () => {
        if (!res.headersSent) res.writeHead(502).end("Preview API unavailable");
      });
      return;
    }
    if (pathname === "/sockets" || pathname.startsWith("/sockets/")) {
      res.writeHead(426).end("WebSocket upgrade required");
      return;
    }
    let decoded;
    try {
      decoded = decodeURIComponent(pathname);
    } catch {
      res.writeHead(400).end();
      return;
    }
    const assetRoot = ["assets", "css", "fonts", "js"].includes(decoded.split("/")[1]);
    if ((decoded !== "/" && decoded !== "/index.html" && !assetRoot) ||
        decoded.split("/").some((part) => part.startsWith("."))) {
      res.writeHead(404).end();
      return;
    }
    if (pathname === "/") req.url = "/index.html";
    res.setHeader("Cache-Control", "no-store");
    if (decoded === '/js/display/display-renderer-manager.js' && ['GET', 'HEAD'].includes(req.method)) {
      // Keep protected frontend files/receipts unchanged; this policy rolls back
      // atomically with the gateway update. It never changes Android settings.
      const original = fs.readFileSync(path.join(frontendDir, 'js/display/display-renderer-manager.js'));
      const policy = fs.readFileSync(path.join(__dirname, 'browser-renderer-policy.js'));
      const body = Buffer.concat([original, Buffer.from('\n;\n'), policy]);
      res.setHeader('Content-Type', 'text/javascript; charset=utf-8');
      res.setHeader('Content-Length', body.length);
      res.end(req.method === 'HEAD' ? undefined : body);
      return;
    }
    serve(req, res, { public: frontendDir, directoryListing: false, cleanUrls: false })
      .catch(() => { if (!res.headersSent) res.writeHead(500).end(); });
  };
  const requestHandler = (req, res) => {
    handler(req, res).catch((error) => {
      if (!res.headersSent) {
        if (error.status === 503) res.setHeader("Retry-After", "1");
        res.writeHead(error.status === 503 ? 503 : 500);
      }
      res.end("Gateway request unavailable");
    });
  };
  const server = publicOrigin ? https.createServer({ ...tls, minVersion: "TLSv1.2" }, requestHandler) :
    http.createServer(requestHandler);

  const handleUpgrade = async (req, socket, head) => {
    if (closing) { rejectUpgrade(socket, 503, "Closing"); return; }
    const host = req.headers.host;
    if (!hostAllowed(host) || req.headers.origin !== originFor(host)) {
      rejectUpgrade(socket, 403, "Forbidden");
      return;
    }
    const generation = access?.snapshot().csrfToken;
    if (!(await authorized(req)) || (access && generation !== access.snapshot().csrfToken)) {
      rejectUpgrade(socket, 401, "Unauthorized");
      return;
    }
    if (!req.url.startsWith("/") || req.url.startsWith("//")) {
      rejectUpgrade(socket, 400, "Bad Request");
      return;
    }
    const url = new URL(req.url, `http://${host}`);
    const pathname = url.pathname;
    if (pathname === "/sockets/gps" && gpsControl) {
      const token = url.searchParams.get("session"), csrf = url.searchParams.get("csrf");
      if (url.searchParams.getAll("session").length !== 1 || url.searchParams.getAll("csrf").length !== 1 ||
          [...url.searchParams.keys()].some(key => !["session", "csrf"].includes(key))) {
        rejectUpgrade(socket, 403, "Forbidden"); return;
      }
      const permitted = () => {
        if (access && generation !== access.snapshot().csrfToken) return false;
        try { displayControl.assertOwner(token, csrf); return true; } catch { return false; }
      };
      if (!permitted()) { rejectUpgrade(socket, 403, "Forbidden"); return; }
      try { gpsControl.assertAvailable(); } catch (error) { rejectUpgrade(socket, error.status || 503, "GPS Unavailable"); return; }
      gpsServer.handleUpgrade(req, socket, head, client => {
        upgradedSockets.add(socket);
        const group = gpsBySession.get(token) || new Set(); gpsBySession.set(token, group); group.add(socket);
        socket.once("close", () => { upgradedSockets.delete(socket); group.delete(socket); if (!group.size) gpsBySession.delete(token); });
        void gpsControl.attach(client, permitted).catch(() => client.terminate());
      });
      return;
    }
    let target = pathname === "/sockets/display" ? backends.display :
      pathname === "/sockets/touchscreen" ? backends.touchscreen : pathname === "/sockets/audio" ? backends.audio : null;
    if (!target) {
      rejectUpgrade(socket, 404, "Not Found");
      return;
    }
    if (displayControl) {
      const token = url.searchParams.get("session");
      const formats = url.searchParams.getAll("format");
      if (url.searchParams.getAll("session").length !== 1 ||
          [...url.searchParams.keys()].some(key => !["session", "format"].includes(key)) ||
          (formats.length && (formats.length !== 1 || formats[0] !== "h264" || !backends.h264 || pathname !== "/sockets/display"))) {
        rejectUpgrade(socket, 403, "Forbidden"); return;
      }
      if (formats.length) target = backends.h264;
      const channel = pathname === "/sockets/display" ? "display" : "touchscreen";
      const isAudio = pathname === "/sockets/audio";
      if (isAudio && !audioSettings.snapshot().enabled) { rejectUpgrade(socket, 503, "Audio Disabled"); return; }
      try { if (isAudio) displayControl.snapshot(token); else displayControl.connect(token, channel); }
      catch (error) { rejectUpgrade(socket, error.status || 403, "Display Unavailable"); return; }
      if (isAudio) {
        const group = audioBySession.get(token) || new Set(); audioBySession.set(token, group);
        audioSockets.add(socket); group.add(socket);
        socket.once("close", () => { audioSockets.delete(socket); group.delete(socket); if (!group.size) audioBySession.delete(token); });
      }
      else {
        if (channel === "touchscreen") {
          const group = touchBySession.get(token) || new Set(); touchBySession.set(token, group); group.add(socket);
          socket.once("close", () => { group.delete(socket); if (!group.size) touchBySession.delete(token); });
        }
        socket.once("close", () => displayControl.disconnect(token, channel));
      }
      req.url = pathname;
    }
    delete req.headers.authorization;
    upgradedSockets.add(socket);
    socket.once("close", () => upgradedSockets.delete(socket));
    upstream.ws(req, socket, head, { target }, () => {
      if (!socket.destroyed) rejectUpgrade(socket, 502, "Bad Gateway");
    });
  };
  server.on("upgrade", (req, socket, head) => {
    handleUpgrade(req, socket, head).catch((error) => {
      if (!socket.destroyed) rejectUpgrade(socket, error.status === 503 ? 503 : 500, "Service Unavailable");
    });
  });
  const nativeClose = server.close.bind(server);
  if (retireTouch) server.retireDisplayAuthority = async (token, { signal, previousState } = {}) => {
    // An owner's session may already have expired; use the coordinator's committed geometry.
    const before = previousState ? validateState(previousState) : displayControl.snapshot(token);
    signal?.throwIfAborted();
    for (const socket of touchBySession.get(token) || []) socket.destroy();
    for (const socket of gpsBySession.get(token) || []) socket.destroy();
    await gpsControl?.retireFeed({ signal });
    signal?.throwIfAborted();
    const state = await retireTouch({ signal });
    if (state.recoveryRequired || ["width", "height", "density", "resolutionPreset", "isResponsive"].some(key => state[key] !== before[key])) {
      throw new Error("Native retirement changed the display state");
    }
  };
  let closePromise;
  server.close = callback => {
    if (!closePromise) {
      closing = true; closeStreams();
      const stopped = new Promise((resolve, reject) => nativeClose(error => error ? reject(error) : resolve()));
      closePromise = Promise.all([stopped, gpsControl?.close()]);
    }
    closePromise.then(() => callback?.(), error => { if (callback) callback(error); else server.emit("error", error); });
    return server;
  };
  server.once("close", () => { audioSettings?.off("change", audioChanged); displayControl?.off("expired", sessionExpired); displayControl?.off("pause", closeStreams); displayControl?.close(); closeStreams(); gpsServer?.close(); });
  return server;
}

async function main({ configuration, create = createGateway, args = process.argv.slice(2) } = {}) {
  if (!Array.isArray(args)) throw new Error('Invalid gateway arguments');
  if (configuration !== undefined) configuration = await loadWorkerConfiguration({ role: 'user', read: () => configuration });
  const layout = configuration && deriveRuntimeLayout(configuration);
  const preferences = layout?.preferences ?? path.join(os.homedir(), '.config', 'teslaandroid-waydroid');
  const preferenceFile = filename => configuration ? path.posix.join(preferences, filename) : path.join(preferences, filename);
  const sockets = layout?.sockets ?? '/run/teslaandroid-waydroid';
  if (configuration) {
    const expected = { GATEWAY_ORIGIN: layout.gatewayOrigin, GATEWAY_TLS_KEY_FILE: layout.keyFile,
      GATEWAY_TLS_CERT_FILE: layout.certificateFile, GATEWAY_ACCESS_FILE: preferences + '/browser-access.json',
      GATEWAY_DISPLAY_SOCKET: sockets + '/display.sock', GATEWAY_GPS_SOCKET: sockets + '/gps.sock' };
    for (const [name, value] of Object.entries(expected)) {
      if (process.env[name] !== undefined && process.env[name] !== value) throw new Error('Portable gateway override does not match configuration');
    }
    for (const name of ['GATEWAY_ANDROID_LOOPBACK', 'GATEWAY_H264_STREAM', 'GATEWAY_AUDIO_STREAM']) {
      if (process.env[name] !== undefined && process.env[name] !== '1') throw new Error('Invalid portable streaming flag');
    }
    if (args.length > 2 || (args[0] !== undefined && args[0] !== layout.frontend) ||
        (args[1] !== undefined && args[1] !== String(configuration.port))) throw new Error('Portable gateway arguments do not match configuration');
  }
  const frontendDir = args[0] ?? layout?.frontend;
  const publicOrigin = layout?.gatewayOrigin ?? process.env.GATEWAY_ORIGIN;
  const originUrl = publicOrigin ? new URL(publicOrigin) : null;
  const port = Number(configuration?.port ?? (args[1] || (originUrl ? originUrl.port || 443 : 18080)));
  if (!frontendDir || !Number.isInteger(port) || port < 1 || port > 65535) {
    console.error("usage: node server.js FRONTEND_DIR [PORT]");
    process.exit(2);
  }
  const bindAddress = publicOrigin ? process.env.GATEWAY_BIND || originUrl.hostname : "127.0.0.1";
  if (net.isIP(bindAddress) !== 4 || (originUrl && port !== Number(originUrl.port || 443))) {
    throw new Error("gateway bind must be an IPv4 address and port must match GATEWAY_ORIGIN");
  }
  const accessFile = publicOrigin ? process.env.GATEWAY_ACCESS_FILE ||
    preferenceFile("browser-access.json") : undefined;
  const tls = publicOrigin ? { key: fs.readFileSync(layout?.keyFile ?? process.env.GATEWAY_TLS_KEY_FILE),
    cert: fs.readFileSync(layout?.certificateFile ?? process.env.GATEWAY_TLS_CERT_FILE) } : undefined;
  const targets = configuration || process.env.GATEWAY_ANDROID_LOOPBACK === "1" ? {
    api: "http://127.0.0.1:8081", display: "ws://127.0.0.1:9091",
    touchscreen: "ws://127.0.0.1:9999",
  } : DEFAULT_TARGETS;
  if (configuration || process.env.GATEWAY_H264_STREAM === "1") targets.h264 = "ws://127.0.0.1:9092";
  if (configuration || process.env.GATEWAY_AUDIO_STREAM === "1") targets.audio = "ws://127.0.0.1:9093";
  async function start() {
    if (process.env.GATEWAY_REAR_DISPLAY !== undefined && process.env.GATEWAY_REAR_DISPLAY !== "1") throw new Error("Invalid rear display flag");
    const rearSettings = process.env.GATEWAY_REAR_DISPLAY === "1" ? require("./rear-display-settings").createRearDisplaySettings(
      preferenceFile("rear-display.json")) : undefined;
    let displayControl, helper, retireAuthority;
    const displaySocket = configuration ? sockets + '/display.sock' : process.env.GATEWAY_DISPLAY_SOCKET;
    if (displaySocket) {
      if (displaySocket !== sockets + '/display.sock') throw new Error("Unsupported display helper socket");
      const { createHelperClient } = require("./display-helper-client");
      helper = createHelperClient(displaySocket);
      displayControl = require("./display-control").createDisplayControl({ helper, rearSettings,
        ...(rearSettings ? { retireTimeoutMs: 30000, retireAuthority: (...args) => retireAuthority(...args) } : {}) });
      await displayControl.initialize();
    }
    if (rearSettings && !displayControl) throw new Error("Rear display requires display session authority");
    const audioSettings = targets.audio ? require("./audio-settings").createAudioSettings(
      preferenceFile("browser-audio.json")) : undefined;
    if (process.env.GATEWAY_AUDIO_PCM_GAIN !== undefined && process.env.GATEWAY_AUDIO_PCM_GAIN !== "1") throw new Error("Invalid audio PCM gain flag");
    const audioPcmGain = process.env.GATEWAY_AUDIO_PCM_GAIN === "1";
    if (process.env.GATEWAY_DEVICE_INFO !== undefined && process.env.GATEWAY_DEVICE_INFO !== "1") throw new Error("Invalid device information flag");
    const deviceInfoProvider = process.env.GATEWAY_DEVICE_INFO === "1" ? require("./device-info").createDeviceInfoProvider({
      lanOrigin: originUrl && net.isIP(originUrl.hostname) === 4 ? originUrl.origin : null }) : undefined;
    if (process.env.GATEWAY_VIDEO_SETTINGS !== undefined && process.env.GATEWAY_VIDEO_SETTINGS !== "1") throw new Error("Invalid video settings flag");
    const videoSettings = process.env.GATEWAY_VIDEO_SETTINGS === "1" ? require("./video-settings").createVideoSettings(
      configuration ? preferenceFile('browser-video.json') : require("./video-settings").VIDEO_SETTINGS_FILE) : undefined;
    let gpsControl;
    const gpsSocket = configuration ? sockets + '/gps.sock' : process.env.GATEWAY_GPS_SOCKET;
    if (gpsSocket) {
      if (gpsSocket !== sockets + '/gps.sock') throw new Error("Unsupported GPS helper socket");
      gpsControl = require("./gps-control").createGpsControl({ helper: require("./gps-helper-client").createGpsHelperClient(gpsSocket) });
      await gpsControl.initialize().catch(() => { console.error("GPS helper unavailable; streaming remains enabled"); });
    }
    const proxyOrigin = readProxyOrigin(configuration ? '/etc/droiddeck/proxy-origin' : "/etc/teslaandroid-waydroid/proxy-origin");
    let connectionControl;
    const connectionSocket = configuration ? sockets + '/connection.sock' : '/run/droiddeck-connection/connection.sock';
    if (fs.existsSync(connectionSocket)) {
      const socketStat = fs.lstatSync(connectionSocket);
      if (!socketStat.isSocket() || socketStat.uid !== 0 || socketStat.mode & 0o007 ||
          (configuration && socketStat.gid !== configuration.gid)) throw new Error('Unsafe connection helper socket');
      connectionControl = require('../browser-connection/helper').createConnectionClient(connectionSocket);
      await connectionControl.initialize();
    }
    const server = create({ frontendDir, publicOrigin, proxyOrigin, accessFile, tls, targets, displayControl, audioSettings, gpsControl, videoSettings, connectionControl, audioPcmGain, deviceInfoProvider,
      ...(rearSettings ? { retireTouch: options => helper.retireTouch(options) } : {}) });
    retireAuthority = server.retireDisplayAuthority;
    server.listen(port, bindAddress, () => console.log(`TeslaAndroid x86 preview at ${publicOrigin || `http://127.0.0.1:${port}`}/`));
    for (const signal of ["SIGTERM", "SIGINT"]) process.once(signal, () => {
      displayControl?.close(); void Promise.resolve(gpsControl?.close()).finally(closeGateway);
    });
    function closeGateway() { server.close(() => process.exit(0)); server.closeAllConnections(); }
    return server;
  }
  return start();
}
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });

module.exports = { createGateway, readProxyOrigin, main };
