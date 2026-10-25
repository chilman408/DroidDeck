// SPDX-License-Identifier: GPL-3.0-only
const { EXPECTED, inspectNativeGpu } = require('./gpu-native');

const CPU = Object.freeze(Object.fromEntries(Object.entries(EXPECTED)
  .filter(([key]) => !['ro.hardware.gralloc', 'init.svc.vendor.graphics.allocator-4-0-gbm_mesa'].includes(key))
  .map(([key, value]) => [key, key === 'ro.waydroid.software_rendering' ? '1' :
    key === 'persist.waydroid.use_subsurface' ? 'false' : value])));

function evaluateCpuFallback(evidence) {
  if (!evidence || evidence.kind !== 'android-gpu-native-health' ||
      typeof evidence.nativeGpuVerified !== 'boolean' || !evidence.properties ||
      Object.keys(evidence.properties).length !== Object.keys(EXPECTED).length ||
      Object.keys(evidence.properties).some(key => !Object.hasOwn(EXPECTED, key)) ||
      Object.values(evidence.properties).some(value => typeof value !== 'string' ||
        value.length > 256 || /[^\x20-\x7e]/.test(value)) ||
      typeof evidence.renderer !== 'string' || evidence.renderer.length > 512 ||
      !/^GLES:[\x20-\x7e]+$/.test(evidence.renderer) ||
      !evidence.libraries || typeof evidence.libraries.allocatorLibrary !== 'boolean' ||
      typeof evidence.libraries.consumerLibrary !== 'boolean') {
    throw new Error('Bounded original Android rendering evidence required');
  }
  const reasons = [];
  for (const [key, value] of Object.entries(CPU)) {
    if (evidence.properties[key] !== value) reasons.push('property:' + key);
  }
  if (evidence.nativeGpuVerified || /\bvirgl\b/i.test(evidence.renderer) ||
      !/\b(?:swiftshader|llvmpipe|softpipe)\b/i.test(evidence.renderer)) reasons.push('renderer');
  if (evidence.properties['ro.hardware.gralloc'] === 'minigbm_gbm_mesa' ||
      evidence.properties['init.svc.vendor.graphics.allocator-4-0-gbm_mesa'] === 'running') reasons.push('gpu-allocator');
  return Object.freeze({ kind: 'android-cpu-fallback-health', cpuFallbackVerified: reasons.length === 0,
    nativeGpuVerified: evidence.nativeGpuVerified, mediaPlaybackVerified: false, browserStreamVerified: false,
    gpuLibrariesMapped: evidence.libraries.allocatorLibrary || evidence.libraries.consumerLibrary,
    reasons: Object.freeze(reasons) });
}

async function inspectCpuFallback(options) {
  // Reuse the original read-only probe's container/process birth and property
  // generation checks. Software fallback never relaxes the native GPU gate.
  return evaluateCpuFallback(await inspectNativeGpu(options));
}
async function main(args) {
  if (args.length || process.platform !== 'linux' || process.getuid() !== 0) {
    throw new Error('No-argument root Linux CPU fallback inspection required');
  }
  const result = await inspectCpuFallback();
  console.log(JSON.stringify(result));
  if (!result.cpuFallbackVerified) process.exitCode = 2;
}
if (require.main === module) main(process.argv.slice(2)).catch(() => {
  console.error('CPU fallback inspection unavailable or unstable'); process.exitCode = 1;
});
module.exports = { CPU, evaluateCpuFallback, inspectCpuFallback };
