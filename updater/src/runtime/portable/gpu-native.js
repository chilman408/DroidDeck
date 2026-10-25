// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs/promises');
const EXPECTED = Object.freeze({
  'sys.boot_completed': '1',
  'ro.hardware.gralloc': 'minigbm_gbm_mesa',
  'init.svc.vendor.graphics.allocator-4-0-gbm_mesa': 'running',
  'ro.waydroid.software_rendering': '0',
  'persist.waydroid.use_subsurface': 'true',
  'debug.stagefright.ccodec': '4',
  'media.sf.hwaccel': '0',
  'debug.ffmpeg-codec2.hwaccel.drm': '0',
  'debug.ffmpeg-codec2.hwaccel.force': '0',
  'debug.ffmpeg-codec2.pixel_format': 'RGBX_8888',
});
const LIBRARY = '/vendor/lib64/libminigbm_gralloc_gbm_mesa.so';
const CONSUMERS = Object.freeze([
  '/vendor/lib64/hw/android.hardware.graphics.mapper@4.0-impl.minigbm_gbm_mesa.so',
  '/vendor/lib64/hw/gralloc.minigbm_gbm_mesa.so',
]);
const LXC = Object.freeze(['-P', '/var/lib/waydroid/lxc', '-n', 'waydroid']);

function text(value, limit) {
  if (!(typeof value === 'string' || Buffer.isBuffer(value)) || Buffer.byteLength(value) > limit) {
    throw new Error('Native GPU evidence exceeded its bound');
  }
  const result = new TextDecoder('utf-8', { fatal: true }).decode(Buffer.from(value));
  if (result.includes('\0')) throw new Error('Invalid native GPU evidence');
  return result;
}
function rendererLine(value) {
  const lines = text(value, 4 * 1024 * 1024).split(/\r?\n/).filter(line => /^GLES:/.test(line));
  if (lines.length !== 1 || lines[0].length > 512 || !/^[\x20-\x7e]+$/.test(lines[0])) {
    throw new Error('Missing or ambiguous Android renderer');
  }
  return lines[0];
}
function mappedConsumers(value) {
  const found = new Set();
  for (const line of text(value, 4 * 1024 * 1024).split(/\r?\n/)) {
    if (!line) continue;
    const match = /^[0-9a-f]+-[0-9a-f]+ ([r-][w-][x-][ps]) [0-9a-f]+ [0-9a-f]+:[0-9a-f]+ \d+(?:\s+(.*))?$/.exec(line);
    if (!match) throw new Error('Invalid SurfaceFlinger mapping');
    if (/^r-x[ps]$/.test(match[1]) && [LIBRARY, ...CONSUMERS].includes(match[2])) found.add(match[2]);
  }
  return Object.freeze({ allocatorLibrary: found.has(LIBRARY),
    consumerLibrary: CONSUMERS.some(path => found.has(path)) });
}
function surfacePid(identity) {
  if (typeof identity !== 'string' || !/^\d+:\d+\/\d+:\d+$/.test(identity)) {
    throw new Error('Invalid Android process identity');
  }
  const values = identity.split(/[/:]/).map(Number);
  if (!values.every(Number.isSafeInteger) || values.some(value => value < 1) || values[0] < 2 || values[2] < 2) {
    throw new Error('Invalid Android process identity');
  }
  return values[2];
}
function evaluateNative({ properties, renderer, maps }) {
  if (!properties || typeof properties !== 'object' || Array.isArray(properties) ||
      Object.keys(properties).length !== Object.keys(EXPECTED).length ||
      Object.keys(properties).some(key => !Object.hasOwn(EXPECTED, key))) {
    throw new Error('Invalid selected GPU properties');
  }
  const selected = {};
  const reasons = [];
  for (const [key, expected] of Object.entries(EXPECTED)) {
    const value = text(properties[key], 256).trim(); selected[key] = value;
    if (value !== expected) reasons.push('property:' + key);
  }
  const line = rendererLine(renderer), lower = line.toLowerCase();
  if (!/\bvirgl\b/.test(lower) || /swiftshader|llvmpipe|softpipe/.test(lower)) reasons.push('renderer');
  const libraries = mappedConsumers(maps);
  if (!libraries.allocatorLibrary) reasons.push('allocator-library');
  if (!libraries.consumerLibrary) reasons.push('consumer-library');
  return Object.freeze({ kind: 'android-gpu-native-health', nativeGpuVerified: reasons.length === 0,
    mediaPlaybackVerified: false, browserStreamVerified: false,
    properties: Object.freeze(selected), renderer: line, libraries, reasons: Object.freeze(reasons) });
}

