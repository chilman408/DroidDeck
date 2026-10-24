// SPDX-License-Identifier: GPL-3.0-only
// Generated tone only; no Android capture, app data or settings changes.
const http = require('node:http'), fs = require('node:fs'), crypto = require('node:crypto');
const { PassThrough } = require('node:stream');
const ROOT = '/opt/droiddeck/browser-audio-validation-20261014bp';
const ORIGIN = 'http://127.0.0.1:18188';
const ASSETS = Object.freeze({
  '/js/audio/audioplayback.js': '91c8d97c8ce2ed297a5535f5fdfdfe48bac90e568cda727ebf21dd9c15152e01',
  '/js/audio/audio-ws-worker.js': 'b3dda5d2bcab80ee633faa21ab0abb58bba5b80ac5d379359e81eb49f4ae5a24',
});
const PAGE = '<!doctype html><meta charset="utf-8"><title>Generated audio validation</title>' +
  '<button id="play">Play generated test</button><script src="/fixture.js"></script>' +
  '<script src="/js/audio/audioplayback.js"></script>';
const SCRIPT = `(() => {
  let context, analyser, gain;
  window.audioWebsocketUrl = 'ws://127.0.0.1:18188/tone';
  window.addEventListener('audio-el-created', () => {
    context = new AudioContext({ sampleRate: 48000 });
    analyser = context.createAnalyser(); analyser.fftSize = 2048; analyser.smoothingTimeConstant = 0;
    gain = context.createGain(); gain.gain.value = 0;
    context.createMediaElementSource(window.__liveAudioEl).connect(analyser);
    analyser.connect(gain); gain.connect(context.destination);
  });
  document.getElementById('play').onclick = async () => {
    window.setAudioEnabled(true); window.setAudioVolume(1); window.primeAudioFromGesture();
    await context.resume(); window.startAudioPlayback();
  };
  window.generatedAudioFacts = () => {
    const audio = window.__liveAudioEl;
    const data = new Float32Array(analyser?.frequencyBinCount || 0);
    analyser?.getFloatFrequencyData(data);
    let bin = 0;
    for (let i = 1; i < data.length; i++) if (data[i] > data[bin]) bin = i;
    const peakHz = context ? bin * context.sampleRate / analyser.fftSize : 0;
    const peakDb = Number.isFinite(data[bin]) ? data[bin] : -200;
    return { time: audio?.currentTime || 0, paused: audio?.paused ?? true,
      error: audio?.error?.code || null, sampleRate: context?.sampleRate || 0,
      peakHz, peakDb, toneMatches: Math.abs(peakHz - 1000) <= 35 && peakDb > -75,
      outputMuted: gain?.gain.value === 0, savedAudio: false };
  };
})();`;
function generatedSource({ interval = setInterval, cancel = clearInterval } = {}) {
  const source = new PassThrough({ highWaterMark: 65536 }), chunk = Buffer.alloc(3840);
  for (let i = 0; i < 960; i++) {
    const sample = Math.round(512 * Math.sin(2 * Math.PI * i / 48));
    chunk.writeInt16LE(sample, i * 4); chunk.writeInt16LE(sample, i * 4 + 2);
  }
  source.write(Buffer.from([0, 0x72, 0x61, 0x77]));
  const timer = interval(() => { if (!source.write(chunk)) source.destroy(); }, 20);
  source.once('close', () => cancel(timer)); return source;
}
function permitted(request, upgrade = false) {
  return request.headers.host === '127.0.0.1:18188' && request.method === 'GET' &&
    (upgrade ? request.url === '/tone' && request.headers.origin === ORIGIN :
      !request.headers.origin || request.headers.origin === ORIGIN);
}
async function createFixture({ createRelay, WebSocket, WebSocketServer, assets = new Map() } = {}) {
  if (![createRelay, WebSocket, WebSocketServer].every(value => typeof value === 'function') ||
      Object.keys(ASSETS).some(name => !Buffer.isBuffer(assets.get(name)))) throw Error('Fixed generated fixture adapters required');
  const relay = createRelay({ connect: generatedSource, keepCapture: false, idleMs: 50, volume: () => 100 });
  await new Promise((resolve, reject) => { relay.once('error', reject); relay.listen(0, '127.0.0.1', resolve); });
  const server = http.createServer((request, response) => {
    if (!permitted(request)) { response.writeHead(403).end(); return; }
    const name = request.url;
    const body = name === '/' ? PAGE : name === '/fixture.js' ? SCRIPT : assets.get(name);
    if (body === undefined) { response.writeHead(404).end(); return; }
    response.writeHead(200, { 'Content-Type': name === '/' ? 'text/html; charset=utf-8' : 'text/javascript; charset=utf-8',
      'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
      'Content-Security-Policy': "default-src 'none'; script-src 'self'; worker-src 'self'; connect-src 'self'; media-src blob:" });
    response.end(body);
  });
  const sockets = new WebSocketServer({ noServer: true, maxPayload: 1024, perMessageDeflate: false });
  const pairs = new Set();
  server.on('upgrade', (request, socket, head) => {
    if (!permitted(request, true) || pairs.size) { socket.destroy(); return; }
    sockets.handleUpgrade(request, socket, head, client => {
      const upstream = new WebSocket('ws://127.0.0.1:' + relay.address().port);
      const pair = { client, upstream }; pairs.add(pair);
      let stopped = false;
      const close = () => { if (stopped) return; stopped = true; pairs.delete(pair); client.terminate(); upstream.terminate(); };
      upstream.on('message', (bytes, binary) => {
        if (!binary || bytes.length > 1024 * 1024 || client.bufferedAmount > 512 * 1024) { close(); return; }
        if (client.readyState === WebSocket.OPEN) client.send(bytes);
      });
      client.on('message', close); client.once('close', close); client.on('error', close);
      upstream.once('close', close); upstream.on('error', close);
    });
  });
  let closing;
  server.shutdown = () => closing ||= (async () => {
    for (const { client, upstream } of pairs) { client.terminate(); upstream.terminate(); }
    await new Promise(resolve => sockets.close(resolve)); await relay.shutdown();
    if (server.listening) await new Promise(resolve => { server.close(resolve); server.closeAllConnections(); });
  })();
  return server;
}
function readPinned(filename, pin, maximum = 131072) {
  const stat = fs.lstatSync(filename);
  if (!stat.isFile() || stat.uid !== 0 || stat.mode & 0o022 || stat.nlink !== 1 || stat.size > maximum) throw Error('Pinned fixture source required');
  const bytes = require('/opt/droiddeck/runtime/portable/read-bounded').readBoundedRegular(filename, maximum);
  if (crypto.createHash('sha256').update(bytes).digest('hex') !== pin) throw Error('Pinned fixture source changed');
  return bytes;
}
async function main(args) {
  if (process.platform !== 'linux' || process.getuid() !== 1000 || __dirname !== ROOT || args.length !== 1 || args[0] !== '--serve') throw Error('Fixed disposable generated fixture required');
  const config = require('/opt/droiddeck/runtime/portable/protected-config').readProtectedRuntimeConfig();
  if (config.user !== 'hilman' || config.uid !== 1000 || config.lanAddress !== '192.168.2.215') throw Error('Disposable VM required');
  const manifest = readPinned('/opt/droiddeck/runtime/bundle-manifest.json', '9de6dc880c7608aa3f959b94f3b72537eeae9597dc02002038015195ad224f71', 1048576);
  const records = JSON.parse(manifest).files;
  for (const name of ['audio-relay', 'audio-stream', 'audio-gain']) {
    const file = 'preview-gateway/' + name + '.js', record = records.find(item => item.path === file);
    if (!record) throw Error('Missing pinned audio dependency'); readPinned('/opt/droiddeck/runtime/' + file, record.sha256);
  }
  readPinned('/opt/droiddeck/frontend/bundle-manifest.json', 'b7b943a36015737ceed2d03e084ebf3933784fe9ba75fd62c1877e942817a9ef', 1048576);
  const assets = new Map(Object.entries(ASSETS).map(([name, pin]) => [name, readPinned('/opt/droiddeck/frontend' + name, pin)]));
  const { WebSocket, WebSocketServer } = require('/opt/droiddeck/runtime/preview-gateway/node_modules/ws');
  const server = await createFixture({ assets, WebSocket, WebSocketServer,
    createRelay: require('/opt/droiddeck/runtime/preview-gateway/audio-relay').createAudioRelay });
  const timer = setTimeout(() => void server.shutdown(), 35000);
  for (const signal of ['SIGTERM', 'SIGINT']) process.once(signal, () => { clearTimeout(timer); void server.shutdown(); });
  try { await new Promise((resolve, reject) => { server.once('error', reject); server.listen(18188, '127.0.0.1', resolve); }); }
  catch (error) { clearTimeout(timer); await server.shutdown(); throw error; }
  server.once('close', () => clearTimeout(timer));
  console.log('Generated browser audio fixture ready; Android capture disabled');
}
if (require.main === module) main(process.argv.slice(2)).catch(() => { console.error('Generated browser audio fixture rejected'); process.exitCode = 1; });
module.exports = { generatedSource, permitted, createFixture, main };
