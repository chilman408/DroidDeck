// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs'), path = require('node:path'), { createHash } = require('node:crypto');
const { normalizeRuntimeConfig } = require('./runtime-config');
const { configurationDigest } = require('./configuration-generation');
const { createServicePlan } = require('./service-plan');
const { FILES: SERVICE_FILES } = require('./service-bundle');
const { readBoundedRegular } = require('./read-bounded');
const { createPidBundlePlan } = require('./pid-bundle');
const { createOwnedHeadlessPlan, validateOwnedHeadlessPolicy } = require('./headless-owned-bundle');
const sha = value => createHash('sha256').update(value).digest('hex');
const encode = value => JSON.stringify(value, null, 2) + '\n';
const FALSE_FLAGS = ['installable', 'readyForActivation', 'servicesActivated', 'androidOperationsExecuted', 'accountValidated'];
const GPU_SERVICE_PLAN_SHA256 = 'abc5d2b465ce9d4c90b6ce9f9edf77772912ff6ef9b59eb56f22bba768cfcb7f';

function gpuServicePlan(configuration) {
  const source = readBoundedRegular(path.join(__dirname, 'service-plan.js'), 16384);
  if (sha(Buffer.from(source.toString('utf8').replace(/\r\n/g, '\n'))) !== GPU_SERVICE_PLAN_SHA256) throw Error('GPU service source pin changed');
  const plan = createServicePlan(configuration);
  if (Object.keys(plan.files).sort().join('\n') !== SERVICE_FILES.join('\n')) throw Error('GPU service inventory changed');
  return { files: plan.files, manifest: {
    version: 1, product: 'DroidDeck', kind: 'service-component', installable: false,
    sourcePlanSha256: GPU_SERVICE_PLAN_SHA256, configurationDigest: plan.configurationDigest,
    files: SERVICE_FILES.map(name => ({ path: name, size: Buffer.byteLength(plan.files[name]), sha256: sha(plan.files[name]) }))
  } };
}

function createOwnedInstallRecipe(metadata, inputPolicy) {
  if (!Buffer.isBuffer(metadata) || !metadata.length || metadata.length > 16384) throw Error('Bounded metadata required');
  const config = normalizeRuntimeConfig(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(metadata)));
  const policy = validateOwnedHeadlessPolicy(config, inputPolicy), generation = configurationDigest(config);
  const files = { 'runtime.json': encode(config) };
  const parts = [['services', gpuServicePlan(config)], ['pid-compat', createPidBundlePlan(config)],
    ['headless-owned-runtime', createOwnedHeadlessPlan(config, policy)]];
  for (const [directory, part] of parts) {
    if (part.manifest.configurationDigest !== generation) throw Error('Mixed component generations');
    for (const [name, value] of Object.entries(part.files)) {
      const text = Buffer.isBuffer(value) ? new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(value) : value;
      if (typeof text !== 'string' || !Buffer.byteLength(text) || Buffer.byteLength(text) > 16384 || text.includes('\r')) throw Error('Bounded LF recipe required');
      files[directory + '/' + name] = text;
    }
    files[directory + '/bundle-manifest.json'] = encode(part.manifest);
  }
  const names = Object.keys(files).sort();
  if (names.length !== 20) throw Error('Closed owned profile recipe required');
  const sourceMetadataSha256 = sha(metadata), manifest = {
    version: 1, product: 'DroidDeck', kind: 'owned-gpu-install-recipe', configurationDigest: generation,
    sourceMetadataSha256, originalConfigurationSha256: policy.originalConfigurationSha256, profiles: policy.profiles,
    ...Object.fromEntries(FALSE_FLAGS.map(k => [k, false])),
    files: names.map(name => ({ path: name, size: Buffer.byteLength(files[name]), sha256: sha(files[name]) }))
  };
  files['bundle-manifest.json'] = encode(manifest);
  // This describes bytes only. Callers must independently hold/admit their inputs.
  return Object.freeze({ files: Object.freeze(files), summary: Object.freeze({
    kind: 'owned-gpu-install-recipe', payloadFiles: 20, manifestFiles: 4,
    configurationDigest: generation, sourceMetadataSha256, canonicalMetadataSha256: sha(files['runtime.json']),
    serviceManifestSha256: sha(files['services/bundle-manifest.json']),
    pidManifestSha256: sha(files['pid-compat/bundle-manifest.json']),
    ownedHeadlessManifestSha256: sha(files['headless-owned-runtime/bundle-manifest.json']),
    ownedProfilePolicySha256: sha(files['headless-owned-runtime/policy.json']), profileManifestSha256: sha(files['bundle-manifest.json']),
    recipesDerived: true, sourceRecipeCodeExecuted: true, rootProvenanceVerified: false,
    profilePolicyVerified: false, filesWritten: false, targetNamespaceVerified: false,
    gpuAccelerationVerified: false, codecOverlayVerified: false, independentRecoveryArmed: false,
    ...Object.fromEntries(FALSE_FLAGS.map(k => [k, false]))
  }) });
}

function stdin() {
  const bytes = Buffer.alloc(65537); let length = 0;
  while (length < bytes.length) { const n = fs.readSync(0, bytes, length, bytes.length - length, null); if (!n) break; length += n; }
  if (!length || length > 65536) throw Error('Bounded recipe input required');
  const value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes.subarray(0, length)));
  if (!value || Array.isArray(value) || typeof value !== 'object' || Object.keys(value).sort().join(',') !== 'metadata,ownedPolicy' ||
      typeof value.metadata !== 'string') throw Error('Closed recipe input required');
  return value;
}
function main(args) {
  if (args.length !== 2 || args[0] !== 'inspect' || args[1] !== '-') throw Error('Only bounded stdin recipe inspection is exposed');
  const value = stdin(), result = createOwnedInstallRecipe(Buffer.from(value.metadata, 'utf8'), value.ownedPolicy);
  console.log(JSON.stringify(result.summary));
}
if (require.main === module) { try { main(process.argv.slice(2)); } catch (_) {
  console.error('Owned install recipe refused; no files written'); process.exitCode = 1;
} }
module.exports = { createOwnedInstallRecipe, GPU_SERVICE_PLAN_SHA256, main };
