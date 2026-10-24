// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs/promises');
const { constants } = require('node:fs');
const crypto = require('node:crypto');
const http = require('node:http');
const { execute } = require('./host-adapter');
const DIRECTORY = '/home/hilman/teslaandroid-waydroid/generated-transition-stage-20261010m';
const GPU_DIRECTORY = '/home/hilman/teslaandroid-waydroid/gpu-render-generated-20261010b';
const AV1_DIRECTORY = '/home/hilman/teslaandroid-waydroid/generated-av1-surface-20261010d';
const AV1_GPU_DIRECTORY = '/home/hilman/teslaandroid-waydroid/generated-av1-gpu-rgbx-20261010e';
const AV1_LAYERS_DIRECTORY = '/home/hilman/teslaandroid-waydroid/generated-av1-gpu-layers-20261010g';
const AV1_OFFLINE_LAYERS_DIRECTORY = '/home/hilman/teslaandroid-waydroid/generated-av1-offline-layers-20261010g4';
const AV1_SOFTWARE_CONTROL_DIRECTORY = '/home/hilman/teslaandroid-waydroid/generated-av1-software-control-20261011h';
const AV1_GPU_CONTROL_DIRECTORY = '/home/hilman/teslaandroid-waydroid/generated-av1-gpu-control-20261011h';
const AV1_GPU_MOTION_DIRECTORY = '/home/hilman/teslaandroid-waydroid/generated-av1-gpu-motion-20261011h';
const AV1_SOFTWARE_MOTION_DIRECTORY = '/home/hilman/teslaandroid-waydroid/generated-av1-software-motion-20261011h';
const AV1_GPU_VERIFIED_MOTION_DIRECTORY = '/home/hilman/teslaandroid-waydroid/generated-av1-gpu-motion-20261011i';
const PROFILES = Object.freeze({
  '--serve': DIRECTORY, '--serve-gpu-render': GPU_DIRECTORY,
  '--serve-av1': AV1_DIRECTORY, '--serve-av1-gpu': AV1_GPU_DIRECTORY,
  '--serve-av1-layers': AV1_LAYERS_DIRECTORY, '--serve-av1-offline-layers': AV1_OFFLINE_LAYERS_DIRECTORY,
  '--serve-av1-software-control': AV1_SOFTWARE_CONTROL_DIRECTORY, '--serve-av1-gpu-control': AV1_GPU_CONTROL_DIRECTORY,
  '--serve-av1-gpu-motion': AV1_GPU_MOTION_DIRECTORY, '--serve-av1-software-motion': AV1_SOFTWARE_MOTION_DIRECTORY,
  '--serve-av1-verified-gpu-motion': AV1_GPU_VERIFIED_MOTION_DIRECTORY,
  '--serve-av1-source-software': '/home/hilman/teslaandroid-waydroid/generated-av1-source-software-20261011j',
  '--serve-av1-source-gpu': '/home/hilman/teslaandroid-waydroid/generated-av1-source-gpu-20261011j',
  '--serve-av1-source-software-retest': '/home/hilman/teslaandroid-waydroid/generated-av1-source-software-20261011j2',
  '--serve-av1-source-gpu-retest': '/home/hilman/teslaandroid-waydroid/generated-av1-source-gpu-20261011j2',
  '--serve-av1-mesa-allocator': '/home/hilman/teslaandroid-waydroid/generated-av1-mesa-allocator-20261011o',
  '--serve-av1-mesa-boot': '/home/hilman/teslaandroid-waydroid/generated-av1-mesa-boot-20261011o2',
  '--serve-av1-legacy-backpressure': '/home/hilman/teslaandroid-waydroid/generated-av1-legacy-backpressure-20261011p2',
  '--serve-av1-stream-cadence': '/home/hilman/teslaandroid-waydroid/generated-av1-stream-cadence-20261011q',
  '--serve-av1-stream-cadence-retest': '/home/hilman/teslaandroid-waydroid/generated-av1-stream-cadence-20261011q2',
  '--serve-av1-stream-cadence-final': '/home/hilman/teslaandroid-waydroid/generated-av1-stream-cadence-20261011q3',
  '--serve-av1-gpu-cadence': '/home/hilman/teslaandroid-waydroid/generated-av1-gpu-cadence-20261011r',
  '--serve-av1-rate-limit': '/home/hilman/teslaandroid-waydroid/generated-av1-rate-limit-20261011q4',
  '--serve-av1-av-sync': '/home/hilman/teslaandroid-waydroid/generated-av-sync-20261011s',
  '--serve-av1-av-sync-delay': '/home/hilman/teslaandroid-waydroid/generated-av-sync-delay-20261011s2',
  '--serve-av1-av-sync-delay-retest': '/home/hilman/teslaandroid-waydroid/generated-av-sync-delay-20261011s3',
  '--serve-av1-gpu-health': '/home/hilman/teslaandroid-waydroid/generated-av1-gpu-health-20261011u',
  '--serve-av1-gpu-boot-repair': '/home/hilman/teslaandroid-waydroid/generated-av1-gpu-boot-repair-20261017dv2'
});
const ORIGIN = 'http://127.0.0.1:18185';
const PAGE = `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Generated video transition</title>
<style>body{margin:0;background:#111;color:#fff;font:16px sans-serif;letter-spacing:0}video{width:100%;max-height:90vh;object-fit:contain}p{margin:8px}</style></head>
<body><video id="video" muted autoplay playsinline src="/fixture.mp4"></video><p id="state">Generated codec transition test</p>
<script>
const v=document.getElementById('video');let frames=0,last=-1;
function report(type){const r={type,time:v.currentTime,width:v.videoWidth,height:v.videoHeight,frames,callback:typeof v.requestVideoFrameCallback==='function',code:v.error?.code||0};fetch('/report',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(r)}).catch(()=>{});}
v.onloadedmetadata=()=>report('ready');v.onerror=()=>report('error');
v.onended=()=>{report('ended');document.getElementById('state').textContent='Generated clip complete';v.removeAttribute('src');v.load();};
if(typeof v.requestVideoFrameCallback==='function'){function frame(t,m){frames=m.presentedFrames;if(Math.floor(v.currentTime)!==last){last=Math.floor(v.currentTime);report('frame');}v.requestVideoFrameCallback(frame);}v.requestVideoFrameCallback(frame);}
else{v.ontimeupdate=()=>{frames=v.getVideoPlaybackQuality?.().totalVideoFrames||0;if(Math.floor(v.currentTime)!==last){last=Math.floor(v.currentTime);report('frame');}};}
v.play().catch(()=>report('error'));
</script></body></html>`;
const SYNC_PAGE = PAGE.replace('muted autoplay playsinline', 'controls playsinline')
  .replace("v.play().catch(()=>report('error'));",
    "document.addEventListener('pointerdown',()=>{v.muted=false;v.play().catch(()=>report('error'));},{once:true});");
