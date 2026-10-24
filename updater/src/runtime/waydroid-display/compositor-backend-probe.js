// SPDX-License-Identifier: GPL-3.0-only
const path = require('node:path'), { execute } = require('./host-adapter');
const net = require('node:net');
const { ScrcpyStream } = require('../preview-gateway/scrcpy-stream');
const WESTON = 'tesla-capture-pattern-compositor-20261009', PATTERN = 'tesla-capture-pattern-source-20261009';
const ENCODER = 'tesla-capture-pattern-encoder-20261009';
const SOCKET = 'wayland-capture-pattern-20261009';
const DMA_UNITS = ['tesla-capture-dmabuf-compositor-20261010f', 'tesla-capture-dmabuf-source-20261010f',
  'tesla-capture-dmabuf-encoder-20261010f'];
const CPU_UNITS = ['tesla-capture-cpu-buffer-compositor-20261011n', 'tesla-capture-cpu-buffer-source-20261011n',
  'tesla-capture-cpu-buffer-encoder-20261011n'];
const { compositorNode: patternNode } = require('./compositor-source');
function rendererFor(args) {
  if (!Array.isArray(args) || args.length > 1 || (args[0] !== undefined &&
      !['--gl', '--gl-dmabuf', '--gl-dmabuf-implicit', '--gl-cpu-shm', '--gl-cpu-dmabuf'].includes(args[0]))) throw new Error('Only fixed generated profiles are supported');
  return args.length ? 'gl' : 'pixman';
}
function profileFor(args) {
  if (args?.length === 1 && ['--gl-cpu-shm', '--gl-cpu-dmabuf'].includes(args[0])) {
    const dmabuf = args[0] === '--gl-cpu-dmabuf';
    return { renderer: rendererFor(args), dmabuf, cpu: true, units: [...CPU_UNITS],
      socket: 'wayland-capture-cpu-buffer-20261011n',
      source: [path.join(__dirname, 'cpu-buffer-build/client'), dmabuf ? '--dmabuf' : '--shm'],
      probe: ['--cpu-pattern'] };
  }
  const renderer = rendererFor(args), dmabuf = args[0]?.startsWith('--gl-dmabuf') === true;
  return { renderer, dmabuf, units: dmabuf ? [...DMA_UNITS] : [WESTON, PATTERN, ENCODER],
    socket: dmabuf ? 'wayland-capture-dmabuf-20261010f' : SOCKET,
    source: dmabuf ? ['/usr/bin/weston-simple-dmabuf-egl', '--drm-render-node=/dev/dri/renderD128',
      '--size=240', '--format=0x34325258', '--explicit-sync=' + (args[0] === '--gl-dmabuf-implicit' ? '0' : '1')] :
      ['GDK_BACKEND=wayland', 'GSK_RENDERER=cairo', '/usr/bin/python3', path.join(__dirname, 'compositor-pattern-window.py')],
    probe: dmabuf ? ['--dmabuf'] : [] };
}
function rendererFacts(text) {
  if (typeof text !== 'string' || text.length > 1048576) throw new Error('Invalid isolated renderer metadata');
  const lines = text.split(/\r?\n/).filter(line => /GL renderer:/.test(line));
  const hardwareObserved = lines.length === 1 && /virgl|radeonsi|AMD Radeon/i.test(lines[0]) &&
    !/llvmpipe|softpipe|swiftshader/i.test(lines[0]);
  return { hardwareObserved, rendererLines: lines.length };
}
async function stopTrialUnits(started, ctl) {
  if (!Array.isArray(started) || started.some(unit => ![WESTON, PATTERN, ENCODER, ...DMA_UNITS, ...CPU_UNITS].includes(unit))) throw new Error('Invalid trial ownership');
  const failures = [];
  for (const unit of [...started].reverse()) {
    try {
      if ((await ctl('show', unit + '.service', '-p', 'LoadState', '--value')).toString().trim() !== 'not-found') {
        await ctl('stop', unit + '.service');
      }
    } catch (error) { failures.push(error); }
  }
  if (failures.length) throw new AggregateError(failures, 'Isolated trial cleanup failed');
}
async function main() {
  if (process.platform !== 'linux' || process.getuid?.() !== 1000) throw new Error('Isolated compositor probe requires hilman');
  const profile = profileFor(process.argv.slice(2)), { renderer } = profile;
  const [compositorUnit, sourceUnit, encoderUnit] = profile.units;
  const env = { ...process.env, XDG_RUNTIME_DIR: '/run/user/1000', DBUS_SESSION_BUS_ADDRESS: 'unix:path=/run/user/1000/bus' };
  const run = (file, args, timeoutMs = 10000) => execute(file, args, { env, timeoutMs });
  const ctl = (...args) => run('/usr/bin/systemctl', ['--user', ...args]);
  const started = [];
  for (const unit of [WESTON, PATTERN, ENCODER, ...DMA_UNITS, ...CPU_UNITS]) {
    const state = (await ctl('show', unit + '.service', '-p', 'LoadState', '--value')).toString().trim();
    if (state !== 'not-found') throw new Error('Refusing an existing trial unit');
  }
  async function launch(unit, args) {
    await run('/usr/bin/systemd-run', ['--user', '--unit=' + unit, '--collect',
      '--property=RuntimeMaxSec=45', '--property=KillMode=control-group', '--property=UMask=0077', ...args]);
    started.push(unit);
  }
  let failure;
  try {
    await launch(compositorUnit, ['/usr/bin/weston', '--backend=pipewire', '--renderer=' + renderer, '--socket=' + profile.socket,
      '--width=320', '--height=240', '--idle-time=0', '--no-config', '--shell=kiosk-shell.so']);
    let node, snapshot, compositorPid; const deadline = Date.now() + 10000;
    while (Date.now() < deadline) {
      const pid = Number((await ctl('show', compositorUnit + '.service', '-p', 'MainPID', '--value')).toString().trim());
      snapshot = JSON.parse((await run('/usr/bin/pw-dump', [])).toString()); compositorPid = pid;
      try { node = patternNode(snapshot, pid); break; }
      catch (error) {
        if (!error.message.startsWith('Isolated Weston pattern node')) throw error;
        await new Promise(resolve => setTimeout(resolve, 250));
      }
    }
    if (!node) {
      console.error(JSON.stringify({ compositorPid, patternCandidates: snapshot.filter(object =>
        object?.type === 'PipeWire:Interface:Node' && object.info?.props?.['node.name']?.startsWith('weston.')).map(object => ({
          id: object.id, mediaClass: object.info.props['media.class'] ?? null,
          process: object.info.props['application.process.id'] ?? null, client: object.info.props['client.id'] ?? null,
        })), clients: snapshot.filter(object => object?.type === 'PipeWire:Interface:Client' &&
          Number(object.info?.props?.['application.process.id']) === compositorPid).map(object => ({ id: object.id })) }));
      throw new Error('Isolated compositor failed to publish a verified pattern node');
    }
    let gpuFacts = null;
    if (profile.dmabuf || profile.cpu) {
      const invocation = (await ctl('show', compositorUnit + '.service', '-p', 'InvocationID', '--value')).toString().trim();
      if (!/^[a-f0-9]{32}$/.test(invocation)) throw new Error('Invalid isolated compositor invocation');
      gpuFacts = rendererFacts((await run('/usr/bin/journalctl', ['--user',
        '_SYSTEMD_INVOCATION_ID=' + invocation, '--no-pager', '-n', '100', '-o', 'cat'])).toString());
    }
    if (gpuFacts && !gpuFacts.hardwareObserved) throw new Error('Isolated DMA-buffer test lacks verified GPU renderer');
    await launch(sourceUnit, ['/usr/bin/env', 'WAYLAND_DISPLAY=' + profile.socket, ...profile.source]);
    const result = JSON.parse((await run('/usr/bin/python3', [path.join(__dirname, 'compositor-pattern-probe.py'),
      String(node.serial), ...profile.probe], 20000)).toString());
    const plan = { serial: node.serial, width: 320, height: 240, sourceWidth: 320, sourceHeight: 240,
      left: 0, top: 0, right: 0, bottom: 0, bitRate: 1000000, maxFps: 30 };
    await launch(encoderUnit, ['/usr/bin/env', 'TESLA_WAYDROID_RUNTIME_OWNER=' + '1'.repeat(32),
      '/usr/bin/python3', path.join(__dirname, '../preview-gateway/compositor-h264.py'), JSON.stringify(plan)]);
    await new Promise(resolve => setTimeout(resolve, 300));
    const packets = await new Promise((resolve, reject) => {
      const socket = net.connect({ path: '\0scrcpy_54410001' });
      let session = false, config = false, key = false, frames = 0;
      const finish = error => { clearTimeout(timer); socket.destroy(); error ? reject(error) : resolve({ frames, config, key, session }); };
      const timer = setTimeout(() => finish(new Error('Generated producer protocol timed out')), 12000);
      const parser = new ScrcpyStream(event => {
        if (event.type === 'session') {
          if (event.width !== 320 || event.height !== 240) throw new Error('Generated producer geometry mismatch');
          session = true;
        } else if (event.config) config = true;
        else { key ||= event.key; frames++; if (frames === 30 && config && key && session) finish(); }
      });
      socket.once('error', finish);
      socket.on('data', bytes => { try { parser.push(bytes); } catch (error) { finish(error); } });
    });
    console.log(JSON.stringify({ ...result, producerProtocol: packets, renderer, gpuFacts, dmabuf: profile.dmabuf,
      cpuWritten: profile.cpu === true,
      explicitSyncRequested: profile.dmabuf && !profile.cpu ? process.argv[2] !== '--gl-dmabuf-implicit' : null,
      isolated: true, androidRestarted: false,
      scope: 'Generated private compositor pattern; not Android or paid playback acceptance' }));
  } catch (error) { failure = error; }
  try { await stopTrialUnits(started, ctl); }
  catch (error) { if (failure) throw new AggregateError([failure, error], failure.message + '; trial cleanup also failed'); throw error; }
  if (failure) throw failure;
}
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
module.exports = { patternNode, stopTrialUnits, rendererFor, profileFor, rendererFacts };
