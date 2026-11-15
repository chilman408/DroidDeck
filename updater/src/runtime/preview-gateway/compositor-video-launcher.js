// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs/promises');
const { spawn } = require('node:child_process');
const { createHostAdapter, execute, validateSize } = require('../waydroid-display/host-adapter');
const { compositorNode, capturePlan } = require('../waydroid-display/compositor-source');
const { protectedStat } = require('../waydroid-display/runtime-preflight');
const { prepareCapture, captureEnvironment } = require('./capture-lease');
const { captureLayout } = require('./capture-layout');
const { loadWorkerConfiguration } = require('../portable/worker-config');
const { normalizeRuntimeConfig } = require('../portable/runtime-config');
const { createVideoCaptureController } = require('./video-capture-controller');
const { readVideoSettings } = require('./video-settings');
const { resolveCaptureSettings } = require('../portable/capture-policy');
const { readPreference, preferenceFile, chooseEncoding, createStatusWriter } = require('./video-encoding');
const { setTimeout: delay } = require('node:timers/promises');
function requestedEncoder(environment = process.env) {
  const encoder = environment.DROIDMATE_EXPERIMENTAL_H264_ENCODER ?? 'cpu';
  if (!['cpu', 'vaapi'].includes(encoder)) throw new Error('Invalid experimental H264 encoder');
  return encoder;
}
function requestedResize(environment = process.env) {
  const mode = environment.DROIDMATE_GPU_RESIZE ?? 'off';
  if (!['off', 'selective'].includes(mode)) throw new Error('Invalid selective resize policy');
  return mode;
}
function selectiveResizeMode(plan, mode = 'cpu', encoder = 'cpu', resize = 'off') {
  if (resize === 'off') return mode;
  if (resize !== 'selective' || mode !== 'cpu' || !['cpu','vaapi'].includes(encoder)) throw new Error('Selective resize requires a supported baseline and encoder');
  for (const key of ['width','height','sourceWidth','sourceHeight','left','right','top','bottom']) {
    if (!Number.isSafeInteger(plan[key]) || plan[key] < 0) throw new Error('Invalid resize geometry');
  }
  // Permit only the one-pixel viewport rounding used by standard 480p sizing.
  // Substantial cropping, small resizes, same-size and upscaling stay on CPU.
  if (['left','right','top','bottom'].reduce((sum,key) => sum + plan[key],0) > 1) return 'cpu';
  return plan.width > 0 && plan.height > 0 && plan.width * 4 <= plan.sourceWidth * 3 &&
    plan.height * 4 <= plan.sourceHeight * 3 ? 'hybrid' : 'cpu';
}
function captureChoice(plan, mode, pacing, encoder, resize, policySource = 'default') {
  // The existing protected GPU opt-in now means selective resize, rather than
  // forcing crop/same-size frames through GL. No unit-environment change needed.
  if (resize === 'selective' && mode !== 'cpu') throw new Error('Conflicting selective resize opt-ins');
  const selective = resize === 'selective' || mode === 'hybrid' && policySource === 'protected-policy';
  const selectedMode = selectiveResizeMode(plan, selective ? 'cpu' : mode, encoder, selective ? 'selective' : 'off');
  return { mode: selectedMode, pacing: encoder === 'vaapi' ? 'bounded' : selective ? (selectedMode === 'hybrid' ? 'bounded' : 'legacy') : pacing, selective };
}
function compositorLaunchArgs(pid, owner, plan, configuration, mode = 'cpu', pacing = 'legacy', encoder = 'cpu', supervised = false) {
  const layout = captureLayout(configuration);
  if (typeof pid !== 'string' || !/^[1-9]\d*$/.test(pid) || !Number.isSafeInteger(Number(pid)) || Number(pid) < 2) throw new Error('Invalid compositor container identity');
  captureEnvironment(owner, 'video');
  if (!owner) throw new Error('Supervised compositor lease required');
  if (!['cpu', 'hybrid'].includes(mode)) throw new Error('Invalid experimental capture mode');
  if (!['legacy', 'bounded'].includes(pacing)) throw new Error('Invalid experimental capture pacing');
  if (!['cpu', 'vaapi'].includes(encoder)) throw new Error('Invalid experimental H264 encoder');
  const pacingArguments = pacing === 'bounded' ? ['DROIDDECK_EXPERIMENTAL_FRAME_PACING=bounded'] : [];
  if (mode === 'hybrid' || encoder === 'vaapi' || supervised) {
    const gid = configuration === undefined ? 1000 : normalizeRuntimeConfig(configuration).gid;
    return ['-t', pid, '-n', '/usr/bin/setpriv', '--reuid=' + layout.uid, '--regid=' + gid,
      ...(mode === 'hybrid' || encoder === 'vaapi' ? ['--groups=video,render'] : ['--clear-groups']), '--bounding-set=-all', '--inh-caps=-all', '--ambient-caps=-all', '--no-new-privs',
      '/usr/bin/env', ...layout.userEnvironment, 'TESLA_WAYDROID_RUNTIME_OWNER=' + owner,
      'DROIDDECK_EXPERIMENTAL_GPU_CAPTURE=' + mode,
      ...(encoder === 'vaapi' || supervised ? ['DROIDMATE_EXPERIMENTAL_H264_ENCODER=' + encoder] : []),
      ...(supervised ? ['DROIDMATE_ENCODER_STATUS_FD=3'] : []),
      ...pacingArguments, '/usr/bin/python3',
      layout.root + '/runtime/preview-gateway/compositor-h264.py', JSON.stringify(plan),
      ...(configuration === undefined ? [] : ['--uid', String(layout.uid)])];
  }
  return ['-t', pid, '-n', '/usr/sbin/runuser', '-u', layout.user, '--', '/usr/bin/env',
    ...layout.userEnvironment, 'TESLA_WAYDROID_RUNTIME_OWNER=' + owner, ...pacingArguments, '/usr/bin/python3',
    layout.root + '/runtime/preview-gateway/compositor-h264.py', JSON.stringify(plan),
    ...(configuration === undefined ? [] : ['--uid', String(layout.uid)])];
}
function createEncoderFailSafe({replace,onFailure=()=>{},deadlineMs=4000,schedule=setTimeout,cancel=clearTimeout}) {
  let latched=false,current;const drains=new WeakMap();
  const detach=state=>{state.retired=true;cancel(state.timer);if(current===state)current=undefined;};
  // Controller owns this retirement already. Keep failure reporting until exit,
  // while suppressing another replacement request for the same generation.
  const retire=child=>{if(current?.child===child)current.retiring=true;};
  function attach(child,encoder) {
    const state={child,retired:false,retiring:false,pending:false,buffer:'',timer:undefined};current=state;
    let closed;drains.set(child,new Promise(resolve=>{closed=resolve}));
    const refuse=()=>{
      if(current!==state||state.retired)return;
      latched=true;cancel(state.timer);
      if(state.retiring)return;
      detach(state);
      if(encoder==='vaapi')replace(child);else onFailure(new Error('CPU encoder recovery failed'));
    };
    const channel=child.stdio[3];
    channel.on('data',bytes=>{
      if(current!==state||state.retired)return;
      if(bytes.length>256||state.buffer.length+bytes.length>256){refuse();return;}
      state.buffer+=bytes.toString('ascii');
      let index;
      while((index=state.buffer.indexOf('\n'))>=0){
        const message=state.buffer.slice(0,index);state.buffer=state.buffer.slice(index+1);
        if(message==='vaapi-failed'){refuse();return;}
        if(message==='teardown-begin'&&!state.pending){state.pending=true;state.timer=schedule(refuse,deadlineMs);}
        else if(message==='teardown-done'&&state.pending){state.pending=false;cancel(state.timer);}
        else {refuse();return;}
      }
    });
    channel.once('error',refuse);channel.once('end',()=>{if(encoder==='vaapi'&&(!state.retiring||state.pending))refuse();});
    child.once('exit',(_code,signal)=>{
      if(current===state&&encoder==='vaapi'&&(!state.retiring||signal==='SIGKILL'))refuse();
    });
    child.once('close',()=>{detach(state);closed();});
  }
  return {attach,retire,drained:child=>drains.get(child),encoder:requested=>latched?'cpu':requested,latched:()=>latched};
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
  return cleanup;
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
  const protectedSettings = resolveCaptureSettings(process.env);
  const encoder = protectedSettings.encoder ?? requestedEncoder(process.env);
  let preference = readPreference(preferenceFile(layout.videoSettingsFile), {uid:layout.uid});
  const writeStatus = createStatusWriter({gpuAvailable:encoder === 'vaapi'});
  let actualEncoding='starting', activeChild;
  const report = () => writeStatus(actualEncoding, preference.revision);
  report();
  const supervisedEncoder = encoder === 'vaapi';
  const resize = requestedResize(process.env);
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
    const nextPreference=readPreference(preferenceFile(layout.videoSettingsFile), {uid:layout.uid});
    if(nextPreference.revision!==preference.revision)actualEncoding='starting';
    preference=nextPreference; report();
    const key = JSON.stringify([next, profile, preference]);
    if (previousKey !== key) { previousKey = key; revision++; }
    plan = { ...next, bitRate: profile.bitRate, maxFps: profile.maxFps };
    return { ...profile, revision };
  };
  const stop = async error => {
    if (stopping) return;
    stopping = true; clearInterval(timer); actualEncoding='unavailable'; report();
    if (error) { console.error(error.message); process.exitCode = 1; }
    try { await controller.stop(); } catch (failure) { console.error(failure.message); process.exitCode = 1; }
  };
  const repaints=new WeakMap();
  const failSafe = createEncoderFailSafe({replace: child => {repaints.get(child)?.();void controller.replace(child).catch(error => {void stop(error);});},onFailure:error=>{void stop(error);}});
  const controller = createVideoCaptureController({ readProfile: inspect,
    ...(supervisedEncoder ? {preparationTimeoutMs:5000,onRetire:child=>{if(activeChild===child)activeChild=undefined;repaints.get(child)?.();failSafe.retire(child);},afterRetire:child=>failSafe.drained(child)} : {}),
    prepare: async () => {
      const live = (await execute('/usr/bin/lxc-info', ['-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'], { env: process.env })).toString().trim();
      if (live !== pid || await prepareCapture('video', { configuration }) !== owner) throw new Error('Compositor capture ownership changed');
    },
    launch: () => {
      const selected=chooseEncoding({...protectedSettings,encoder},preference);
      const effectiveEncoder=failSafe.encoder(selected.encoder);
      const choice = captureChoice(plan, selected.captureMode, selected.framePacing, effectiveEncoder, preference.preference==='cpu'?'off':resize, selected.source);
      actualEncoding='starting'; report();
      if (choice.selective) console.log(choice.mode === 'hybrid' ? 'Selective GPU downsize selected' : 'Selective resize retained CPU capture');
      const child = spawn('/usr/bin/nsenter', compositorLaunchArgs(pid, owner, plan, configuration, choice.mode, choice.pacing, effectiveEncoder,supervisedEncoder),
      { shell: false, stdio: ['ignore', 'pipe', 'inherit', ...(supervisedEncoder?['pipe']:[])], env: process.env });
      activeChild=child;
      if(supervisedEncoder)failSafe.attach(child,effectiveEncoder);
      let output='',hardware=false;
      child.stdout.on('data',bytes=>{
        if(activeChild!==child||stopping)return;
        output=(output+bytes.toString('utf8')).slice(-4096);
        if(output.includes('Experimental VAAPI H264 encoder active'))hardware=true;
        if(output.includes('Compositor H264 configuration and first key frame ready')){actualEncoding=effectiveEncoder==='vaapi'&&hardware?'gpu':'cpu';report();}
      });
      child.stdout.pipe(process.stdout);
      repaints.set(child,armFirstFrameRepaint(child, async () => {
        if (stopping || child.exitCode !== null || child.signalCode !== null) return;
        await repaintDisplay(execute, pid, async () => await prepareCapture('video', { configuration }) === owner);
      }));
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
module.exports = { sourceNode, armFirstFrameRepaint, repaintDisplay, compositorLaunchArgs, requestedEncoder, requestedResize, selectiveResizeMode, captureChoice, createEncoderFailSafe, main };