function fixturePage(profile) {
  if (!['avc', 'av1', 'av-sync'].includes(profile)) throw new Error('Invalid fixed video profile');
  return profile === 'av-sync' ? SYNC_PAGE : PAGE;
}

function frameFacts(value) {
  if (!value || !Array.isArray(value.frames) || value.frames.length < 200 || value.frames.length > 300) throw new Error('Invalid generated frame count');
  const sizes = [];
  for (const frame of value.frames) {
    if (frame.width !== 864 || ![486, 432].includes(frame.height)) throw new Error('Invalid generated dimensions');
    const size = frame.width + 'x' + frame.height;
    if (sizes.at(-1) !== size) sizes.push(size);
  }
  if (JSON.stringify(sizes) !== JSON.stringify(['864x486', '864x432'])) throw new Error('Generated transition not verified');
  return { frames: value.frames.length, sizes };
}

function fixtureDirectory(profile) {
  if (typeof profile !== 'string' || !Object.hasOwn(PROFILES, profile)) throw new Error('Invalid fixed generated fixture profile');
  return PROFILES[profile];
}
function fixtureProfile(directory) {
  const profile = Object.keys(PROFILES).find(key => PROFILES[key] === directory);
  if (!profile) throw new Error('Invalid fixed generated fixture directory');
  return profile.startsWith('--serve-av1') ? 'av1' : 'avc';
}

