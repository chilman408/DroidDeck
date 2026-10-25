// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs/promises'), http = require('node:http');
const { ROOT, ORIGIN, DIGEST, UNIT } = require('./generated-audio-fixture');
const { foreground } = require('./generated-playback-check');
const AUDIO_UNIT = 'droiddeck-generated-audio-capture-20261014bn.service';
let phase = 'admission';
let scopeDetail = 'none';
let childPhase = 'none';
function owner(value) {
  if (typeof value !== 'string' || !/^[0-9a-f]{32}$/.test(value)) throw Error('Fixed test owner required');
  return value;
}
function guestArgs(pid, args) {
  if (typeof pid !== 'string' || !/^[1-9]\d*$/.test(pid) || Number(pid) < 2 || Number(pid) > 2147483647) throw Error('Container unavailable');
  return ['-t', pid, '-m', '-p', '-r', '--', '/system/bin/env',
    'PATH=/system/bin:/apex/com.android.runtime/bin', 'ANDROID_ROOT=/system', 'ANDROID_DATA=/data', ...args];
}
function observation(value, allowUnverified = false) {
  if (!value || Object.keys(value).sort().join() !== 'bytes,frames,nonzeroSamples,peak,seconds,tone' ||
      !['bytes', 'frames', 'nonzeroSamples', 'peak'].every(key => Number.isSafeInteger(value[key]) && value[key] >= 0) ||
      value.bytes > 2 * 1024 * 1024 || value.bytes !== value.frames * 4 ||
      value.frames < 1 || value.nonzeroSamples > value.frames * 2 ||
      value.peak > 32768 || value.seconds !== value.frames / 48000 || !value.tone ||
      Object.keys(value.tone).sort().join() !== 'frequencyHz,matchingWindows,sampleRate,windows' ||
      value.tone.frequencyHz !== 1000 || value.tone.sampleRate !== 48000 ||
      !Number.isSafeInteger(value.tone.matchingWindows) || value.tone.matchingWindows < 0 ||
      value.tone.windows !== Math.floor(value.frames / 480) || value.tone.matchingWindows > value.tone.windows) throw Error('Generated PCM shape invalid');
  const verified = value.frames >= 48000 && value.nonzeroSamples >= 4800 && value.peak >= 1 &&
    value.tone.matchingWindows >= 50 && value.tone.matchingWindows >= value.tone.windows * 0.7;
  if (!allowUnverified && !verified) throw Error('Generated PCM not verified');
  return { kind: 'generated-android-audio', ...value, generatedPcmVerified: verified, savedAudio: false,
    browserAudioVerified: false, installable: false };
}
function lease(value, active = true) {
  if (!value || value.kind !== 'generated-audio-fixture' || value.sha256 !== DIGEST || value.ready !== true ||
      typeof value.active !== 'boolean' || (active && !value.active) ||
      typeof value.gestureObserved !== 'boolean' || (active && !value.gestureObserved) ||
      typeof value.pageToken !== 'string' || !/^[0-9a-f]{32}$/.test(value.pageToken) ||
      !Number.isSafeInteger(value.reports) || value.reports < 1 || value.reports > 350) throw Error('Generated audio lease unavailable');
  return value;
}
function status() {
  return new Promise((resolve, reject) => {
    const req = http.get(ORIGIN + '/status', { timeout: 2000 }, res => {
      let body = '';
      res.on('data', chunk => { body += chunk.toString(); if (Buffer.byteLength(body) > 2048) res.destroy(Error('Lease bound')); });
      res.once('error', reject); res.once('end', () => { try {
        if (res.statusCode !== 200) throw Error('Lease response'); resolve(JSON.parse(body));
      } catch (error) { reject(error); } });
    });
    req.once('timeout', () => req.destroy(Error('Lease timeout'))); req.once('error', reject);
  });
}
async function context(token) {
  owner(token);
  if (process.platform !== 'linux' || process.getuid() !== 0 || __dirname !== ROOT) throw Error('Disposable root check required');
  const { readProtectedRuntimeConfig } = require('/opt/droiddeck/runtime/portable/protected-config');
  const { deriveRuntimeLayout } = require('/opt/droiddeck/runtime/portable/runtime-config');
  const { createGpsStore } = require('/opt/droiddeck/runtime/waydroid-display/gps-store');
  const { createNativeRuntime, prepareNativeDirectory } = require('/opt/droiddeck/runtime/waydroid-display/runtime-native');
  const { execute } = require('/opt/droiddeck/runtime/waydroid-display/host-adapter');
  const config = readProtectedRuntimeConfig();
  if (config.lanAddress !== '192.168.2.215' || config.uid !== 1000 || config.user !== 'hilman') throw Error('Disposable VM required');
  const invoke = async (file, args, timeoutMs = 10000) => {
    try { return (await execute(file, args, { timeoutMs, env: process.env })).toString().trim(); }
    catch (error) {
      const detail = /Generated Android audio incomplete; phase=(admission|pcm-ownership|pcm-namespace|pcm-fixture|pcm-read); scope=/.exec(error.message || '');
      if (detail) childPhase = detail[1];
      throw error;
    }
  };
  const unit = async name => Object.fromEntries((await invoke('/usr/bin/systemctl', ['show', name,
    '-p', 'ActiveState', '-p', 'MainPID', '-p', 'ControlPID', '-p', 'Description'])).split(/\r?\n/).map(line => line.split('=')));
  const store = createGpsStore(deriveRuntimeLayout(config).sockets + '/runtime/native-lease.json', 'journal');
  const native = createNativeRuntime({ configuration: config, store });
  return { config, invoke, unit, store, native, prepare: () => prepareNativeDirectory({ configuration: config }),
    description: 'DroidDeck generated audio ' + token };
}
async function clean(ctx, token) {
  owner(token);
  // A pre-capture failure may precede creation of the journal directory.
  await ctx.prepare();
  const current = await ctx.unit(AUDIO_UNIT), record = await ctx.store.load();
  if (record && record.lease !== token) throw Error('Foreign native lease; no cleanup');
  if (current.ActiveState === 'active' || current.MainPID !== '0' || current.ControlPID !== '0') {
    if (current.Description !== ctx.description) throw Error('Foreign capture unit; no stop');
    await ctx.invoke('/usr/bin/systemctl', ['stop', AUDIO_UNIT]);
  }
  if (record) { await ctx.native.adopt(token); await ctx.native.finish(); }
}
async function inspect(ctx, token) {
  const { readContainerIdentity } = require('/opt/droiddeck/runtime/waydroid-display/runtime-discovery');
  const getPid = () => ctx.invoke('/usr/bin/lxc-info', ['-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH']);
  const pid = await getPid();
  if (!/^[1-9]\d*$/.test(pid) || Number(pid) < 2) throw Error('Container unavailable');
  const identity = await readContainerIdentity(pid);
  const first = await status();
  if (first.kind !== 'generated-audio-fixture' || first.sha256 !== DIGEST) throw Error('Generated source required');
  const pageToken = owner(first.pageToken);
  const guest = args => ctx.invoke('/usr/bin/nsenter', guestArgs(pid, args));
  const fence = async (playing = true) => {
    scopeDetail = 'units';
    const runtime = await ctx.unit('tesla-waydroid-runtime.service'), fixture = await ctx.unit(UNIT);
    if (runtime.ActiveState !== 'inactive' || runtime.MainPID !== '0' || runtime.ControlPID !== '0' ||
        fixture.ActiveState !== 'active' || !/^[1-9]\d*$/.test(fixture.MainPID) || Number(fixture.MainPID) < 2 ||
        await getPid() !== pid || await readContainerIdentity(pid) !== identity) throw Error('Fixture scope changed');
    scopeDetail = 'process';
    const processPath = '/proc/' + fixture.MainPID;
    const argv = (await fs.readFile(processPath + '/cmdline')).toString().split('\0').filter(Boolean);
    if ((await fs.lstat(processPath)).uid !== 1000 || await fs.readlink(processPath + '/exe') !== '/usr/bin/node' ||
        JSON.stringify(argv) !== JSON.stringify(['/usr/bin/node', ROOT + '/generated-audio-fixture.js', '--serve'])) throw Error('Foreign fixture');
    scopeDetail = 'progress';
    if (lease(await status(), playing).pageToken !== pageToken) throw Error('Generated page changed');
    scopeDetail = 'foreground';
    if (!foreground(await guest(['/system/bin/dumpsys', 'activity', 'activities']))) throw Error('Generated foreground changed');
    scopeDetail = 'verified';
  };
  phase = 'runtime-inactive';
  // Only the disposable VM opens this fixed generated page; never an account/app URL.
  const runtime = await ctx.unit('tesla-waydroid-runtime.service');
  if (runtime.ActiveState !== 'inactive' || runtime.MainPID !== '0' || runtime.ControlPID !== '0') throw Error('Preview must remain stopped');
  phase = 'launch-generated';
  // Jelly may bring an old URI task to front instead of loading this invocation.
  // Restart only this disposable generated-fixture app; never clear its data.
  await guest(['/system/bin/am', 'force-stop', 'org.lineageos.jelly']);
  await guest(['/system/bin/am', 'start', '-W', '-a', 'android.intent.action.VIEW', '-d', ORIGIN + '/?proof=' + pageToken, '-n', 'org.lineageos.jelly/.MainActivity']);
  phase = 'fixture';
  for (let attempt = 0; ; attempt++) {
    try { await fence(false); break; } catch (error) { if (attempt >= 75) throw error; await new Promise(resolve => setTimeout(resolve, 200)); }
  }
  phase = 'generated-gesture';
  const size = await guest(['/system/bin/wm', 'size']);
  const match = /Override size: (\d+)x(\d+)/.exec(size) || /Physical size: (\d+)x(\d+)/.exec(size);
  if (!match || [Number(match[1]), Number(match[2])].some(value => value < 96 || value > 1920)) throw Error('Generated gesture geometry');
  const x = String(Math.floor(Number(match[1]) / 2)), y = String(Math.floor(Number(match[2]) / 2));
  const gesture = ['/system/bin/cmd', 'input', 'touchscreen', '-d', '0', 'swipe', x, y, x, y, '80'];
  await guest(gesture);
  for (let attempt = 0; ; attempt++) {
    // Cold AAC/output-route startup can outlast three seconds on a fresh VM.
    try { await fence(); break; } catch (error) {
      if (attempt >= 75) throw error;
      // Retry only an explicitly unacknowledged generated-button press, never
      // an acknowledged action or an arbitrary app/control under a stale page.
      if (attempt === 5 || attempt === 10) {
        await fence(false); const current = lease(await status(), false);
        if (current.pageToken === pageToken && !current.gestureObserved && !current.active) await guest(gesture);
      }
      await new Promise(resolve => setTimeout(resolve, 200));
    }
  }
  phase = 'capture';
  const existing = await ctx.unit(AUDIO_UNIT);
  if (existing.ActiveState !== 'inactive' || existing.MainPID !== '0' || existing.ControlPID !== '0') throw Error('Capture namespace occupied');
  await ctx.prepare(); await ctx.native.assertVacant(); await ctx.native.begin(token);
  try {
    await ctx.invoke('/usr/bin/systemd-run', ['--unit=' + AUDIO_UNIT, '--description=' + ctx.description, '--collect',
      '--property=RuntimeMaxSec=25s', '--property=TimeoutStopSec=5s', '--setenv=TESLA_WAYDROID_RUNTIME_OWNER=' + token,
      '--setenv=TESLA_WAYDROID_RUNTIME_ROLE=audio', '--setenv=DROIDDECK_CONFIGURATION_SHA256=2841520938496e5dadc0cc5c4f0a867c3c3ffaf8906e66b3a08992b69b8c76e9',
      '/usr/bin/node', '/opt/droiddeck/runtime/portable/worker-entry.js', 'audio']);
    await new Promise(resolve => setTimeout(resolve, 1000)); await fence();
    phase = 'pcm';
    const result = await ctx.invoke('/usr/bin/nsenter', ['-t', pid, '-n', '--', '/usr/bin/node',
      ROOT + '/generated-audio-check.js', '--pcm', token, pid], 8000);
    phase = 'scope-after'; await fence();
    if (result.length > 2048) throw Error('PCM report bound');
    phase = 'observation'; return observation(JSON.parse(result), true);
  } finally { await clean(ctx, token); }
}
async function main(args) {
  if (!['--inspect', '--cleanup', '--pcm'].includes(args[0]) ||
      args.length !== (args[0] === '--pcm' ? 3 : 2)) throw Error('Fixed generated audio command required');
  const token = owner(args[1]), ctx = await context(token);
  if (args[0] === '--cleanup') { await clean(ctx, token); console.log('Generated audio cleanup complete'); }
  else if (args[0] === '--pcm') console.log(JSON.stringify(await pcm(ctx, token, args[2])));
  else {
    const result = await inspect(ctx, token); console.log(JSON.stringify(result));
    if (!result.generatedPcmVerified) process.exitCode = 2;
  }
}
async function pcm(ctx, token, pid, { readlink = fs.readlink, readStatus = status,
  runProbe = require('./raw-audio-probe').runProbe,
  makeStats = () => require('./generated-tone-stats').createToneStats(require('./raw-audio-probe').RawAudioStats) } = {}) {
  guestArgs(pid, []);
  // The host parent supplies its fenced PID. LXC's abstract monitor socket is
  // not visible from Android's network namespace, so do not rediscover it here.
  phase = 'pcm-ownership';
  const record = await ctx.store.load(), current = await ctx.unit(AUDIO_UNIT);
  if (record?.lease !== owner(token) ||
      current.ActiveState !== 'active' || current.Description !== ctx.description) throw Error('Owned capture required');
  phase = 'pcm-namespace';
  if (await readlink('/proc/self/ns/net') !== await readlink('/proc/' + pid + '/ns/net')) throw Error('Owned network capture required');
  phase = 'pcm-fixture';
  lease(await readStatus());
  // Stay within the eight-second generated clip instead of depending on a loop
  // transition while Android changes its output route for REMOTE_SUBMIX.
  phase = 'pcm-read'; return runProbe(undefined, 2000, makeStats());
}
if (require.main === module) main(process.argv.slice(2)).catch(() => { console.error('Generated Android audio incomplete; phase=' + phase + '; scope=' + scopeDetail + '; child=' + childPhase); process.exitCode = 1; });
module.exports = { owner, guestArgs, lease, observation, clean, pcm, AUDIO_UNIT };
