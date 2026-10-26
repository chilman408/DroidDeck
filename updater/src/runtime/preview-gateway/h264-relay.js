// SPDX-License-Identifier: GPL-3.0-only
const http = require("node:http");
const net = require("node:net");
const { WebSocketServer, WebSocket } = require("ws");
const { ScrcpyStream } = require("./scrcpy-stream");
const { loadWorkerConfiguration } = require('../portable/worker-config');
const SOCKET = "\0scrcpy_54410001";
const FLOW_PROTOCOL = 'droiddeck-live-v1';
const FLOW_WINDOW = 4, FLOW_BYTES = 512 * 1024;
const FLOW_PENDING_BYTES = 2 * 1024 * 1024 + 256 * 1024 + 12;
function createVideoRelay({ connect = () => net.connect({ path: SOCKET }), reconnectMs = 500, clock = () => performance.now() } = {}) {
  const server = http.createServer((req, res) => {
    res.setHeader('Cache-Control', 'no-store');
    if (req.method !== 'GET' || req.url !== '/flow-state') { res.writeHead(404).end(); return; }
    const token = req.headers['x-droiddeck-display-session'];
    if (!/^[a-f0-9]{64}$/.test(token || '')) { res.writeHead(403).end(); return; }
    const clients = [...ws.clients].filter(client => client.readyState === WebSocket.OPEN && client.displaySession === token);
    // Ambiguous overlapping reconnects and non-acknowledging clients cannot drive adaptation.
    if (clients.length !== 1 || !clients[0].flow) { res.end(JSON.stringify({ available: false })); return; }
    const client = clients[0];
    const oldest = client.inflight.size ? Math.max(0, clock() - Math.min(...[...client.inflight.values()].map(value => value.sentAt))) : 0;
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ available: true, flow: true, streamId: client.streamId,
      ackedPackets: client.ackedPackets, acknowledgedBytes: client.acknowledgedBytes,
      pendingBytes: client.pendingBytes, pendingFrames: client.pending.length,
      oldestMs: Math.round(oldest), ackDelayMs: Math.round(client.ackDelayMs) }));
  });
  const ws = new WebSocketServer({ server, maxPayload: 1024 });
  let source, retry, closing = false, session, config, replay = [], replayBytes = 0;
  function clearReplay() { replay = []; replayBytes = 0; }
  function send(client, payload) {
    if (client.readyState !== WebSocket.OPEN) return;
    if (client.bufferedAmount > 2 * 1024 * 1024) { client.terminate(); return; }
    client.send(payload);
  }
  function drainFlow(client) {
    while (client.readyState === WebSocket.OPEN && client.pending.length && client.inflight.size < FLOW_WINDOW) {
      const payload = client.pending[0];
      if (client.inflight.size && client.inflightBytes + payload.length > FLOW_BYTES) return;
      client.pending.shift(); client.pendingBytes -= payload.length;
      if (payload === config) client.flowConfig = config;
      const id = ++client.flowSerial;
      client.inflight.set(id, { bytes: payload.length, sentAt: clock() }); client.inflightBytes += payload.length;
      send(client, JSON.stringify({ type: 'scrcpy-flow-frame', id }));
      send(client, payload);
    }
  }
  function clearFlow(client) { client.pending = []; client.pendingBytes = 0; }
  function queueFlow(client, packet, key = false) {
    if (key) {
      // Unsent old references can be replaced only by a complete new GOP.
      clearFlow(client); client.waitingKey = false;
      if (client.flowConfig !== config) { client.pending.push(config); client.pendingBytes = config.length; }
    } else if (client.waitingKey) return;
    if (client.pending.length >= 121 || client.pendingBytes + packet.length > FLOW_PENDING_BYTES) {
      clearFlow(client); client.waitingKey = true; return;
    }
    client.pending.push(packet); client.pendingBytes += packet.length; drainFlow(client);
  }
  function receive(event) {
    if (event.type === "session") {
      session = JSON.stringify({ type: "scrcpy-session", width: event.width, height: event.height }); config = null; clearReplay();
      for (const client of ws.clients) { client.waitingKey = true; if (client.flow) { clearFlow(client); client.flowConfig = null; } send(client, session); }
    } else if (event.config) {
      if (event.packet.length > 256 * 1024) throw new Error("Video configuration size exceeded");
      config = event.packet; clearReplay();
      for (const client of ws.clients) { client.waitingKey = true; if (client.flow) clearFlow(client); }
    } else {
      // Retain only a complete bounded GOP so idle screens can serve late viewers.
      if (event.key) clearReplay();
      if (config && (event.key || replay.length)) {
        replay.push(event.packet); replayBytes += event.packet.length;
        if (replay.length > 120 || replayBytes + config.length > 2 * 1024 * 1024) clearReplay();
      }
      for (const client of ws.clients) {
        if (client.flow) { if (config) queueFlow(client, event.packet, event.key); continue; }
        if (client.waitingKey) {
          if (!event.key || !config) continue;
          send(client, config); client.waitingKey = false;
        }
        send(client, event.packet);
      }
    }
  }
  function start() {
    if (closing) return;
    const current = connect(); source = current; session = config = null; clearReplay();
    const parser = new ScrcpyStream(receive);
    const failed = () => {
      if (source !== current) return;
      source = null; session = config = null; clearReplay(); current.destroy();
      for (const client of ws.clients) client.close(1012, "Video reconnecting");
      if (!closing) { retry = setTimeout(start, reconnectMs); retry.unref(); }
    };
    current.on("data", bytes => { try { parser.push(bytes); } catch { failed(); } });
    current.once("error", failed); current.once("close", failed); current.once("end", failed);
  }
  ws.on("connection", (client, req) => {
    const token = req?.headers?.['x-droiddeck-display-session'];
    client.displaySession = /^[a-f0-9]{64}$/.test(token || '') ? token : null;
    client.streamId = require('node:crypto').randomBytes(8).toString('hex');
    client.ackedPackets = 0; client.acknowledgedBytes = 0; client.ackDelayMs = 0;
    if (ws.clients.size > 4) { client.close(1013, "Video viewer limit"); return; }
    client.waitingKey = true;
    client.flow = client.protocol === FLOW_PROTOCOL;
    if (client.flow) { clearFlow(client); client.inflight = new Map(); client.inflightBytes = 0; client.flowSerial = 0; client.flowConfig = null; }
    if (session) send(client, session);
    if (config && replay.length) {
      if (client.flow) {
        client.pending = [config, ...replay]; client.pendingBytes = config.length + replayBytes;
        drainFlow(client);
      } else {
        send(client, config);
        for (const packet of replay) send(client, packet);
      }
      client.waitingKey = false;
    }
    client.on("message", (bytes, binary) => {
      let ack;
      try { if (!binary && client.flow) ack = JSON.parse(bytes.toString()); } catch {}
      if (!ack || Object.keys(ack).sort().join(',') !== 'id,type' || ack.type !== 'scrcpy-flow-ack' ||
          !Number.isSafeInteger(ack.id) || ack.id <= 0) { client.close(1008, "Video is read-only"); return; }
      if (!client.inflight.has(ack.id)) return;
      const record = client.inflight.get(ack.id);
      const latency = Math.max(0, clock() - record.sentAt);
      client.ackDelayMs = client.ackedPackets ? client.ackDelayMs * .75 + latency * .25 : latency;
      client.ackedPackets++; client.acknowledgedBytes += record.bytes;
      client.inflightBytes -= record.bytes; client.inflight.delete(ack.id); drainFlow(client);
    });
    client.on("error", () => {});
  });
  server.once("listening", start);
  server.shutdown = async () => {
    closing = true; clearTimeout(retry); source?.destroy();
    for (const client of ws.clients) client.terminate();
    await new Promise(resolve => ws.close(resolve));
    await new Promise(resolve => server.close(resolve));
  };
  return server;
}
async function main({ configuration, args = process.argv.slice(2) } = {}) {
  if (!Array.isArray(args) || args.length) throw new Error('Fixed video relay requires no arguments');
  await loadWorkerConfiguration({ role: 'user', read: () => configuration });
  const server = createVideoRelay(); server.listen(9092, "127.0.0.1");
  for (const signal of ["SIGTERM", "SIGINT"]) process.once(signal, () => server.shutdown().then(() => process.exit(0)));
  return server;
}
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
module.exports = { createVideoRelay, SOCKET, main };
