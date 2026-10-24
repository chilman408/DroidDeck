// SPDX-License-Identifier: GPL-3.0-only
const { test } = require('node:test'), assert = require('node:assert/strict');
const { patternNode, stopTrialUnits, rendererFor, profileFor, rendererFacts } = require('./compositor-backend-probe');
const node = (id = 77, pid = 40000) => ({ id, type: 'PipeWire:Interface:Node', info: { props: {
  'media.class': 'Video/Source', 'application.process.id': String(pid), 'node.name': 'weston.PipeWire-0', 'object.serial': 90 } } });
test('CPU profiles share only their private units and require fixed generated motion', async () => {
  const shm = profileFor(['--gl-cpu-shm']), dma = profileFor(['--gl-cpu-dmabuf']);
  for (const profile of [shm, dma]) {
    assert.equal(profile.renderer, 'gl'); assert.equal(profile.cpu, true);
    assert.equal(profile.socket, 'wayland-capture-cpu-buffer-20261011n');
    assert.deepEqual(profile.probe, ['--cpu-pattern']);
    assert.ok(profile.source[0].endsWith('cpu-buffer-build' + require('node:path').sep + 'client'));
    assert.ok(profile.units.every(unit => unit.endsWith('-20261011n') && !profileFor([]).units.includes(unit)));
  }
  assert.equal(shm.dmabuf, false); assert.equal(shm.source[1], '--shm');
  assert.equal(dma.dmabuf, true); assert.equal(dma.source[1], '--dmabuf');
  assert.deepEqual(shm.units, dma.units);
  const stops = [];
  await stopTrialUnits(shm.units, async (...args) => {
    if (args[0] === 'stop') stops.push(args[1]);
    return Buffer.from('loaded');
  });
  assert.deepEqual(stops, [...shm.units].reverse().map(unit => unit + '.service'));
  assert.throws(() => profileFor(['--gl-cpu-dmabuf', '--device=other']));
});
test('generated renderer trial permits only pixman default or fixed GL', () => {
  assert.equal(rendererFor([]), 'pixman'); assert.equal(rendererFor(['--gl']), 'gl');
  for (const args of [['--gl', 'extra'], ['--renderer=anything'], null]) assert.throws(() => rendererFor(args));
});

test('DMA-buffer profiles isolate fixed GPU-generated sources and synchronization choices', () => {
  const explicit = profileFor(['--gl-dmabuf']), implicit = profileFor(['--gl-dmabuf-implicit']);
  assert.equal(explicit.renderer, 'gl'); assert.equal(explicit.dmabuf, true);
  assert.equal(explicit.socket, 'wayland-capture-dmabuf-20261010f');
  assert.deepEqual(explicit.source, ['/usr/bin/weston-simple-dmabuf-egl', '--drm-render-node=/dev/dri/renderD128',
    '--size=240', '--format=0x34325258', '--explicit-sync=1']);
  assert.ok(Number(explicit.source.find(arg => arg.startsWith('--size=')).slice(7)) <= 240,
    'Square fixture must fit the fixed 320x240 compositor');
  assert.equal(implicit.source.at(-1), '--explicit-sync=0');
  assert.deepEqual(explicit.probe, ['--dmabuf']);
  assert.ok(explicit.units.every(unit => unit.startsWith('tesla-capture-dmabuf-') && unit.endsWith('-20261010f')));
  assert.ok(explicit.units.every(unit => !profileFor([]).units.includes(unit)));
  assert.equal(profileFor([]).dmabuf, false); assert.deepEqual(profileFor(['--gl']).probe, []);
  for (const args of [['--gl-dmabuf', '--node=77'], ['--gl-dmabuf=/dev/dri/renderD129']]) {
    assert.throws(() => profileFor(args));
  }
});

test('DMA-buffer cleanup stops only owned units in reverse startup order', async () => {
  const units = profileFor(['--gl-dmabuf']).units, stopped = [];
  await stopTrialUnits(units, async (...args) => {
    if (args[0] === 'stop') stopped.push(args[1]);
    return Buffer.from('loaded');
  });
  assert.deepEqual(stopped, [...units].reverse().map(unit => unit + '.service'));
  await assert.rejects(stopTrialUnits(['tesla-waydroid-runtime'], async () => {}), /Invalid trial/);
});

test('DMA-buffer success requires unambiguous GPU compositor metadata, not software GL', () => {
  assert.deepEqual(rendererFacts('[22:10] GL renderer: virgl (AMD Radeon Graphics, radeonsi, phoenix)'),
    { hardwareObserved: true, rendererLines: 1 });
  for (const log of ['', 'GL renderer: llvmpipe (LLVM)', 'GL renderer: virgl (llvmpipe)',
    'GL renderer: AMD Radeon\nGL renderer: AMD Radeon', 'GL renderer: unknown']) {
    assert.equal(rendererFacts(log).hardwareObserved, false);
  }
  assert.throws(() => rendererFacts('x'.repeat(1048577)), /Invalid/);
});
test('only the exact isolated compositor process can supply the test node', () => {
  assert.deepEqual(patternNode([node(), node(88, 40001)], 40000), { id: 77, serial: 90 });
  assert.throws(() => patternNode([node()], 40001), /unavailable/);
  assert.throws(() => patternNode([node(), node(78)], 40000), /ambiguous/);
});
test('unrelated desktop and audio nodes cannot be captured by the pattern test', () => {
  for (const [key, value] of [['node.name', 'desktop-main'], ['media.class', 'Audio/Source']]) {
    const other = node(); other.info.props[key] = value;
    assert.throws(() => patternNode([other], 40000), /unavailable/);
  }
  assert.throws(() => patternNode([node(-1)], 40000), /unavailable/);
  assert.throws(() => patternNode([], 1), /Invalid/);
  assert.throws(() => patternNode(Array(4097), 40000), /Invalid/);
});
test('PipeWire client ownership resolves process identity missing from node properties', () => {
  const source = node(); delete source.info.props['application.process.id']; source.info.props['client.id'] = 65;
  const client = { id: 65, type: 'PipeWire:Interface:Client', info: { props: { 'application.process.id': 40000 } } };
  source.info.props['media.class'] = 'Stream/Output/Video';
  assert.deepEqual(patternNode([source, client], 40000), { id: 77, serial: 90 });
  assert.throws(() => patternNode([source, client], 40001), /unavailable/);
  delete source.info.props['object.serial'];
  assert.throws(() => patternNode([source, client], 40000), /unavailable/);
});
test('collected source unit cannot prevent stopping the owned compositor', async () => {
  const calls = [];
  await stopTrialUnits(['tesla-capture-pattern-compositor-20261009', 'tesla-capture-pattern-source-20261009'], async (...args) => {
    calls.push(args); return Buffer.from(args[1].includes('source') ? 'not-found' : 'loaded');
  });
  assert.equal(calls.filter(args => args[0] === 'stop').length, 1);
  assert.ok(calls.at(-1)[1].includes('compositor'));
  await assert.rejects(stopTrialUnits(['tesla-waydroid-compositor'], async () => {}), /Invalid trial/);
});
test('a failed source stop still attempts compositor cleanup', async () => {
  const stops = [];
  await assert.rejects(stopTrialUnits(['tesla-capture-pattern-compositor-20261009', 'tesla-capture-pattern-source-20261009'], async (...args) => {
    if (args[0] === 'stop') { stops.push(args[1]); if (args[1].includes('source')) throw new Error('stop failed'); }
    return Buffer.from('loaded');
  }), /cleanup failed/);
  assert.equal(stops.length, 2);
});