async function inspectNativeGpu({ run, readIdentity, readAndroid, io = fs, signal } = {}) {
  signal = signal ? AbortSignal.any([signal, AbortSignal.timeout(30000)]) : AbortSignal.timeout(30000);
  signal.throwIfAborted();
  // Recovery-owned builtins-only code cannot race an installed display exchange.
  run ||= require('./recovery-probe-io').execute;
  if (!readIdentity || !readAndroid) {
    const identity = require('./recovery-probe-io');
    readIdentity ||= pid => identity.readContainerIdentity(pid, { io, signal });
    readAndroid ||= identity.readAndroidIdentity;
  }
  const options = { signal, timeoutMs: 10000 };
  const command = async (file, args, bound = 4096) => {
    signal?.throwIfAborted();
    const value = text(await run(file, args, options), bound);
    signal?.throwIfAborted(); return value;
  };
  const runningPid = async () => {
    if ((await command('/usr/bin/lxc-info', [...LXC, '-sH'])).trim() !== 'RUNNING') {
      throw new Error('Native GPU inspection requires a running container');
    }
    const value = (await command('/usr/bin/lxc-info', [...LXC, '-pH'])).trim(), pid = Number(value);
    if (!/^[1-9]\d*$/.test(value) || !Number.isSafeInteger(pid) || pid < 2) throw new Error('Invalid container PID');
    return pid;
  };
  const pid = await runningPid(), identity = await readIdentity(pid);
  const android = await readAndroid(pid, options, { run, io }), sf = surfacePid(android);
  const guest = (args, bound) => command('/usr/bin/nsenter',
    ['-t', String(pid), '-m', '-p', '-r', '--', ...args], bound);
  const properties = async () => {
    const result = {};
    for (const key of Object.keys(EXPECTED)) result[key] =
      (await guest(['/system/bin/getprop', key], 256)).trim();
    return result;
  };
  const before = await properties();
  const renderer = await guest(['/system/bin/dumpsys', 'SurfaceFlinger'], 4 * 1024 * 1024);
  // Read executable consumer mappings only, not app state, buffers, licenses or captures.
  const maps = await guest(['/system/bin/toybox', 'cat', `/proc/${sf}/maps`], 4 * 1024 * 1024);
  const result = evaluateNative({ properties: before, renderer, maps });
  if (JSON.stringify(await properties()) !== JSON.stringify(before) || await runningPid() !== pid ||
      await readIdentity(pid) !== identity || await readAndroid(pid, options, { run, io }) !== android) {
    throw new Error('Android GPU evidence changed during inspection');
  }
  signal?.throwIfAborted();
  return Object.freeze({ ...result, containerPid: pid, surfaceFlingerPid: sf,
    containerIdentity: identity, androidIdentity: android });
}
async function main(args) {
  if (args.length || process.platform !== 'linux' || process.getuid() !== 0) {
    throw new Error('No-argument root Linux native GPU inspection required');
  }
  const result = await inspectNativeGpu(); console.log(JSON.stringify(result));
  if (!result.nativeGpuVerified) process.exitCode = 2;
}
if (require.main === module) main(process.argv.slice(2)).catch(() => {
  console.error('Native GPU inspection unavailable or unstable'); process.exitCode = 1;
});
module.exports = { EXPECTED, LIBRARY, CONSUMERS, evaluateNative, inspectNativeGpu };
