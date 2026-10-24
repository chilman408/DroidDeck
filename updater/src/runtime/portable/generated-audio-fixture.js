// SPDX-License-Identifier: GPL-3.0-only
// Disposable installation proof; serves only the existing generated public clip.
const fs = require('node:fs'), http = require('node:http');
const crypto = require('node:crypto');
const { range, sha } = require('./generated-playback');
const ROOT = '/opt/droiddeck/generated-audio-validation-20261014bn';
const DIRECTORY = '/home/hilman/.local/share/droiddeck-generated-playback-20261014bi';
const DIGEST = 'bc0486708a3552da97f57d1ae026f81c12cf688201429eb0607d49a22c2a5681';
const ORIGIN = 'http://192.168.240.1:18187';
const UNIT = 'droiddeck-generated-audio-fixture-20261014bn.service';
const PAGE = `<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DroidDeck generated audio validation</title><style>
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:black}
video,button{position:absolute;inset:0;width:100%;height:100%;border:0}
video{object-fit:contain}button{background:#121212;color:white;font:24px sans-serif}
</style><video id="v" src="/fixture.mp4" preload="auto" loop playsinline></video>
<button id="b">Play generated test tone</button><script>
const v=document.getElementById('v'),b=document.getElementById('b');
const mark=(gesture,playError='none')=>fetch('/ready',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({pageToken:'__PAGE_TOKEN__',gesture,playError})}).catch(()=>{});
b.onclick=()=>{mark(true);v.muted=false;v.play().then(()=>{b.hidden=true;mark(true)}).catch(e=>mark(true,
['NotAllowedError','NotSupportedError','AbortError'].includes(e.name)?e.name:'other'))};
requestAnimationFrame(()=>requestAnimationFrame(()=>mark(false)));
setInterval(()=>{if(!v.videoWidth)return;fetch('/event',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({width:v.videoWidth,height:v.videoHeight,time:v.currentTime,muted:v.muted,
playing:!v.paused&&!v.ended,code:v.error?.code||0,pageToken:'__PAGE_TOKEN__'})}).catch(()=>{})},200);</script>`;
function event(value) {
  return value && Object.keys(value).sort().join() === 'code,height,muted,pageToken,playing,time,width' &&
    value.width === 640 && value.height === 360 && value.code === 0 &&
    typeof value.muted === 'boolean' && typeof value.playing === 'boolean' &&
    typeof value.pageToken === 'string' && /^[0-9a-f]{32}$/.test(value.pageToken) &&
    Number.isFinite(value.time) && value.time >= 0 && value.time <= 8.1 ? value : null;
}
function createServer(video, { origin = ORIGIN, clients = ['192.168.240.1', '192.168.240.112'], now = Date.now,
  pageToken = crypto.randomBytes(16).toString('hex') } = {}) {
  if (!/^[0-9a-f]{32}$/.test(pageToken)) throw Error('Generated page identity required');
  let last, progress = 0, posts = 0, requests = 0, ready = false, gestureObserved = false, playError = 'none';
  const server = http.createServer(async (req, res) => {
    res.setHeader('Cache-Control', 'no-store'); res.setHeader('X-Content-Type-Options', 'nosniff');
    if (!clients.includes(req.socket.remoteAddress) || req.headers.host !== new URL(origin).host) { res.writeHead(403).end(); return; }
    if (req.method === 'GET' && req.url === '/?proof=' + pageToken) {
      res.setHeader('Content-Type', 'text/html'); res.end(PAGE.replaceAll('__PAGE_TOKEN__', pageToken)); return;
    }
    if (['GET', 'HEAD'].includes(req.method) && req.url === '/fixture.mp4') {
      const selected = range(req.headers.range, video.length);
      if (!selected) { res.writeHead(416, { 'Content-Range': `bytes */${video.length}` }).end(); return; }
      requests++; const { start, end, partial } = selected;
      res.setHeader('Content-Type', 'video/mp4'); res.setHeader('Accept-Ranges', 'bytes');
      res.setHeader('Content-Length', end - start + 1);
      if (partial) res.setHeader('Content-Range', `bytes ${start}-${end}/${video.length}`);
      res.writeHead(partial ? 206 : 200).end(req.method === 'HEAD' ? undefined : video.subarray(start, end + 1)); return;
    }
    if (req.method === 'GET' && req.url === '/status') {
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify({ kind: 'generated-audio-fixture', sha256: sha(video), pageToken, ready,
        active: ready && requests > 0 && last?.muted === false && last.playing && progress > 0 && now() - progress < 1500,
        reports: posts, gestureObserved, playError, playback: last || null })); return;
    }
    if (req.method !== 'POST' || !['/event', '/ready'].includes(req.url)) { res.writeHead(404).end(); return; }
    if (req.headers.origin !== origin || req.headers['content-type'] !== 'application/json' || posts >= 350) { res.writeHead(403).end(); return; }
    try {
      let body = '';
      for await (const chunk of req) { body += chunk.toString('utf8'); if (Buffer.byteLength(body) > 512) { res.writeHead(413).end(); return; } }
      const decoded = JSON.parse(body);
      if (req.url === '/ready') {
        if (!decoded || Object.keys(decoded).sort().join() !== 'gesture,pageToken,playError' ||
            decoded.pageToken !== pageToken || typeof decoded.gesture !== 'boolean' ||
            !['none', 'NotAllowedError', 'NotSupportedError', 'AbortError', 'other'].includes(decoded.playError)) { res.writeHead(403).end(); return; }
        ready = true; gestureObserved ||= decoded.gesture; playError = decoded.playError; posts++; res.writeHead(204).end(); return;
      }
      const value = event(decoded);
      if (!value) { res.writeHead(400).end(); return; }
      if (value.pageToken !== pageToken) { res.writeHead(403).end(); return; }
      if (last && !value.muted && value.playing && value.time !== last.time) progress = now();
      last = value; posts++; res.writeHead(204).end();
    } catch (_) { if (!res.headersSent) res.writeHead(400).end(); }
  });
  server.requestTimeout = 3000; server.headersTimeout = 3000; server.keepAliveTimeout = 1000;
  return server;
}
function readClip() {
  const directory = fs.lstatSync(DIRECTORY);
  if (!directory.isDirectory() || directory.uid !== 1000 || directory.gid !== 1000 || (directory.mode & 0o777) !== 0o700 ||
      fs.readdirSync(DIRECTORY).sort().join() !== 'fixture.mp4,manifest.json') throw Error('Generated directory changed');
  const filename = DIRECTORY + '/fixture.mp4', before = fs.lstatSync(filename);
  if (!before.isFile() || before.uid !== 1000 || before.gid !== 1000 || before.nlink !== 1 ||
      (before.mode & 0o777) !== 0o600 || before.size !== 963402) throw Error('Generated clip changed');
  const fd = fs.openSync(filename, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
  try {
    const clip = fs.readFileSync(fd);
    for (const info of [fs.fstatSync(fd), fs.lstatSync(filename)]) if (['dev', 'ino', 'size', 'mtimeMs', 'ctimeMs', 'mode', 'uid', 'gid', 'nlink']
      .some(key => info[key] !== before[key])) throw Error('Generated clip identity changed');
    if (sha(clip) !== DIGEST) throw Error('Generated provenance changed');
    return clip;
  } finally { fs.closeSync(fd); }
}
async function serve() {
  if (process.platform !== 'linux' || process.getuid() !== 1000 || __dirname !== ROOT ||
      process.argv.slice(2).join() !== '--serve') throw Error('Fixed disposable generated audio fixture required');
  const server = createServer(readClip());
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(18187, '192.168.240.1', resolve); });
  const close = () => { server.closeAllConnections(); server.close(); };
  const timer = setTimeout(close, 55000); server.once('close', () => clearTimeout(timer));
  process.once('SIGTERM', close); process.once('SIGINT', close);
  console.log('Generated audio fixture on private Android bridge');
}
if (require.main === module) serve().catch(() => { console.error('Generated audio fixture refused'); process.exitCode = 1; });
module.exports = { ROOT, DIRECTORY, DIGEST, ORIGIN, UNIT, PAGE, event, createServer };
