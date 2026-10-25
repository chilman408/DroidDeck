// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { sourceFacts, generatedForeground, geometrySize, inspectGeneratedSource } = require('./generated-source-motion-probe');
const facts = { sampledFrames: 40, colorSamples: 35, meaningfulMotionSamples: 20, maximumChangedRatio: .8,
  maximumMeanDifference: 40, beforeH264Encoding: true, savedPixels: false, globalSettingsWrites: false, scope: 'generated testsrc2 only' };
function fake(overrides = {}) {
  const calls = []; let probes = 0;
  return { calls, run: async (file, args, options) => {
    calls.push({ file, args, options });
    if (file.endsWith('lxc-info')) return Buffer.from(overrides.changedContainer && probes ? '999' : '123');
    if (file.endsWith('systemctl')) return Buffer.from(args.includes('MainPID') ? '456' :
      ((overrides.activeUnit ? args[1] === overrides.activeUnit : args[1].includes('software-source')) || overrides.ambiguous) ? 'active' : 'inactive');
    if (file.endsWith('nsenter')) {
      if (args.at(-1) === 'activities') return Buffer.from(overrides.foreground || 'mResumedActivity: ActivityRecord{1 u0 org.lineageos.jelly/.MainActivity t3}');
      if (args.at(-1) === 'displays') return Buffer.from('init=1280x720 240dpi cur=960x720 app=960x672');
      return Buffer.from('Viewport INTERNAL: displayId=0, isActive=[1], orientation=0, logicalFrame=[0, 0, 960, 720], physicalFrame=[160, 0, 1120, 720], deviceSize=[1280, 720]');
    }
    assert.equal(file, '/usr/sbin/runuser'); probes++;
    return Buffer.from(JSON.stringify(facts));
  }, io: {
    readFile: async () => Buffer.from((overrides.argv || ['/usr/sbin/runuser', '-u', 'hilman', '--', '/usr/bin/env',
      'NODE_PATH=/home/hilman/teslaandroid-waydroid/runtime/waydroid-display/node_modules', '/usr/bin/node',
      '/opt/teslaandroid-waydroid/source-motion-fixture-20261011j2/generated-transition-fixture.js', '--serve-av1-source-software-retest']).join('\0') + '\0'),
    lstat: async () => ({ uid: 0 }), readlink: async () => '/usr/sbin/runuser'
  }, node: async () => ({ id: 22, serial: 33 }) };
}
test('source metadata contains only bounded numeric facts and fixed scope', () => {
  assert.deepEqual(sourceFacts(facts), facts);
  for (const change of [{ savedPixels: true }, { scope: 'account screen' }, { sampledFrames: 100 },
    { maximumChangedRatio: NaN }, { maximumMeanDifference: 999 }, { extra: 'secret' }]) {
    assert.throws(() => sourceFacts({ ...facts, ...change }));
  }
});
test('foreground and geometry parsing refuse ambiguity and other apps', () => {
  assert.equal(generatedForeground('mResumedActivity: ActivityRecord{1 u0 org.lineageos.jelly/.MainActivity t3}'), true);
  assert.equal(generatedForeground('mResumedActivity: com.netflix.mediaclient/.MainActivity'), false);
  assert.equal(generatedForeground('mResumedActivity: org.lineageos.jelly/.MainActivity\nmResumedActivity: org.lineageos.jelly/.MainActivity'), false);
  assert.deepEqual(geometrySize('init=1280x720 240dpi cur=960x720 app=960x672'), { width: 960, height: 720, density: 180 });
  assert.throws(() => geometrySize('init=1280x720 cur=960x720\ninit=1280x720 cur=960x720'));
});
test('Android current foreground format is accepted only when records agree', () => {
  const record = 'ActivityRecord{a u0 org.lineageos.jelly/.MainActivity t109}';
  assert.equal(generatedForeground('    topResumedActivity=' + record), true);
  assert.equal(generatedForeground('mResumedActivity: ' + record + '\n topResumedActivity=' + record), true);
  assert.equal(generatedForeground('mResumedActivity: ' + record + '\n topResumedActivity=ActivityRecord{b u0 org.lineageos.jelly/.MainActivity t110}'), false);
  assert.equal(generatedForeground('topResumedActivity=ActivityRecord{a u0 com.netflix.mediaclient/.MainActivity t109}'), false);
});
test('owned generated scope produces a cropped raw probe with no settings or encoder commands', async () => {
  const adapter = fake(), result = await inspectGeneratedSource(adapter);
  assert.deepEqual(result, { version: 1, ...facts });
  const child = adapter.calls.find(call => call.file === '/usr/sbin/runuser');
  const plan = JSON.parse(child.args.at(-1));
  assert.equal(plan.serial, 33); assert.equal(plan.left, 160); assert.equal(plan.right, 160);
  assert.equal(child.options.timeoutMs, 20000);
  assert.ok(adapter.calls.every(call => !call.args.includes('setprop') && !call.args.includes('start') && !call.args.includes('stop')));
});
test('Mesa allocator source capture is limited to its separate root and exact fixture command', async () => {
  const argv = ['/usr/sbin/runuser', '-u', 'hilman', '--', '/usr/bin/env',
    'NODE_PATH=/home/hilman/teslaandroid-waydroid/runtime/waydroid-display/node_modules', '/usr/bin/node',
    '/opt/teslaandroid-waydroid/mesa-allocator-fixture-20261011o/generated-transition-fixture.js', '--serve-av1-mesa-allocator'];
  const options = { activeUnit: 'tesla-gpu-mesa-allocator-generated-20261011o.service', argv };
  assert.deepEqual(await inspectGeneratedSource(fake(options)), { version: 1, ...facts });
  await assert.rejects(inspectGeneratedSource(fake({ ...options, argv: argv.map(value =>
    value.includes('mesa-allocator-fixture') ? value.replace('20261011o', '20261011j2') : value) })), /Foreign/);
  const corrected = { activeUnit: 'tesla-gpu-mesa-boot-generated-20261011o2.service', argv: argv.map(value =>
    value.replace('mesa-allocator-fixture-20261011o', 'mesa-boot-fixture-20261011o2')
      .replace('--serve-av1-mesa-allocator', '--serve-av1-mesa-boot')) };
  assert.deepEqual(await inspectGeneratedSource(fake(corrected)), { version: 1, ...facts });
});
test('unowned fixture or foreground refuses before raw capture; stale container refuses results', async () => {
  for (const overrides of [{ ambiguous: true }, { argv: ['/usr/bin/node', '/tmp/foreign.js'] },
    { foreground: 'mResumedActivity: com.netflix.mediaclient/.MainActivity' }]) {
    const adapter = fake(overrides);
    await assert.rejects(inspectGeneratedSource(adapter));
    assert.equal(adapter.calls.some(call => call.file === '/usr/sbin/runuser'), false);
  }
  await assert.rejects(inspectGeneratedSource(fake({ changedContainer: true })), /scope changed/);
});
