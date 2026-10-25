// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { CPU, evaluateCpuFallback, inspectCpuFallback } = require('./cpu-fallback');
const { EXPECTED } = require('./gpu-native');
const good = () => ({ kind: 'android-gpu-native-health', nativeGpuVerified: false,
  properties: { ...EXPECTED, ...CPU, 'ro.hardware.gralloc': 'default',
    'init.svc.vendor.graphics.allocator-4-0-gbm_mesa': '' },
  renderer: 'GLES: Google, SwiftShader, OpenGL ES 3.0',
  libraries: { allocatorLibrary: false, consumerLibrary: false } });

test('CPU fallback is explicit and never claims GPU, media or browser acceptance', () => {
  const result = evaluateCpuFallback(good());
  assert.equal(result.cpuFallbackVerified, true); assert.equal(result.nativeGpuVerified, false);
  assert.equal(result.mediaPlaybackVerified, false); assert.equal(result.browserStreamVerified, false);
  assert.ok(Object.isFrozen(result)); assert.ok(Object.isFrozen(result.reasons));
});
for (const key of Object.keys(CPU)) test('rejects incorrect CPU/codec property ' + key, () => {
  const evidence = good(); evidence.properties[key] = 'wrong';
  assert.equal(evaluateCpuFallback(evidence).cpuFallbackVerified, false);
});
test('native, mixed or unknown renderers and retained GPU allocator cannot pass', () => {
  for (const renderer of ['GLES: virgl', 'GLES: virgl SwiftShader', 'GLES: unknown']) {
    assert.equal(evaluateCpuFallback({ ...good(), renderer }).cpuFallbackVerified, false);
  }
  for (const renderer of ['GLES: Mesa llvmpipe', 'GLES: Mesa softpipe']) {
    assert.equal(evaluateCpuFallback({ ...good(), renderer }).cpuFallbackVerified, true);
  }
  assert.equal(evaluateCpuFallback({ ...good(), nativeGpuVerified: true }).cpuFallbackVerified, false);
  for (const key of ['allocatorLibrary', 'consumerLibrary']) {
    const result = evaluateCpuFallback({ ...good(), libraries: { ...good().libraries, [key]: true } });
    assert.equal(result.cpuFallbackVerified, true); assert.equal(result.gpuLibrariesMapped, true);
  }
  for (const [key, value] of [['ro.hardware.gralloc', 'minigbm_gbm_mesa'],
    ['init.svc.vendor.graphics.allocator-4-0-gbm_mesa', 'running']]) {
    const evidence = good(); evidence.properties[key] = value;
    assert.equal(evaluateCpuFallback(evidence).cpuFallbackVerified, false);
  }
});
test('actual publisher CPU renderer can pass with passively mapped GPU dependencies', () => {
  const result = evaluateCpuFallback({ ...good(),
    renderer: 'GLES: Google Inc. (Google), ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (LLVM 16.0.0) (0x0000C0DE)), SwiftShader driver-5.0.0), OpenGL ES 3.1.0',
    libraries: { allocatorLibrary: true, consumerLibrary: true } });
  assert.equal(result.cpuFallbackVerified, true); assert.equal(result.nativeGpuVerified, false);
  assert.equal(result.gpuLibrariesMapped, true);
});
test('malformed or oversized evidence is refused', () => {
  for (const evidence of [null, {}, { ...good(), nativeGpuVerified: 'false' },
    { ...good(), properties: { ...good().properties, extra: 'unrelated' } },
    { ...good(), renderer: 'GLES: SwiftShader\nGLES: SwiftShader' },
    { ...good(), renderer: 'GLES:' + 'x'.repeat(513) },
    { ...good(), libraries: { allocatorLibrary: 'false', consumerLibrary: false } }]) {
    assert.throws(() => evaluateCpuFallback(evidence));
  }
  const evidence = good(); evidence.properties['sys.boot_completed'] = '\0';
  assert.throws(() => evaluateCpuFallback(evidence));
});

function fixture({ mutate = false, androidAfter = '30:400/40:500', stopped = false } = {}) {
  const evidence = good(), calls = []; let reads = 0, births = 0;
  return { calls, options: {
    readIdentity: async () => 'ns:birth',
    readAndroid: async () => ++births === 1 ? '30:400/40:500' : androidAfter,
    run: async (file, args) => {
      calls.push([file, ...args]);
      if (file === '/usr/bin/lxc-info') return args.at(-1) === '-sH' ? stopped ? 'STOPPED' : 'RUNNING' : '700';
      const guest = args.slice(6);
      if (guest[0] === '/system/bin/getprop') return mutate && ++reads > 10 ? 'changed' : evidence.properties[guest[1]];
      if (guest[0] === '/system/bin/dumpsys') return evidence.renderer + '\n';
      assert.deepEqual(guest, ['/system/bin/toybox', 'cat', '/proc/40/maps']); return '';
    }
  } };
}
test('actual probe route reuses stable generation checks and only reads selected evidence', async () => {
  const value = fixture(), result = await inspectCpuFallback(value.options);
  assert.equal(result.cpuFallbackVerified, true);
  assert.ok(value.calls.every(args => !args.some(arg => /setprop|upgrade|account|persistent_properties|\/drm/.test(arg))));
});
test('changed properties, Android generation and stopped container fail closed', async () => {
  for (const changes of [{ mutate: true }, { androidAfter: '30:400/40:501' }, { stopped: true }]) {
    await assert.rejects(inspectCpuFallback(fixture(changes).options));
  }
});
