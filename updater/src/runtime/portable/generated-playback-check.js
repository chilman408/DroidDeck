// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs/promises');
const http = require('node:http');
const { ROOT, UNIT, ORIGIN } = require('./generated-playback');
let phase = 'admission';
function foreground(value) {
  if (typeof value !== 'string' || Buffer.byteLength(value) > 4 * 1024 * 1024) throw Error('Foreground bound');
  const lines = value.split(/\r?\n/).filter(line => /^\s*(?:mResumedActivity:|topResumedActivity=)/.test(line));
  return lines.length > 0 && lines.length <= 2 && lines.every(line => /\borg\.lineageos\.jelly\/\.MainActivity\b/.test(line));
}
function observation(value, allowUnverified = false) {
  if (!value || value.kind !== 'generated-playback-pixels' || value.afterH264Decode !== true || value.savedPixels !== false ||
      Object.keys(value).sort().join() !== 'afterH264Decode,colorFrames,frames,kind,motionSamples,savedPixels,verified' ||
      !['frames', 'colorFrames', 'motionSamples'].every(key => Number.isSafeInteger(value[key]) && value[key] >= 0 && value[key] <= 64) ||
      value.colorFrames > value.frames || value.motionSamples > value.colorFrames || typeof value.verified !== 'boolean' ||
      value.verified !== (value.frames >= 8 && value.colorFrames >= 8 && value.motionSamples >= 3) ||
      (!allowUnverified && !value.verified)) throw Error('Generated image validation failed');
  return value;
}
function status() {
  return new Promise((resolve, reject) => {
    const req = http.get(ORIGIN + '/status', { timeout: 2000 }, res => {
      let body = '';
      res.on('data', data => { body += data.toString(); if (Buffer.byteLength(body) > 2048) res.destroy(Error('Lease bound')); });
      res.on('error', reject); res.on('end', () => { try { if (res.statusCode !== 200) throw Error('Lease status'); resolve(JSON.parse(body)); } catch (error) { reject(error); } });
    });
    req.on('timeout', () => req.destroy(Error('Lease timeout'))); req.on('error', reject);
  });
}
async function inspect() {
  if (process.platform !== 'linux' || process.getuid() !== 0 || process.argv.length !== 2 || __dirname !== ROOT) throw Error('Protected disposable validation required');
  const { execute } = require('/opt/droiddeck/runtime/waydroid-display/host-adapter');
  const { readContainerIdentity } = require('/opt/droiddeck/runtime/waydroid-display/runtime-discovery');
  const { readProtectedRuntimeConfig } = require('/opt/droiddeck/runtime/portable/protected-config');
  const { sourceNode } = require('/opt/droiddeck/runtime/preview-gateway/compositor-video-launcher');
  const { capturePlan } = require('/opt/droiddeck/runtime/waydroid-display/compositor-source');
  const config = readProtectedRuntimeConfig();
  if (config.lanAddress !== '192.168.2.215' || config.uid !== 1000 || config.user !== 'hilman') throw Error('Disposable VM admission changed');
  for (const name of ['generated-playback.js', 'generated-playback-check.js', 'generated-playback-pixels.py']) {
    const info = await fs.lstat(ROOT + '/' + name);
    if (!info.isFile() || info.uid !== 0 || info.gid !== 0 || info.nlink !== 1 || (info.mode & 0o777) !== 0o644) throw Error('Unsafe validation code');
  }
  const invoke = async (file, args, timeoutMs = 10000) => (await execute(file, args, { timeoutMs, env: process.env })).toString().trim();
  const unit = async name => Object.fromEntries((await invoke('/usr/bin/systemctl', ['show', name, '-p', 'ActiveState', '-p', 'MainPID', '-p', 'ControlPID']))
    .split(/\r?\n/).map(line => line.split('=')));
  const getPid = () => invoke('/usr/bin/lxc-info', ['-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH']);
  const pid = await getPid();
  if (!/^[1-9]\d*$/.test(pid) || Number(pid) < 2) throw Error('Invalid container');
  const identity = await readContainerIdentity(pid);
  const guest = args => invoke('/usr/bin/nsenter', ['-t', pid, '-m', '-p', '-r', '--', ...args]);
  phase = 'lease';
  const lease = await status(), selected = await sourceNode(undefined, undefined, config);
  if (!lease.active || !/^[0-9a-f]{64}$/.test(lease.sha256)) throw Error('Generated video is not progressing');
  const fence = async () => {
    const runtime = await unit('tesla-waydroid-runtime.service'), server = await unit(UNIT);
    if (runtime.ActiveState !== 'inactive' || runtime.MainPID !== '0' || runtime.ControlPID !== '0' ||
        server.ActiveState !== 'active' || !/^[1-9]\d*$/.test(server.MainPID) || Number(server.MainPID) < 2) throw Error('Generated service scope changed');
    const info = await fs.lstat('/proc/' + server.MainPID);
    const argv = (await fs.readFile('/proc/' + server.MainPID + '/cmdline')).toString().split('\0').filter(Boolean);
    if (info.uid !== 1000 || await fs.readlink('/proc/' + server.MainPID + '/exe') !== '/usr/bin/node' ||
        JSON.stringify(argv) !== JSON.stringify(['/usr/bin/node', ROOT + '/generated-playback.js', '--serve']) ||
        await getPid() !== pid || await readContainerIdentity(pid) !== identity ||
        !foreground(await guest(['/system/bin/dumpsys', 'activity', 'activities']))) throw Error('Generated foreground or identity changed');
    const current = await status();
    if (!current.active || current.sha256 !== lease.sha256 || JSON.stringify(await sourceNode(undefined, undefined, config)) !== JSON.stringify(selected)) throw Error('Generated lease changed');
  };
  phase = 'scope-before'; await fence();
  phase = 'geometry';
  const wm = await guest(['/system/bin/wm', 'size']);
  const m = /Override size: (\d+)x(\d+)/.exec(wm) || /Physical size: (\d+)x(\d+)/.exec(wm);
  if (!m) throw Error('Display size unavailable');
  const plan = { ...capturePlan(await guest(['/system/bin/dumpsys', 'input']),
    { width: Number(m[1]), height: Number(m[2]), density: 180 }, selected), bitRate: 3000000, maxFps: 30 };
  phase = 'pixels';
  const raw = await invoke('/usr/sbin/runuser', ['-u', 'hilman', '--', '/usr/bin/env',
    'XDG_RUNTIME_DIR=/run/user/1000', 'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus',
    '/usr/bin/python3', ROOT + '/generated-playback-pixels.py', JSON.stringify(plan), lease.sha256], 15000);
  phase = 'scope-after'; await fence();
  if (raw.length > 2048) throw Error('Pixel result bound');
  phase = 'observation';
  return { ...observation(JSON.parse(raw), true), browserDecodedFrames: (await status()).frames, audioVerified: false, browserStreamVerified: false };
}
if (require.main === module) inspect().then(value => {
  console.log(JSON.stringify(value)); if (!value.verified) process.exitCode = 2;
}).catch(error => {
  const detail = phase === 'pixels' && /Generated-only pixel inspection unavailable; phase=([a-z-]{1,32});/.exec(error.message || '');
  console.error('Generated Android playback inspection incomplete: phase=' + phase + (detail ? ', pixelPhase=' + detail[1] : '')); process.exitCode = 1;
});
module.exports = { foreground, observation, status };
