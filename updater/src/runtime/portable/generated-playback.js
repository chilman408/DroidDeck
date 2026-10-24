// SPDX-License-Identifier: GPL-3.0-only
// Disposable-VM validation only. Serves generated media, never an arbitrary file or URL.
const fs = require('node:fs'), http = require('node:http'), path = require('node:path');
const { execFileSync } = require('node:child_process');
const crypto = require('node:crypto');
const ROOT = '/opt/droiddeck/generated-playback-validation-20261014bi';
const DIRECTORY = '/home/hilman/.local/share/droiddeck-generated-playback-20261014bi';
const ADDRESS = '192.168.240.1', PORT = 18186, ORIGIN = `http://${ADDRESS}:${PORT}`;
const UNIT = 'droiddeck-generated-playback-20261014bi.service';
const sha = data => crypto.createHash('sha256').update(data).digest('hex');
function generateArgs(directory) {
  return ['-v', 'error', '-n', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=30:duration=8',
    '-f', 'lavfi', '-i', 'sine=frequency=1000:sample_rate=48000:duration=8', '-t', '8',
    '-c:v', 'libx264', '-profile:v', 'baseline', '-pix_fmt', 'yuv420p', '-bf', '0', '-g', '30',
    '-threads', '2', '-c:a', 'aac', '-b:a', '128k', '-ac', '2', '-af', 'volume=0.05',
    '-movflags', '+faststart', path.posix.join(directory, 'fixture.mp4')];
}
function run(file, args, timeout = 10000) {
  return execFileSync(file, args, { timeout, maxBuffer: 4 * 1024 * 1024, stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, NODE_OPTIONS: '', NODE_PATH: '' }, windowsHide: true });
}
function readOwned(filename, maximum) {
  const before = fs.lstatSync(filename);
  if (!before.isFile() || before.uid !== 1000 || before.gid !== 1000 || before.nlink !== 1 ||
      (before.mode & 0o777) !== 0o600 || before.size < 1 || before.size > maximum) throw Error('Unsafe generated file');
  const fd = fs.openSync(filename, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
  try {
    const data = fs.readFileSync(fd), after = fs.fstatSync(fd), named = fs.lstatSync(filename);
    for (const value of [after, named]) if (['dev', 'ino', 'size', 'mtimeMs', 'ctimeMs', 'mode', 'uid', 'gid', 'nlink']
      .some(key => value[key] !== before[key])) throw Error('Generated file changed');
    if (data.length !== before.size) throw Error('Generated size changed');
    return data;
  } finally { fs.closeSync(fd); }
}
function mediaFacts(value) {
  const s = value?.streams, v = s?.find(x => x.codec_type === 'video'), a = s?.find(x => x.codec_type === 'audio');
  if (!Array.isArray(s) || s.length !== 2 || v?.codec_name !== 'h264' || v.width !== 640 || v.height !== 360 ||
      v.pix_fmt !== 'yuv420p' || v.r_frame_rate !== '30/1' || Number(v.duration) !== 8 ||
      Number(v.nb_read_frames) !== 240 || a?.codec_name !== 'aac' || a.sample_rate !== '48000' ||
      a.channels !== 2 || Math.abs(Number(a.duration) - 8) > .05) throw Error('Generated media shape changed');
  return { videoCodec: 'h264', width: 640, height: 360, frames: 240, audioCodec: 'aac', durationSeconds: 8 };
}
function range(header, length) {
  if (header === undefined) return { start: 0, end: length - 1, partial: false };
  const match = /^bytes=(\d{1,8})-(\d{0,8})$/.exec(header);
  if (!match) return null;
  const start = Number(match[1]), end = match[2] ? Number(match[2]) : length - 1;
  return start < length && end >= start && end < length ? { start, end, partial: true } : null;
}
function report(value) {
  if (!value || Object.keys(value).sort().join() !== 'code,frames,height,playing,time,width' ||
      value.width !== 640 || value.height !== 360 || value.code !== 0 || typeof value.playing !== 'boolean' ||
      !Number.isSafeInteger(value.frames) || value.frames < 0 || value.frames > 20000 ||
      !Number.isFinite(value.time) || value.time < 0 || value.time > 8.1) return null;
  return value;
}
const PAGE = `<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DroidDeck generated playback validation</title>
<style>html,body{margin:0;background:#000;width:100%;height:100%;overflow:hidden}video{width:100%;height:100%;object-fit:contain}</style>
<video id="v" src="/fixture.mp4" muted autoplay loop playsinline></video><script>
const v=document.getElementById('v'); let frames=0;
if(v.requestVideoFrameCallback){const f=(t,m)=>{frames=m.presentedFrames;v.requestVideoFrameCallback(f)};v.requestVideoFrameCallback(f)}
setInterval(()=>{if(!v.videoWidth)return;fetch('/event',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({width:v.videoWidth,height:v.videoHeight,frames:frames||v.getVideoPlaybackQuality?.().totalVideoFrames||0,
time:v.currentTime,code:v.error?.code||0,playing:!v.paused&&!v.ended})}).catch(()=>{})},200);
v.play().catch(()=>{});</script>`;
function createServer(video, { origin = ORIGIN, clients = [ADDRESS, '192.168.240.112'], now = Date.now } = {}) {
  let last, progressAt = 0, posts = 0, requests = 0;
  const server = http.createServer(async (req, res) => {
    res.setHeader('Cache-Control', 'no-store'); res.setHeader('X-Content-Type-Options', 'nosniff');
    if (!clients.includes(req.socket.remoteAddress) || req.headers.host !== new URL(origin).host) { res.writeHead(403).end(); return; }
    if (req.method === 'GET' && req.url === '/') { res.setHeader('Content-Type', 'text/html'); res.end(PAGE); return; }
    if (['GET', 'HEAD'].includes(req.method) && req.url === '/fixture.mp4') {
      const r = range(req.headers.range, video.length);
      if (!r) { res.writeHead(416, { 'Content-Range': `bytes */${video.length}` }).end(); return; }
      requests++; res.setHeader('Content-Type', 'video/mp4'); res.setHeader('Accept-Ranges', 'bytes');
      res.setHeader('Content-Length', r.end - r.start + 1);
      if (r.partial) res.setHeader('Content-Range', `bytes ${r.start}-${r.end}/${video.length}`);
      res.writeHead(r.partial ? 206 : 200).end(req.method === 'HEAD' ? undefined : video.subarray(r.start, r.end + 1)); return;
    }
    if (req.method === 'GET' && req.url === '/status') {
      res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify({ kind: 'generated-playback',
        sha256: sha(video), active: requests > 0 && last?.playing === true && progressAt > 0 && now() - progressAt < 1500,
        reports: posts, frames: last?.frames || 0, mediaRequests: requests, error: last?.code || 0 })); return;
    }
    if (req.method !== 'POST' || req.url !== '/event') { res.writeHead(404).end(); return; }
    if (req.headers.origin !== origin || req.headers['content-type'] !== 'application/json' || posts >= 350) { res.writeHead(403).end(); return; }
    try {
      let body = '';
      for await (const chunk of req) { body += chunk.toString('utf8'); if (Buffer.byteLength(body) > 512) { res.writeHead(413).end(); return; } }
      const value = report(JSON.parse(body));
      if (!value) { res.writeHead(400).end(); return; }
      if (last && value.playing && value.frames > last.frames && value.time !== last.time) progressAt = now();
      last = value; posts++; res.writeHead(204).end();
    } catch (_) { if (!res.headersSent) res.writeHead(400).end(); }
  });
  server.requestTimeout = 3000; server.headersTimeout = 3000; server.keepAliveTimeout = 1000;
  return server;
}
function generate() {
  fs.mkdirSync(DIRECTORY, { mode: 0o700 });
  const mask = process.umask(0o077);
  try {
    run('/usr/bin/ffmpeg', generateArgs(DIRECTORY), 60000);
    const facts = mediaFacts(JSON.parse(run('/usr/bin/ffprobe', ['-v', 'error', '-count_frames',
      '-show_entries', 'stream=codec_type,codec_name,width,height,pix_fmt,nb_read_frames,r_frame_rate,duration,sample_rate,channels',
      '-of', 'json', DIRECTORY + '/fixture.mp4'], 30000)));
    const video = readOwned(DIRECTORY + '/fixture.mp4', 8 * 1024 * 1024);
    const manifest = { version: 1, kind: 'generated-playback', ...facts, bytes: video.length, sha256: sha(video) };
    fs.writeFileSync(DIRECTORY + '/manifest.json', JSON.stringify(manifest) + '\n', { flag: 'wx', mode: 0o600 });
    console.log(JSON.stringify(manifest));
  } finally { process.umask(mask); }
}
async function serve() {
  const directory = fs.lstatSync(DIRECTORY);
  if (!directory.isDirectory() || directory.uid !== 1000 || directory.gid !== 1000 || (directory.mode & 0o777) !== 0o700 ||
      fs.readdirSync(DIRECTORY).sort().join() !== 'fixture.mp4,manifest.json') throw Error('Generated directory changed');
  const video = readOwned(DIRECTORY + '/fixture.mp4', 8 * 1024 * 1024);
  const manifest = JSON.parse(readOwned(DIRECTORY + '/manifest.json', 2048));
  if (manifest.kind !== 'generated-playback' || manifest.sha256 !== sha(video) || manifest.bytes !== video.length) throw Error('Generated receipt changed');
  const server = createServer(video);
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(PORT, ADDRESS, resolve); });
  console.log('Generated playback fixture listening on private Android bridge');
  const close = () => { server.closeAllConnections(); server.close(); };
  const timer = setTimeout(close, 55000);
  server.once('close', () => clearTimeout(timer));
  process.once('SIGTERM', close); process.once('SIGINT', close);
}
if (require.main === module) (async () => {
  if (process.platform !== 'linux' || process.getuid() !== 1000 || process.argv.length !== 3 ||
      !['--generate', '--serve'].includes(process.argv[2])) throw Error('Fixed user generated validation required');
  if (process.argv[2] === '--generate') generate(); else await serve();
})().catch(() => { console.error('Generated playback validation refused'); process.exitCode = 1; });
module.exports = { ROOT, DIRECTORY, UNIT, ORIGIN, generateArgs, mediaFacts, range, report, createServer, sha };
