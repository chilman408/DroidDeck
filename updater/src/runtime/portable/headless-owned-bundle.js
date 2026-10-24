// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs'), path = require('node:path'), os = require('node:os'), { createHash } = require('node:crypto');
const { readBoundedRegular } = require('./read-bounded');
const { normalizeRuntimeConfig, readRuntimeConfig } = require('./runtime-config');
const { configurationDigest } = require('./configuration-generation');
const PINS = Object.freeze({ 'headless-runtime.py': '2c0c34bf167411556e5a478669a86ec791a73e257cec117c596a0570af368d7b',
  'headless-session.py': '744be141e0b26b507d70ac77275f2507f2e28a4659a0f0489206d5fd384334ba',
  'host-platform-compat.py': '6a1191680ed1380a1bcecbd9c6704cfa5ff9837935b24e33af9aae1fc51471b2' });
const FLAGS = ['installable', 'readyForActivation', 'servicesActivated', 'androidOperationsExecuted', 'accountValidated'];
const HASHES = ['configurationDigest', 'serviceManifestSha256', 'metadataSha256', 'pidManifestSha256',
  'originalConfigurationSha256', 'softwareConfigurationSha256', 'gpuConfigurationSha256'];
const RECEIPT_FLAGS = { accountValidated: true, servicesActivated: false, installable: false, readyForActivation: false,
  androidOperationsExecuted: false, gpuProfilePolicyRegenerated: true, codecsVerified: true, independentRecoveryArmed: false };
