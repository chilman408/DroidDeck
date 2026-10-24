// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs/promises');
const { spawn } = require('node:child_process');
const { createHostAdapter, execute, validateSize } = require('../waydroid-display/host-adapter');
const { compositorNode, capturePlan } = require('../waydroid-display/compositor-source');
const { protectedStat } = require('../waydroid-display/runtime-preflight');
const { prepareCapture, captureEnvironment } = require('./capture-lease');
const { captureLayout } = require('./capture-layout');
const { loadWorkerConfiguration } = require('../portable/worker-config');
const { createVideoCaptureController } = require('./video-capture-controller');
const { readVideoSettings } = require('./video-settings');
const { setTimeout: delay } = require('node:timers/promises');
function compositorLaunchArgs(pid, owner, plan, configuration) {
  const layout = captureLayout(configuration);
  if (typeof pid !== 'string' || !/^[1-9]\d*$/.test(pid) || !Number.isSafeInteger(Number(pid)) || Number(pid) < 2) throw new Error('Invalid compositor container identity');
  captureEnvironment(owner, 'video');
  if (!owner) throw new Error('Supervised compositor lease required');
  return ['-t', pid, '-n', '/usr/sbin/runuser', '-u', layout.user, '--', '/usr/bin/env',
    ...layout.userEnvironment, 'TESLA_WAYDROID_RUNTIME_OWNER=' + owner, '/usr/bin/python3',
    layout.root + '/runtime/preview-gateway/compositor-h264.py', JSON.stringify(plan),
    ...(configuration === undefined ? [] : ['--uid', String(layout.uid)])];
}
function armFirstFrameRepaint(child, repaint, {
  setTimeout: schedule = setTimeout, clearTimeout: cancel = clearTimeout,
  onFailure = () => console.error('Capture first-frame repaint failed'),
} = {}) {
  let pending = '', timer, done = false;
  const cleanup = () => {
    if (done) return;
    done = true; cancel(timer);
    child.stdout.removeListener('data', observed);
    child.removeListener('exit', cleanup); child.removeListener('error', cleanup);
  };
  const observed = bytes => {
    pending = (pending + bytes.toString('utf8', Math.max(0, bytes.length - 512))).slice(-512);
    if (pending.includes('Compositor H264 configuration and first key frame ready\n')) cleanup();
  };
  child.stdout.on('data', observed);
  child.once('exit', cleanup); child.once('error', cleanup);
  timer = schedule(() => { cleanup(); void Promise.resolve().then(repaint).catch(onFailure); }, 2000);
  timer.unref?.();
}
async function repaintDisplay(run, pid, ownsLease, sleep = delay) {
  if (typeof pid !== 'string' || !/^\d+$/.test(pid) || Number(pid) < 2) throw new Error('Invalid repaint container identity');
  let requested = false;
  const command = async operation => {
    const live = (await run('/usr/bin/lxc-info', ['-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'],
      { env: process.env })).toString().trim();
    if (live !== pid || !await ownsLease()) throw new Error('Capture repaint ownership changed');
    if (operation === 'expand-notifications') requested = true;
    await run('/usr/bin/nsenter', ['-t', pid, '-m', '-p', '-r', '--', '/system/bin/cmd', 'statusbar', operation],
      { env: process.env, timeout: 3000 });
  };
  // Waydroid uses this repaint sequence; omit its unrelated policy_control write.
  try { await command('expand-notifications'); await sleep(500); }
  finally { if (requested) await command('collapse'); }
}
async function sourceNode(run = execute, io = fs, configuration) {
  const layout = captureLayout(configuration);
  const ctl = () => run('/usr/bin/systemctl', ['show', 'tesla-waydroid-compositor.service', '-p', 'MainPID', '--value'], { env: process.env });
  const pid = Number((await ctl()).toString().trim());
  if (!Number.isSafeInteger(pid) || pid < 2) throw new Error('Private compositor unavailable');
  const stat = await io.lstat('/proc/' + pid);
  const argv = (await io.readFile('/proc/' + pid + '/cmdline')).toString().split('\0').filter(Boolean);
  if (stat.uid !== layout.uid || await io.readlink('/proc/' + pid + '/exe') !== '/usr/bin/weston' ||
      !argv.includes('--backend=pipewire') || !argv.includes('--socket=wayland-teslaandroid')) {
    throw new Error('Only the private PipeWire compositor is permitted');
  }
  const snapshot = await run('/usr/sbin/runuser', ['-u', layout.user, '--', '/usr/bin/env', ...layout.userEnvironment, '/usr/bin/pw-dump'], { env: process.env });
  const node = compositorNode(JSON.parse(snapshot.toString()), pid);
  if (Number((await ctl()).toString().trim()) !== pid) throw new Error('Compositor identity changed');
  return node;
}
async function main({ configuration, args = process.argv.slice(2) } = {}) {
  if (process.platform !== 'linux' || process.getuid?.() !== 0 || !Array.isArray(args) || args.length) throw new Error('Fixed compositor launcher requires Linux root');
  configuration = await loadWorkerConfiguration({ role: 'privileged', read: () => configuration });
  const layout = captureLayout(configuration);
  for (const filename of ['compositor-video-launcher.js', 'compositor-h264.py']) {
    protectedStat(await fs.lstat(layout.root + '/runtime/preview-gateway/' + filename), 'file');
  }
  const owner = await prepareCapture('video', { configuration });
  if (!owner) throw new Error('Supervised compositor lease required');
  const pid = (await execute('/usr/bin/lxc-info', ['-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'], { env: process.env })).toString().trim();
  if (!/^\d+$/.test(pid) || Number(pid) < 2) throw new Error('Invalid container identity');
  const adapter = createHostAdapter({ captureMethod: 'logical', hostLibs: layout.hostLibraries });
  let plan, previousKey, revision = 0, timer, stopping = false, polling = false;
  const inspect = async () => {
    const wm = (await adapter.guest(['/system/bin/wm', 'size'])).toString();
    const match = /Override size: (\d+)x(\d+)/.exec(wm) || /Physical size: (\d+)x(\d+)/.exec(wm);
    if (!match) throw new Error('Logical display size unavailable');
    const size = { width: Number(match[1]), height: Number(match[2]), density: 180 };
    validateSize(size);
    const next = capturePlan((await adapter.guest(['/system/bin/dumpsys', 'input'])).toString(), size, await sourceNode(execute, fs, configuration));
    const profile = readVideoSettings(layout.videoSettingsFile, { uid: layout.uid });
    const key = JSON.stringify([next, profile]);
    if (previousKey !== key) { previousKey = key; revision++; }
    plan = { ...next, bitRate: profile.bitRate, maxFps: profile.maxFps };
    return { ...profile, revision };
  };
  const stop = async error => {
    if (stopping) return;
    stopping = true; clearInterval(timer);
    if (error) { console.error(error.message); process.exitCode = 1; }
    try { await controller.stop(); } catch (failure) { console.error(failure.message); process.exitCode = 1; }
  };
  const controller = createVideoCaptureController({ readProfile: inspect,
    prepare: async () => {
      const live = (await execute('/usr/bin/lxc-info', ['-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'], { env: process.env })).toString().trim();
      if (live !== pid || await prepareCapture('video', { configuration }) !== owner) throw new Error('Compositor capture ownership changed');
    },
    launch: () => {
      const child = spawn('/usr/bin/nsenter', compositorLaunchArgs(pid, owner, plan, configuration),
      { shell: false, stdio: ['ignore', 'pipe', 'inherit'], env: process.env });
      child.stdout.pipe(process.stdout);
      armFirstFrameRepaint(child, async () => {
        if (stopping || child.exitCode !== null || child.signalCode !== null) return;
        await repaintDisplay(execute, pid, async () => await prepareCapture('video', { configuration }) === owner);
      });
      return child;
    },
    onFailure: error => { void stop(error); },
  });
  for (const signal of ['SIGTERM', 'SIGINT']) process.once(signal, () => { void stop(); });
  try { await controller.reconcile(); } catch (error) { await stop(error); return; }
  if (!stopping) timer = setInterval(async () => {
    if (stopping || polling) return;
    polling = true;
    try { await controller.reconcile(); } catch (error) { await stop(error); }
    finally { polling = false; }
  }, 2000);
}
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
module.exports = { sourceNode, armFirstFrameRepaint, repaintDisplay, compositorLaunchArgs, main };
