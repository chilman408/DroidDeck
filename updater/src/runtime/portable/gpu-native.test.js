// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { EXPECTED, LIBRARY, CONSUMERS, evaluateNative, inspectNativeGpu } = require('./gpu-native');
const mapping = path => `1000-2000 r-xp 00001000 fd:01 500 ${path}\n`;
const good = () => ({ properties: { ...EXPECTED }, renderer: 'GLES: Mesa, virgl (AMD Radeon Graphics), OpenGL ES 3.2 Mesa 26.0.8\n',
  maps: mapping(LIBRARY) + mapping(CONSUMERS[0]) });
test('native GPU proof preserves software decoding and does not claim media or browser acceptance', () => {
  const result = evaluateNative(good());
  assert.equal(result.nativeGpuVerified, true); assert.deepEqual(result.reasons, []);
  assert.equal(result.mediaPlaybackVerified, false); assert.equal(result.browserStreamVerified, false);
  assert.ok(Object.isFrozen(result)); assert.ok(Object.isFrozen(result.properties));
});
for (const key of Object.keys(EXPECTED)) test('rejects wrong native property ' + key, () => {
  const value = good(); value.properties[key] = 'wrong';
  assert.deepEqual(evaluateNative(value).reasons, ['property:' + key]);
});
test('library presence or allocator service alone cannot pass without executable consumer maps', () => {
  for (const maps of ['', mapping(LIBRARY), mapping(CONSUMERS[0]),
    mapping(LIBRARY) + mapping(CONSUMERS[0] + ' (deleted)'),
    mapping(LIBRARY).replace('r-xp', 'rw-p') + mapping(CONSUMERS[0])]) {
    assert.equal(evaluateNative({ ...good(), maps }).nativeGpuVerified, false);
  }
  assert.equal(evaluateNative({ ...good(), maps: mapping(LIBRARY) + mapping(CONSUMERS[1]) }).nativeGpuVerified, true);
});
test('software and ambiguous renderer evidence is rejected', () => {
  for (const renderer of ['GLES: SwiftShader', 'GLES: Mesa llvmpipe', 'GLES: virgl softpipe', 'GLES: nonvirgl']) {
    assert.equal(evaluateNative({ ...good(), renderer }).nativeGpuVerified, false);
  }
  for (const renderer of ['', 'GLES: virgl\nGLES: virgl', 'GLES:' + 'x'.repeat(513), 'GLES: virgl\0']) {
    assert.throws(() => evaluateNative({ ...good(), renderer }));
  }
});
test('bounds, malformed mappings, invalid UTF8 and extra property records are rejected', () => {
  for (const maps of ['bad mapping', Buffer.alloc(4 * 1024 * 1024 + 1), Buffer.from([255]), mapping(LIBRARY) + '\0']) {
    assert.throws(() => evaluateNative({ ...good(), maps }));
  }
  assert.throws(() => evaluateNative({ ...good(), properties: { ...EXPECTED, unrelated: 'secret' } }));
  assert.throws(() => evaluateNative({ ...good(), properties: { ...EXPECTED, 'sys.boot_completed': 'x'.repeat(257) } }));
});
function fixture({ mutate, state = 'RUNNING', pidText = '700', androidAfter = '30:400/40:500', identityAfter = 'ns:birth' } = {}) {
  const calls = []; let identityReads = 0, androidReads = 0, propertyReads = 0;
  const observation = good();
  const run = async (file, args, options) => {
    calls.push({ file, args }); assert.equal(options.timeoutMs, 10000); assert.ok(options.signal instanceof AbortSignal);
    if (file === '/usr/bin/lxc-info') return args.at(-1) === '-sH' ? state : pidText;
    assert.equal(file, '/usr/bin/nsenter'); assert.deepEqual(args.slice(0, 7), ['-t', '700', '-m', '-p', '-r', '--', args[6]]);
    const guest = args.slice(6);
    if (guest[0] === '/system/bin/getprop') {
      propertyReads++; return mutate && propertyReads > 10 ? 'changed' : observation.properties[guest[1]];
    }
    if (guest[0] === '/system/bin/dumpsys') return observation.renderer;
    assert.deepEqual(guest, ['/system/bin/toybox', 'cat', '/proc/40/maps']); return observation.maps;
  };
  return { calls, deps: { run, readIdentity: async () => ++identityReads === 1 ? 'ns:birth' : identityAfter,
    readAndroid: async () => ++androidReads === 1 ? '30:400/40:500' : androidAfter } };
}
test('read-only native probe binds commands to one stable container and Android generation', async () => {
  const value = fixture(), result = await inspectNativeGpu(value.deps);
  assert.equal(result.nativeGpuVerified, true); assert.equal(result.containerPid, 700); assert.equal(result.surfaceFlingerPid, 40);
  assert.equal(result.containerIdentity, 'ns:birth'); assert.equal(result.androidIdentity, '30:400/40:500');
  assert.equal(value.calls.filter(call => call.args.includes('/system/bin/getprop')).length, 20);
  assert.ok(value.calls.every(call => !call.args.some(arg => /setprop|unfreeze|upgrade|account|\/drm|persistent_properties/.test(arg))));
});
for (const [name, changes] of [['properties', { mutate: true }], ['namespace', { identityAfter: 'different' }],
  ['Android process birth', { androidAfter: '30:400/40:501' }]]) test('rejects raced ' + name, async () => {
  await assert.rejects(inspectNativeGpu(fixture(changes).deps), /changed/);
});
test('stopped/frozen containers and invalid PID do not execute guest commands', async () => {
  for (const changes of [{ state: 'STOPPED' }, { state: 'FROZEN' }, { pidText: '1' }, { pidText: '700; id' }]) {
    const value = fixture(changes); await assert.rejects(inspectNativeGpu(value.deps));
    assert.ok(value.calls.every(call => call.file === '/usr/bin/lxc-info'));
  }
});
test('malformed Android identity is rejected before guest evidence reads', async () => {
  const value = fixture(); value.deps.readAndroid = async () => '30:400/40;id:500';
  await assert.rejects(inspectNativeGpu(value.deps), /identity/);
  assert.ok(value.calls.every(call => call.file === '/usr/bin/lxc-info'));
});
test('cancelled inspection stops before any command', async () => {
  const value = fixture(); const controller = new AbortController(); controller.abort();
  await assert.rejects(inspectNativeGpu({ ...value.deps, signal: controller.signal })); assert.equal(value.calls.length, 0);
});
test('cancellation during evidence collection prevents publishing a health result', async () => {
  const value = fixture(), controller = new AbortController(), run = value.deps.run;
  value.deps.run = async (file, args, options) => {
    const result = await run(file, args, options);
    if (args.includes('/system/bin/dumpsys')) controller.abort();
    return result;
  };
  await assert.rejects(inspectNativeGpu({ ...value.deps, signal: controller.signal }));
  assert.equal(value.calls.filter(call => call.args.includes('/system/bin/getprop')).length, 10);
  assert.ok(value.calls.every(call => !call.args.includes('/proc/40/maps')));
});
