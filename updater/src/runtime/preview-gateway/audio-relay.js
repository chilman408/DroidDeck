// SPDX-License-Identifier: GPL-3.0-only
const http = require("node:http");
const net = require("node:net");
const { spawn } = require("node:child_process");
const { WebSocketServer, WebSocket } = require("ws");
const { Mp4Fragments, RawPcmStream } = require("./audio-stream");
const { PcmGainStream, createVolumeProvider, gainEnabled } = require("./audio-gain");
const { loadWorkerConfiguration } = require('../portable/worker-config');
const { captureLayout } = require('./capture-layout');
const SOCKET = "\0scrcpy_54410002";
function encoderArgs(codec = "flac") {
  if (!["flac", "aac"].includes(codec)) throw new Error("Closed audio codec required");
  return ["-hide_banner", "-loglevel", "error", "-nostdin", "-probesize", "32", "-analyzeduration", "0",
    "-f", "s16le", "-ar", "48000", "-ac", "2",
    "-i", "pipe:0", "-vn", ...(codec === "aac" ?
      ["-c:a", "aac", "-profile:a", "aac_low", "-b:a", "128k"] :
      ["-c:a", "flac", "-compression_level", "0", "-strict", "experimental"]),
    // Match the pinned TeslaAndroid FLAC muxer's supported low-latency options.
    "-f", "mp4", "-movflags", "empty_moov+separate_moof+default_base_moof+frag_discont",
    "-brand", "isom", "-frag_duration", "40000", "-flush_packets", "1",
    "-max_interleave_delta", "0", "-use_editlist", "0", "-write_prft", "0", "pipe:1"];
}
function sendAudio(client, bytes) {
  if (client.readyState !== WebSocket.OPEN) return;
  if (client.bufferedAmount > 512 * 1024) { client.terminate(); return; }
  client.send(bytes);
}
function createAudioRelay({ connect = () => net.connect({ path: SOCKET }),
  spawnEncoder = () => spawn("/usr/bin/ffmpeg", encoderArgs(), { stdio: ["pipe", "pipe", "pipe"], shell: false }), reconnectMs = 1000, idleMs = 1000,
  spawnAacEncoder = () => spawn("/usr/bin/ffmpeg", encoderArgs("aac"), { stdio: ["pipe", "pipe", "pipe"], shell: false }),
  keepCapture = false, volume = () => 100, clock = () => performance.now(), liveTimeoutMs = 1500 } = {}) {
  const server = http.createServer((_req, res) => res.writeHead(404).end());
  const ws = new WebSocketServer({ server, maxPayload: 1024 });
  let current, retry, idle, closing = false, init, aacIdle;
  const sendLive = (client, bytes) => {
    if (!client.live || !client.ready || client.inflight.size >= 8) return;
    const id = ++client.serial;
    client.inflight.set(id, clock());
    sendAudio(client, JSON.stringify({ type: 'audio-frame', id, stamp: client.stamp }));
    sendAudio(client, bytes);
  };
  const broadcast = (client, bytes, initialization = init) => {
    if (!client.live) { sendAudio(client, bytes); return; }
    if (!client.ready) return;
    if (!client.initialized) {
      if (!initialization) return;
      client.initialized = true; sendLive(client, initialization);
      if (bytes === initialization) return;
    }
    // Independently decodable audio fragments need no unsent history. Drop when
    // credit is exhausted, keep draining the shared capture for healthy viewers.
    sendLive(client, bytes);
  };
  const watchdog = setInterval(() => {
    for (const client of ws.clients) {
      if (client.live && ((!client.ready && clock() - client.born > liveTimeoutMs) ||
          [...client.inflight.values()].some(at => clock() - at > liveTimeoutMs))) client.terminate();
    }
  }, 250); watchdog.unref();
  function start() {
    if (closing) return;
    const source = connect(), encoder = spawnEncoder(), pcm = new RawPcmStream(), gain = new PcmGainStream(volume);
    const ctx = { source, encoder, pcm, gain, ending: false, aac: null }; current = ctx; init = null;
    const stop = () => {
      if (ctx.ending) return; ctx.ending = true; init = null;
      source.destroy(); pcm.destroy(); gain.destroy(); encoder.stdin.destroy();
      ctx.aac?.stop();
      for (const client of ws.clients) client.close(1012, "Audio reconnecting");
      encoder.kill("SIGTERM");
      ctx.killTimer = setTimeout(() => encoder.kill("SIGKILL"), 2000); ctx.killTimer.unref();
    };
    ctx.stop = stop;
    encoder.once("close", () => {
      stop(); clearTimeout(ctx.killTimer); ctx.resolveStop?.();
      if (current !== ctx) return; current = null; init = null;
      if (!closing && (ws.clients.size || keepCapture)) { retry = setTimeout(start, reconnectMs); retry.unref(); }
    });
    const parser = new Mp4Fragments((type, bytes) => {
      if (ctx.ending) return;
      if (type === "init") init = bytes;
      for (const client of ws.clients) if (!client.aac) broadcast(client, bytes);
    });
    source.once("error", stop); source.once("close", stop); source.once("end", stop);
    pcm.once("error", stop); gain.once("error", stop); encoder.once("error", stop); encoder.stdin.once("error", stop);
    encoder.stdout.on("data", bytes => { try { parser.push(bytes); } catch { stop(); } });
    encoder.stderr.on("data", () => {});
    source.pipe(pcm).pipe(gain).pipe(encoder.stdin);
  }
  function startAac() {
    const ctx = current;
    if (!ctx || ctx.ending || ctx.aac || closing) return;
    const encoder = spawnAacEncoder();
    const branch = { encoder, init: null, ending: false }; ctx.aac = branch;
    const pump = bytes => {
      // Stop only the optional branch if its input stops draining; never stall Android or FLAC.
      if (branch.ending) return;
      if (encoder.stdin.writableLength + bytes.length > 192 * 1024) { branch.stop(); return; }
      encoder.stdin.write(bytes);
    };
    branch.stop = () => {
      if (branch.ending) return; branch.ending = true; branch.init = null;
      ctx.gain.off('data', pump); encoder.stdin.destroy();
      for (const client of ws.clients) if (client.aac) client.close(1012, 'AAC reconnecting');
      encoder.kill('SIGTERM'); branch.killTimer = setTimeout(() => encoder.kill('SIGKILL'), 2000); branch.killTimer.unref();
    };
    const parser = new Mp4Fragments((type, bytes) => {
      if (branch.ending) return;
      if (type === 'init') branch.init = bytes;
      for (const client of ws.clients) if (client.aac) broadcast(client, bytes, branch.init);
    });
    encoder.stdout.on('data', bytes => { try { parser.push(bytes); } catch (_) { branch.stop(); } });
    encoder.stderr.on('data', () => {}); encoder.once('error', branch.stop); encoder.stdin.once('error', branch.stop);
    encoder.once('close', () => {
      branch.stop(); clearTimeout(branch.killTimer); branch.resolveStop?.();
      if (ctx.aac === branch) ctx.aac = null;
    });
    ctx.gain.on('data', pump);
  }
  ws.on("connection", client => {
    if (ws.clients.size > 4) { client.close(1013, "Audio viewer limit"); return; }
    clearTimeout(idle); clearTimeout(retry);
    if (!current) start();
    client.aac = client.protocol === "droiddeck-aac-live-v1";
    client.live = client.aac || client.protocol === "droiddeck-audio-live-v1";
    if (client.aac) { clearTimeout(aacIdle); startAac(); }
    client.ready = false; client.initialized = false; client.inflight = new Map(); client.serial = 0; client.born = clock();
    if (init && !client.live) sendAudio(client, init);
    client.on('message', (bytes, binary) => {
      let value; try { if (!binary && client.live) value = JSON.parse(bytes.toString()); } catch (_) {}
      const ready = value?.type === 'audio-ready' && !client.ready && Object.keys(value).sort().join(',') === 'stamp,type';
      const ack = value?.type === 'audio-ack' && client.ready && Object.keys(value).sort().join(',') === 'id,stamp,type' && Number.isSafeInteger(value.id);
      if ((!ready && !ack) || !Number.isFinite(value.stamp) || value.stamp < 0) { client.close(1008, 'Audio is read-only'); return; }
      if (ack && !client.inflight.has(value.id)) return;
      if (ack) client.inflight.delete(value.id);
      client.stamp = value.stamp; client.ready = true;
      const initialization = client.aac ? current?.aac?.init : init;
      if (!client.initialized && initialization) broadcast(client, initialization, initialization);
    }); client.on("error", () => {});
    client.once("close", () => {
      if (![...ws.clients].some(c => c.aac && c.readyState === WebSocket.OPEN)) {
        clearTimeout(aacIdle); aacIdle = setTimeout(() => {
          if (![...ws.clients].some(c => c.aac && c.readyState === WebSocket.OPEN)) current?.aac?.stop();
        }, idleMs); aacIdle.unref();
      }
      if (ws.clients.size) return;
      // The supervised Waydroid path keeps draining PCM between viewers: tearing
      // down REMOTE_SUBMIX repeatedly can wedge this image's ALSA/Pulse output.
      if (keepCapture) return;
      clearTimeout(retry); idle = setTimeout(() => { if (!ws.clients.size) current?.stop(); }, idleMs); idle.unref();
    });
  });
  server.shutdown = async () => {
    closing = true; clearInterval(watchdog); clearTimeout(retry); clearTimeout(idle); clearTimeout(aacIdle);
    if (current) {
      const branch = current.aac;
      const stopped = new Promise(resolve => { current.resolveStop = resolve; });
      const aacStopped = branch ? new Promise(resolve => { branch.resolveStop = resolve; }) : Promise.resolve();
      current.stop(); await Promise.all([stopped, aacStopped]);
    }
    for (const client of ws.clients) client.terminate();
    await new Promise(resolve => ws.close(resolve)); await new Promise(resolve => server.close(resolve));
  };
  return server;
}
async function main({ configuration, args = process.argv.slice(2) } = {}) {
  if (!Array.isArray(args) || args.length) throw new Error('Fixed audio relay requires no arguments');
  configuration = await loadWorkerConfiguration({ role: 'user', read: () => configuration });
  const layout = captureLayout(configuration);
  const volume = gainEnabled(process.env) ? createVolumeProvider({ filename: layout.audioSettingsFile, onError: message => console.error(message) }) : () => 100;
  const server = createAudioRelay({ keepCapture: true, volume }); server.listen(9093, "127.0.0.1");
  for (const signal of ["SIGTERM", "SIGINT"]) process.once(signal, () => server.shutdown().then(() => process.exit(0)));
  return server;
}
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
module.exports = { createAudioRelay, encoderArgs, sendAudio, main };