function av1Facts(value) {
  const stream = value?.streams?.[0];
  if (!Array.isArray(value?.streams) || value.streams.length !== 1 ||
      stream.codec_name !== 'av1' || stream.width !== 640 || stream.height !== 360 ||
      stream.pix_fmt !== 'yuv420p' || stream.nb_read_frames !== '240' ||
      stream.r_frame_rate !== '30/1' || !/^8\.0+$/.test(stream.duration || '')) {
    throw new Error('Generated AV1 fixture not verified');
  }
  return { codec: 'av1', frames: 240, sizes: ['640x360'] };
}
async function protectedFile(name, maximum, directoryName = DIRECTORY) {
  const directory = await fs.lstat(directoryName);
  if (!directory.isDirectory() || directory.uid !== process.getuid() || (directory.mode & 0o022)) throw new Error('Unprotected fixture directory');
  const file = await fs.open(directoryName + '/' + name, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const stat = await file.stat();
    if (!stat.isFile() || stat.uid !== process.getuid() || (stat.mode & 0o022) || stat.size > maximum) throw new Error('Invalid generated file');
    const value = await file.readFile();
    if (value.length > maximum) throw new Error('Generated file exceeded bound');
    return value;
  } finally { await file.close(); }
}

async function generateFixture(run = execute) {
  await fs.mkdir(DIRECTORY, { mode: 0o700 });
  // Child encoders inherit a private mask even on hosts with group-writable defaults.
  const originalMask = process.umask(0o077);
  try {
    const options = { env: process.env, timeoutMs: 60000 };
    for (const [name, height, seconds] of [['intro', 486, 3], ['main', 432, 5]]) {
      await run('/usr/bin/ffmpeg', ['-v', 'error', '-n', '-f', 'lavfi', '-i', `testsrc2=size=864x${height}:rate=30`,
        '-t', String(seconds), '-c:v', 'libx264', '-profile:v', 'baseline', '-pix_fmt', 'yuv420p',
        '-g', '30', '-bf', '0', '-crf', '28', '-f', 'h264', DIRECTORY + '/' + name + '.h264'], options);
    }
    await run('/usr/bin/ffmpeg', ['-v', 'error', '-n', '-fflags', '+genpts', '-r', '30', '-i',
      'concat:' + DIRECTORY + '/intro.h264|' + DIRECTORY + '/main.h264', '-c:v', 'copy',
      '-movflags', '+faststart', DIRECTORY + '/fixture.mp4'], options);
    const output = await run('/usr/bin/ffprobe', ['-v', 'error', '-select_streams', 'v', '-show_frames',
      '-show_entries', 'frame=width,height', '-of', 'json', DIRECTORY + '/fixture.mp4'], options);
    const facts = frameFacts(JSON.parse(output.toString()));
    const video = await protectedFile('fixture.mp4', 8 * 1024 * 1024);
    const manifest = { version: 1, ...facts, bytes: video.length, sha256: crypto.createHash('sha256').update(video).digest('hex'),
      scope: 'Generated testsrc2 only; no app, account, protected media or license data' };
    await fs.writeFile(DIRECTORY + '/manifest.json', JSON.stringify(manifest) + '\n', { flag: 'wx', mode: 0o600 });
    return manifest;
  } finally { process.umask(originalMask); }
}

