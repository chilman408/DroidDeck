// SPDX-License-Identifier: GPL-3.0-only
// Run only during the fixed generated fixture, never account or paid-media screens.
const fs = require('node:fs/promises');
const { execute, validateSize } = require('../runtime/waydroid-display/host-adapter');
const { sourceNode } = require('../runtime/preview-gateway/compositor-video-launcher');
const { capturePlan } = require('../runtime/waydroid-display/compositor-source');
const ROOT = '/opt/teslaandroid-waydroid/source-motion-probe-20261011j2';
const FIXTURE_ROOT = '/opt/teslaandroid-waydroid/source-motion-fixture-20261011j2';
const PROFILES = Object.freeze({
  'tesla-software-source-generated-20261011j2.service': '--serve-av1-source-software-retest',
  'tesla-gpu-source-generated-20261011j2.service': '--serve-av1-source-gpu-retest',
  'tesla-gpu-mesa-allocator-generated-20261011o.service': '--serve-av1-mesa-allocator',
  'tesla-gpu-mesa-boot-generated-20261011o2.service': '--serve-av1-mesa-boot'
});
const MESA_FIXTURE_ROOTS = Object.freeze({
  'tesla-gpu-mesa-allocator-generated-20261011o.service': '/opt/teslaandroid-waydroid/mesa-allocator-fixture-20261011o',
  'tesla-gpu-mesa-boot-generated-20261011o2.service': '/opt/teslaandroid-waydroid/mesa-boot-fixture-20261011o2'
});
function sourceFacts(value) {
  const keys = ['beforeH264Encoding', 'colorSamples', 'globalSettingsWrites', 'maximumChangedRatio',
    'maximumMeanDifference', 'meaningfulMotionSamples', 'sampledFrames', 'savedPixels', 'scope'];
  if (!value || Object.keys(value).sort().join() !== keys.sort().join() ||
      value.beforeH264Encoding !== true || value.savedPixels !== false || value.globalSettingsWrites !== false ||
      value.scope !== 'generated testsrc2 only' ||
      !['sampledFrames', 'colorSamples', 'meaningfulMotionSamples'].every(key =>
        Number.isInteger(value[key]) && value[key] >= 0 && value[key] <= 64) ||
      value.sampledFrames < 8 || value.colorSamples > value.sampledFrames || value.meaningfulMotionSamples > value.colorSamples ||
      !Number.isFinite(value.maximumChangedRatio) || value.maximumChangedRatio < 0 || value.maximumChangedRatio > 1 ||
      !Number.isFinite(value.maximumMeanDifference) || value.maximumMeanDifference < 0 || value.maximumMeanDifference > 255) {
    throw new Error('Invalid generated source metadata');
  }
  return value;
}
function generatedForeground(text) {
  if (typeof text !== 'string' || Buffer.byteLength(text) > 4 * 1024 * 1024) throw new Error('Invalid foreground metadata');
  const lines = text.split(/\r?\n/);
  const legacy = lines.filter(line => /^\s*mResumedActivity:/.test(line)).map(line => line.replace(/^\s*mResumedActivity:\s*/, '').trim());
  const current = lines.filter(line => /^\s*topResumedActivity=/.test(line)).map(line => line.replace(/^\s*topResumedActivity=\s*/, '').trim());
  if (legacy.length > 1 || current.length > 1) return false;
  const resumed = new Set([...legacy, ...current]);
  return resumed.size === 1 && /\borg\.lineageos\.jelly\/\.MainActivity\b/.test([...resumed][0]);
}
function geometrySize(text) {
  if (typeof text !== 'string' || Buffer.byteLength(text) > 4 * 1024 * 1024) throw new Error('Invalid geometry metadata');
  const matches = [...text.matchAll(/init=(\d+)x(\d+) .*?cur=(\d+)x(\d+)/g)];
  if (matches.length !== 1) throw new Error('Generated display geometry unavailable or ambiguous');
  const size = { width: Number(matches[0][3]), height: Number(matches[0][4]), density: 180 };
  validateSize(size);
  return size;
}
async function inspectGeneratedSource({ run = execute, io = fs, node = sourceNode } = {}) {
  const options = { env: { ...process.env, LD_LIBRARY_PATH: '/opt/teslaandroid-waydroid/libgbinder' }, timeoutMs: 10000 };
  const invoke = async (file, args, timeoutMs = 10000) => (await run(file, args, { ...options, timeoutMs })).toString().trim();
  const discover = async () => {
    const pid = await invoke('/usr/bin/lxc-info', ['-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH']);
    if (!/^[1-9]\d*$/.test(pid) || !Number.isSafeInteger(Number(pid)) || Number(pid) < 2) throw new Error('Invalid generated container');
    return pid;
  };
  const pid = await discover();
  const guest = args => invoke('/usr/bin/nsenter', ['-t', pid, '-m', '-p', '-r', '--', '/system/bin/dumpsys', ...args]);
  const active = [];
  for (const unit of Object.keys(PROFILES)) {
    // systemctl show, unlike is-active, exits successfully for inactive units.
    if (await invoke('/usr/bin/systemctl', ['show', unit, '-p', 'ActiveState', '--value']) === 'active') active.push(unit);
  }
  if (active.length !== 1) throw new Error('Exactly one owned generated fixture is required');
  const unit = active[0];
  const fixturePid = await invoke('/usr/bin/systemctl', ['show', unit, '-p', 'MainPID', '--value']);
  if (!/^[1-9]\d*$/.test(fixturePid) || !Number.isSafeInteger(Number(fixturePid)) || Number(fixturePid) < 2) throw new Error('Invalid fixture identity');
  const processPath = '/proc/' + fixturePid;
  const argv = (await io.readFile(processPath + '/cmdline')).toString();
  const expected = ['/usr/sbin/runuser', '-u', 'hilman', '--', '/usr/bin/env',
    'NODE_PATH=/home/hilman/teslaandroid-waydroid/runtime/waydroid-display/node_modules',
    '/usr/bin/node', (MESA_FIXTURE_ROOTS[unit] || FIXTURE_ROOT) +
      '/generated-transition-fixture.js', PROFILES[unit]];
  if (argv.length > 4096 || JSON.stringify(argv.split('\0').filter(Boolean)) !== JSON.stringify(expected) ||
      (await io.lstat(processPath)).uid !== 0 || await io.readlink(processPath + '/exe') !== '/usr/sbin/runuser') {
    throw new Error('Foreign generated fixture process');
  }
  const fence = async () => {
    if (await discover() !== pid || await invoke('/usr/bin/systemctl', ['show', unit, '-p', 'MainPID', '--value']) !== fixturePid ||
        await invoke('/usr/bin/systemctl', ['show', unit, '-p', 'ActiveState', '--value']) !== 'active' ||
        !generatedForeground(await guest(['activity', 'activities']))) throw new Error('Generated fixture scope changed');
  };
  await fence();
  const size = geometrySize(await guest(['window', 'displays']));
  const selected = await node(run, io);
  const plan = { ...capturePlan(await guest(['input']), size, selected), bitRate: 4000000, maxFps: 30 };
  const result = await invoke('/usr/sbin/runuser', ['-u', 'hilman', '--', '/usr/bin/env',
    'XDG_RUNTIME_DIR=/run/user/1000', 'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus',
    '/usr/bin/python3', ROOT + '/runtime/preview-gateway/generated-compositor-motion.py', JSON.stringify(plan)], 20000);
  await fence();
  if (JSON.stringify(await node(run, io)) !== JSON.stringify(selected) ||
      JSON.stringify(geometrySize(await guest(['window', 'displays']))) !== JSON.stringify(size)) {
    throw new Error('Generated compositor or geometry changed');
  }
  if (result.length > 2048) throw new Error('Oversized generated source metadata');
  return { version: 1, ...sourceFacts(JSON.parse(result)) };
}
if (require.main === module) {
  if (process.platform !== 'linux' || process.getuid?.() !== 0 || process.argv.length !== 2) {
    console.error('Fixed root generated source probe required'); process.exitCode = 1;
  } else inspectGeneratedSource().then(value => console.log(JSON.stringify(value))).catch(() => {
    console.error('Generated source scope or motion probe failed; no settings changed'); process.exitCode = 1;
  });
}
module.exports = { sourceFacts, generatedForeground, geometrySize, inspectGeneratedSource };
