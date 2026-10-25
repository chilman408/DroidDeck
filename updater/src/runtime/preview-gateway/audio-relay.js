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
function encoderArgs() {
  return ["-hide_banner", "-loglevel", "error", "-nostdin", "-probesize", "32", "-analyzeduration", "0",
    "-f", "s16le", "-ar", "48000", "-ac", "2",
    "-i", "pipe:0", "-vn", "-c:a", "flac", "-compression_level", "0", "-strict", "experimental",
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
  keepCapture = false, volume = () => 100 } = {}) {
  const server = http.createServer((_req, res) => res.writeHead(404).end());
  const ws = new WebSocketServer({ server, maxPayload: 1024 });
  let current, retry, idle, closing = false, init;
  function start() {
    if (closing) return;
    const source = connect(), encoder = spawnEncoder(), pcm = new RawPcmStream(), gain = new PcmGainStream(volume);
    const ctx = { source, encoder, pcm, gain, ending: false }; current = ctx; init = null;
    const stop = () => {
      if (ctx.ending) return; ctx.ending = true; init = null;
      source.destroy(); pcm.destroy(); gain.destroy(); encoder.stdin.destroy();
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
      for (const client of ws.clients) sendAudio(client, bytes);
    });
    source.once("error", stop); source.once("close", stop); source.once("end", stop);
    pcm.once("error", stop); gain.once("error", stop); encoder.once("error", stop); encoder.stdin.once("error", stop);
    encoder.stdout.on("data", bytes => { try { parser.push(bytes); } catch { stop(); } });
    encoder.stderr.on("data", () => {});
    source.pipe(pcm).pipe(gain).pipe(encoder.stdin);
  }
  ws.on("connection", client => {
    if (ws.clients.size > 4) { client.close(1013, "Audio viewer limit"); return; }
    clearTimeout(idle); clearTimeout(retry);
    if (!current) start();
    if (init) sendAudio(client, init);
    client.on("message", () => client.close(1008, "Audio is read-only")); client.on("error", () => {});
    client.once("close", () => {
      if (ws.clients.size) return;
      // The supervised Waydroid path keeps draining PCM between viewers: tearing
      // down REMOTE_SUBMIX repeatedly can wedge this image's ALSA/Pulse output.
      if (keepCapture) return;
      clearTimeout(retry); idle = setTimeout(() => { if (!ws.clients.size) current?.stop(); }, idleMs); idle.unref();
    });
  });
  server.shutdown = async () => {
    closing = true; clearTimeout(retry); clearTimeout(idle);
    if (current) { const stopped = new Promise(resolve => { current.resolveStop = resolve; }); current.stop(); await stopped; }
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