async function generateAv1Fixture(run = execute) {
  await fs.mkdir(AV1_DIRECTORY, { mode: 0o700 });
  const originalMask = process.umask(0o077);
  try {
    const options = { env: process.env, timeoutMs: 60000 };
    await run('/usr/bin/ffmpeg', ['-v', 'error', '-n', '-f', 'lavfi', '-i',
      'testsrc2=size=640x360:rate=30', '-frames:v', '240', '-an', '-c:v', 'libaom-av1',
      '-cpu-used', '8', '-threads', '2', '-row-mt', '1', '-lag-in-frames', '0',
      '-crf', '38', '-b:v', '0', '-g', '30', '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
      AV1_DIRECTORY + '/fixture.mp4'], options);
    const output = await run('/usr/bin/ffprobe', ['-v', 'error', '-select_streams', 'v', '-count_frames',
      '-show_entries', 'stream=codec_name,width,height,pix_fmt,nb_read_frames,r_frame_rate,duration',
      '-of', 'json', AV1_DIRECTORY + '/fixture.mp4'], options);
    const facts = av1Facts(JSON.parse(output.toString()));
    const video = await protectedFile('fixture.mp4', 8 * 1024 * 1024, AV1_DIRECTORY);
    const manifest = { version: 1, ...facts, bytes: video.length,
      sha256: crypto.createHash('sha256').update(video).digest('hex'),
      scope: 'Generated testsrc2 AV1 only; no app, account, protected media or license data' };
    await fs.writeFile(AV1_DIRECTORY + '/manifest.json', JSON.stringify(manifest) + '\n', { flag: 'wx', mode: 0o600 });
    return manifest;
  } finally { process.umask(originalMask); }
}

function reportFact(input, profile = 'avc') {
  if (!['avc', 'av1'].includes(profile)) throw new Error('Invalid fixed video profile');
  const width = profile === 'av1' ? 640 : 864, heights = profile === 'av1' ? [0, 360] : [0, 486, 432];
  if (!input || typeof input !== 'object' || Array.isArray(input) ||
      Object.keys(input).sort().join(',') !== 'callback,code,frames,height,time,type,width') return null;
  if (!['ready', 'frame', 'ended', 'error'].includes(input.type) || typeof input.callback !== 'boolean') return null;
  for (const [key, maximum] of [['width', width], ['height', Math.max(...heights)], ['frames', 10000], ['code', 4]]) {
    if (!Number.isSafeInteger(input[key]) || input[key] < 0 || input[key] > maximum) return null;
  }
  if ((input.width !== 0 && input.width !== width) || !heights.includes(input.height) ||
      !Number.isFinite(input.time) || input.time < 0 || input.time > 10) return null;
  return { type: input.type, time: Math.round(input.time * 1000) / 1000, width: input.width,
    height: input.height, frames: input.frames, callback: input.callback, code: input.code };
}

function rangeFact(header, length) {
  if (header === undefined) return { start: 0, end: length - 1, partial: false };
  const match = /^bytes=(\d{1,8})-(\d{1,8})?$/.exec(header);
  if (!match) return null;
  const start = Number(match[1]), end = match[2] ? Number(match[2]) : length - 1;
  if (start >= length || end < start || end >= length) return null;
  return { start, end, partial: true };
}