const sha = bytes => createHash('sha256').update(bytes).digest('hex'), encode = value => Buffer.from(JSON.stringify(value, null, 2) + '\n');
function canonical(value) {
  if (typeof value !== 'string' || !path.isAbsolute(value) || path.resolve(value) !== value || value.includes('\0')) throw Error('Canonical component path required');
  return value;
}
function directories(filename, privateHome) {
  let current = path.parse(canonical(filename)).root;
  for (const part of filename.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part); const st = fs.lstatSync(current);
    if (!st.isDirectory() || st.isSymbolicLink() || (privateHome && (current === privateHome || current.startsWith(privateHome + path.sep)) &&
        (st.uid !== process.getuid() || st.mode & 0o022))) throw Error('Unsafe component parent');
  }
}
function validateReceipt(configuration, receipt) {
  const keys = ['kind', ...HASHES, ...Object.keys(RECEIPT_FLAGS)].sort();
  if (!receipt || typeof receipt !== 'object' || Array.isArray(receipt) || Object.keys(receipt).sort().join(',') !== keys.join(',') ||
      receipt.kind !== 'protected-machine-gpu-profile-policy' || HASHES.some(k => typeof receipt[k] !== 'string' || !/^[a-f0-9]{64}$/.test(receipt[k])) ||
      Object.entries(RECEIPT_FLAGS).some(([k, v]) => receipt[k] !== v) || receipt.configurationDigest !== configurationDigest(configuration) ||
      new Set([receipt.originalConfigurationSha256, receipt.softwareConfigurationSha256, receipt.gpuConfigurationSha256]).size !== 3) throw Error('Owned machine policy receipt rejected');
}
function validateOwnedHeadlessPolicy(configuration, value) {
  const exact = (v, keys) => v && typeof v === 'object' && !Array.isArray(v) && Object.keys(v).sort().join(',') === keys.sort().join(',');
  const generation = configurationDigest(configuration);
  if (!exact(value, ['version', 'configurationDigest', 'originalConfigurationSha256', 'profiles']) || value.version !== 2 ||
      value.configurationDigest !== generation || !exact(value.profiles, ['software', 'gpu'])) throw Error('Closed owned headless policy required');
  const hashes = [value.originalConfigurationSha256, value.profiles.software, value.profiles.gpu];
  if (hashes.some(h => typeof h !== 'string' || !/^[a-f0-9]{64}$/.test(h)) || new Set(hashes).size !== 3) throw Error('Distinct owned profiles required');
  return { version: 2, configurationDigest: generation, originalConfigurationSha256: hashes[0], profiles: { software: hashes[1], gpu: hashes[2] } };
}
function createOwnedHeadlessPolicy(configuration, receipt) {
  validateReceipt(configuration, receipt);
  return validateOwnedHeadlessPolicy(configuration, { version: 2, configurationDigest: receipt.configurationDigest,
    originalConfigurationSha256: receipt.originalConfigurationSha256,
    profiles: { software: receipt.softwareConfigurationSha256, gpu: receipt.gpuConfigurationSha256 } });
}
function policyPlan(configuration, value) {
  const policy = validateOwnedHeadlessPolicy(configuration, value), generation = policy.configurationDigest, files = {};
  for (const name of ['headless-owned-runtime.py', ...Object.keys(PINS)]) {
    directories(__dirname);
    const bytes = Buffer.from(readBoundedRegular(path.join(__dirname, name), 32768).toString('utf8').replace(/\r\n/g, '\n'));
    if (PINS[name] && sha(bytes) !== PINS[name]) throw Error('Accepted session source pin changed');
    files[name] = bytes;
  }
  files['policy.json'] = encode(policy);
  files['systemd/60-droiddeck-headless.conf'] = Buffer.from('[Service]\nType=notify\nNotifyAccess=main\nExecStart=\n' +
    'ExecStart=/usr/bin/python3 -I -B /opt/droiddeck/headless-owned-runtime/headless-owned-runtime.py\nTimeoutStartSec=60s\n');
  return { generation, files, policy };
}
function plan(configuration, receipt) { return policyPlan(configuration, createOwnedHeadlessPolicy(configuration, receipt)); }
function manifest(p) {
  return { version: 1, product: 'DroidDeck', kind: 'headless-owned-session-component', configurationDigest: p.generation,
    originalConfigurationSha256: p.policy.originalConfigurationSha256, profiles: p.policy.profiles,
    ...Object.fromEntries(FLAGS.map(k => [k, false])),
    files: Object.keys(p.files).sort().map(name => ({ path: name, size: p.files[name].length, sha256: sha(p.files[name]) })) };
}
function verifyOwnedHeadlessBundle(configuration, receipt, directory) {
  return verifyOwnedHeadlessPolicy(configuration, createOwnedHeadlessPolicy(configuration, receipt), directory);
}
function createOwnedHeadlessPlan(configuration, policy) {
  const p = policyPlan(configuration, policy);
  return { files: p.files, manifest: manifest(p) };
}
function verifyOwnedHeadlessPolicy(configuration, policy, directory) {
  // Recipe consistency only: this does not validate host ownership or authorize activation.
  const root = canonical(directory), p = policyPlan(configuration, policy), expected = manifest(p);
  directories(root); directories(path.join(root, 'systemd'));
  if (fs.readdirSync(root).sort().join(',') !== 'bundle-manifest.json,headless-owned-runtime.py,headless-runtime.py,headless-session.py,host-platform-compat.py,policy.json,systemd' ||
      fs.readdirSync(path.join(root, 'systemd')).join(',') !== '60-droiddeck-headless.conf') throw Error('Unexpected owned headless inventory');
  for (const [name, bytes] of Object.entries(p.files)) {
    if (!readBoundedRegular(path.join(root, name), 32768).equals(bytes)) throw Error('Owned headless recipe changed');
  }
  if (!readBoundedRegular(path.join(root, 'bundle-manifest.json'), 32768).equals(encode(expected))) throw Error('Owned headless manifest changed');
  return expected;
}
function write(filename, bytes) {
  const fd = fs.openSync(filename, fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_WRONLY, 0o600);
  try { fs.writeFileSync(fd, bytes); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
}
function buildOwnedHeadlessBundle(configuration, receipt, directory) {
  const root = canonical(directory), p = plan(configuration, receipt);
  directories(path.dirname(root)); fs.mkdirSync(root, { mode: 0o700 });
  fs.mkdirSync(path.join(root, 'systemd'), { mode: 0o700 });
  for (const [name, bytes] of Object.entries(p.files)) write(path.join(root, name), bytes);
  write(path.join(root, 'bundle-manifest.json'), encode(manifest(p)));
  return verifyOwnedHeadlessBundle(configuration, receipt, root);
}
function stdinReceipt() {
  const bytes = Buffer.alloc(16385); let length = 0;
  while (length < bytes.length) { const n = fs.readSync(0, bytes, length, bytes.length - length, null); if (!n) break; length += n; }
  if (!length || length > 16384) throw Error('Bounded receipt required');
  return JSON.parse(bytes.subarray(0, length).toString('utf8'));
}
function readOwnedPolicyReceipt(configuration, input) {
  let receipt;
  if (input === '-') receipt = stdinReceipt();
  else { const file = canonical(input); directories(path.dirname(file)); receipt = JSON.parse(readBoundedRegular(file, 16384)); }
  validateReceipt(configuration, receipt);
  return receipt;
}
function readOwnedHeadlessReceipt(configuration, metadata, input) {
  const receipt = readOwnedPolicyReceipt(configuration, input);
  if (!Buffer.isBuffer(metadata) || !metadata.length || metadata.length > 16384 || sha(metadata) !== receipt.metadataSha256) throw Error('Machine metadata changed');
  return receipt;
}
function main(args) {
  if (args.length !== 4 || !['build', 'verify'].includes(args[0])) throw Error('Use build|verify CONFIG GPU_POLICY_RECEIPT OUTPUT');
  const input = canonical(args[1]); directories(path.dirname(input));
  const metadata = readBoundedRegular(input, 16384), configuration = readRuntimeConfig(input);
  const receipt = readOwnedHeadlessReceipt(configuration, metadata, args[2]);
  if (!readBoundedRegular(input, 16384).equals(metadata)) throw Error('Machine metadata changed');
  const output = canonical(args[3]); let m;
  if (args[0] === 'build') {
    if (process.platform !== 'linux' || !process.getuid || process.getuid() === 0) throw Error('Unprivileged Linux build required');
    const home = path.resolve(os.homedir());
    if (!output.startsWith(home + path.sep)) throw Error('Component output outside home');
    directories(path.dirname(output), home); m = buildOwnedHeadlessBundle(configuration, receipt, output);
  } else m = verifyOwnedHeadlessBundle(configuration, receipt, output);
  console.log(JSON.stringify({ kind: m.kind, configurationDigest: m.configurationDigest, files: m.files.length,
    installable: false, readyForActivation: false, servicesActivated: false, androidOperationsExecuted: false }));
}
if (require.main === module) { try { main(process.argv.slice(2)); } catch (_) { console.error('Owned headless component rejected; no activation performed'); process.exitCode = 1; } }
module.exports = { buildOwnedHeadlessBundle, verifyOwnedHeadlessBundle, createOwnedHeadlessPolicy, createOwnedHeadlessPlan,
  validateOwnedHeadlessPolicy, verifyOwnedHeadlessPolicy, readOwnedHeadlessReceipt, readOwnedPolicyReceipt, main };