function createFixtureServer(video, record, profile = 'avc') {
  const page = fixturePage(profile);
  let reports = 0;
  return http.createServer(async (req, res) => {
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    if (req.headers.host !== '127.0.0.1:18185') { res.writeHead(403).end(); return; }
    if (req.method === 'GET' && req.url === '/') {
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }).end(page); return;
    }
    if (req.method === 'GET' && req.url === '/fixture.mp4') {
      const range = rangeFact(req.headers.range, video.length);
      if (!range) { res.writeHead(416, { 'Content-Range': 'bytes */' + video.length }).end(); return; }
      res.setHeader('Accept-Ranges', 'bytes'); res.setHeader('Content-Type', 'video/mp4');
      res.setHeader('Content-Length', range.end - range.start + 1);
      if (range.partial) res.setHeader('Content-Range', `bytes ${range.start}-${range.end}/${video.length}`);
      res.writeHead(range.partial ? 206 : 200).end(video.subarray(range.start, range.end + 1)); return;
    }
    if (req.method !== 'POST' || req.url !== '/report') { res.writeHead(404).end(); return; }
    if (req.headers.origin !== ORIGIN || req.headers['content-type'] !== 'application/json') { res.writeHead(403).end(); return; }
    let body = '', bytes = 0;
    try {
      for await (const chunk of req) {
        bytes += chunk.length;
        if (bytes > 2048) { res.writeHead(413).end(); return; }
        body += chunk.toString();
      }
      const fact = reportFact(JSON.parse(body), profile === 'av-sync' ? 'av1' : profile);
      if (!fact) { res.writeHead(400).end(); return; }
      if (reports >= 64) { res.writeHead(429).end(); return; }
      reports++;
      await record(fact); res.writeHead(204).end();
    } catch { if (!res.headersSent) res.writeHead(400).end(); }
  });
}

async function serveFixture(directory = DIRECTORY) {
  const av1 = fixtureProfile(directory) === 'av1';
  const manifest = JSON.parse((await protectedFile('manifest.json', 2048, directory)).toString());
  const video = await protectedFile('fixture.mp4', 8 * 1024 * 1024, directory);
  if (manifest.version !== 1 || manifest.bytes !== video.length ||
      manifest.sha256 !== crypto.createHash('sha256').update(video).digest('hex')) throw new Error('Generated fixture changed');
  if (av1 && (manifest.codec !== 'av1' || manifest.frames !== 240 ||
      JSON.stringify(manifest.sizes) !== JSON.stringify(['640x360']))) throw new Error('Generated AV1 manifest changed');
  const output = await fs.open(directory + '/browser-metadata.jsonl', constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
  let writes = Promise.resolve(), timer;
  const server = createFixtureServer(video, fact => {
    writes = writes.then(() => output.writeFile(JSON.stringify(fact) + '\n'));
    return writes;
  }, [PROFILES['--serve-av1-av-sync'], PROFILES['--serve-av1-av-sync-delay'],
    PROFILES['--serve-av1-av-sync-delay-retest']].includes(directory) ? 'av-sync' : av1 ? 'av1' : 'avc');
  try {
    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(18185, '127.0.0.1', resolve);
    });
    await new Promise(resolve => {
      server.once('close', resolve);
      timer = setTimeout(() => { server.close(); server.closeAllConnections?.(); }, av1 ? 45000 : 90000);
    });
  } finally {
    clearTimeout(timer);
    if (server.listening) { server.close(); server.closeAllConnections?.(); }
    try { await writes; await output.sync(); } finally { await output.close(); }
  }
}

if (require.main === module) {
  if (process.platform !== 'linux' || process.getuid?.() !== 1000 || process.argv.length !== 3 ||
      (!['--generate', '--generate-av1'].includes(process.argv[2]) && !Object.hasOwn(PROFILES, process.argv[2]))) {
    console.error('Fixed unprivileged generated fixture helper required'); process.exitCode = 1;
  } else (process.argv[2] === '--generate' ? generateFixture().then(value => console.log(JSON.stringify(value))) :
    process.argv[2] === '--generate-av1' ? generateAv1Fixture().then(value => console.log(JSON.stringify(value))) :
    serveFixture(fixtureDirectory(process.argv[2]))).catch(() => {
    console.error('Generated fixture operation failed; no playback settings changed'); process.exitCode = 1;
  });
}
module.exports = { frameFacts, av1Facts, reportFact, rangeFact, createFixtureServer, fixtureDirectory, fixtureProfile, fixturePage };
