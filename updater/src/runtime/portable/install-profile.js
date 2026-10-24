// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs'), os = require('node:os'), path = require('node:path'), { createHash } = require('node:crypto');
const { readBoundedRegular } = require('./read-bounded');
const { normalizeRuntimeConfig, readRuntimeConfig } = require('./runtime-config');
const { configurationDigest } = require('./configuration-generation');
const { createServicePlan } = require('./service-plan');
const { FILES: SERVICE_FILES, buildServiceBundle, verifyServiceBundle } = require('./service-bundle');
const { createPidPlan, buildPidBundle } = require('./pid-bundle');
const { buildHeadlessBundle, verifyHeadlessBundle } = require('./headless-bundle');
const { buildOwnedHeadlessBundle, createOwnedHeadlessPolicy, validateOwnedHeadlessPolicy,
  verifyOwnedHeadlessPolicy, readOwnedHeadlessReceipt, readOwnedPolicyReceipt } = require('./headless-owned-bundle');
const { validateRuntimeAccount } = require('./runtime-account');
const PID_FILES = Object.freeze(['portable/pid-startup.js', 'systemd/40-droiddeck-pid-compat.conf', 'waydroid-display/pid-compat.js']);
const MANIFEST = 'bundle-manifest.json', LIMIT = 16384;
const FILES = Object.freeze(['runtime.json', 'services/' + MANIFEST, 'pid-compat/' + MANIFEST,
  ...SERVICE_FILES.map(p => 'services/' + p), ...PID_FILES.map(p => 'pid-compat/' + p)].sort());
const HEADLESS_FILES = Object.freeze(['bundle-manifest.json', 'headless-runtime.py', 'headless-session.py',
  'host-platform-compat.py', 'policy.json', 'systemd/60-droiddeck-headless.conf'].map(p => 'headless-runtime/' + p));
const OWNED_HEADLESS_FILES = Object.freeze(['bundle-manifest.json', 'headless-owned-runtime.py', 'headless-runtime.py',
  'headless-session.py', 'host-platform-compat.py', 'policy.json', 'systemd/60-droiddeck-headless.conf'].map(p => 'headless-owned-runtime/' + p));
const FALSE_FLAGS = Object.freeze(['installable', 'readyForActivation', 'servicesActivated', 'androidOperationsExecuted', 'accountValidated']);
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const encode = value => Buffer.from(JSON.stringify(value, null, 2) + '\n');
function exactKeys(value, keys) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).sort().join(',') !== [...keys].sort().join(',')) throw Error('Invalid profile metadata');
}
function profileFiles(profile, owned) {
  if (owned !== undefined) {
    if (profile !== undefined) throw Error('Owned and single-profile modes cannot mix');
    return [...FILES, ...OWNED_HEADLESS_FILES].sort();
  }
  if (profile === undefined) return FILES;
  if (typeof profile !== 'string' || !/^[a-f0-9]{64}$/.test(profile)) throw Error('Invalid Waydroid profile hash');
  return [...FILES, ...HEADLESS_FILES].sort();
}
function absolute(value) {
  if (typeof value !== 'string' || value.length > 4096 || value.includes('\0') ||
      !path.isAbsolute(value) || path.resolve(value) !== value) throw Error('Canonical profile path required');
  return value;
}
function directories(filename, privateHome) {
  let current = path.parse(absolute(filename)).root;
  for (const part of filename.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part); const st = fs.lstatSync(current);
    if (!st.isDirectory() || st.isSymbolicLink()) throw Error('Unsafe profile directory');
    if (privateHome && (current === privateHome || current.startsWith(privateHome + path.sep)) &&
        (st.uid !== process.getuid() || st.mode & 0o022)) throw Error('Non-private profile parent');
  }
}
function inventory(root, includeManifest, files) {
  directories(root);
  const dirs = new Map([['', new Set()]]);
  for (const file of [...files, ...(includeManifest ? [MANIFEST] : [])]) {
    const parts = file.split('/');
    parts.forEach((part, i) => {
      const parent = parts.slice(0, i).join('/'); if (!dirs.has(parent)) dirs.set(parent, new Set()); dirs.get(parent).add(part);
    });
  }
  for (const [dir, expected] of dirs) {
    const target = path.join(root, dir), stat = fs.lstatSync(target);
    if (!stat.isDirectory() || stat.isSymbolicLink() ||
        fs.readdirSync(target).sort().join('\n') !== [...expected].sort().join('\n')) throw Error('Unexpected profile inventory');
  }
}
function record(file, bytes) { return { path: file, size: bytes.length, sha256: sha(bytes) }; }
function compareRecords(records, expected) {
  if (!Array.isArray(records) || records.length !== expected.length) throw Error('Invalid profile file records');
  records.forEach((r, i) => {
    exactKeys(r, ['path', 'size', 'sha256']);
    if (r.path !== expected[i].path || r.size !== expected[i].size || r.sha256 !== expected[i].sha256) throw Error('Profile bytes changed');
  });
}
function inspectParts(root, includeManifest, profile, owned) {
  const files = profileFiles(profile, owned);
  inventory(root, includeManifest, files);
  const bytes = new Map(files.map(p => [p, readBoundedRegular(path.join(root, p), LIMIT)]));
  const config = normalizeRuntimeConfig(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes.get('runtime.json'))));
  if (!bytes.get('runtime.json').equals(encode(config))) throw Error('Noncanonical profile configuration');
  const generation = configurationDigest(config), services = verifyServiceBundle(path.join(root, 'services'));
  const plan = createServicePlan(config), pid = createPidPlan(config);
  if (services.configurationDigest !== generation || pid.configurationDigest !== generation ||
      Object.keys(pid.files).sort().join('\n') !== PID_FILES.join('\n')) throw Error('Mixed profile generations');
  for (const p of SERVICE_FILES) if (!bytes.get('services/' + p).equals(Buffer.from(plan.files[p]))) throw Error('Service recipe differs');
  for (const p of PID_FILES) if (!bytes.get('pid-compat/' + p).equals(Buffer.from(pid.files[p]))) throw Error('PID recipe differs');
  const pidManifest = JSON.parse(bytes.get('pid-compat/' + MANIFEST));
  exactKeys(pidManifest, ['version', 'product', 'kind', 'installable', 'configurationDigest', 'files']);
  if (pidManifest.version !== 1 || pidManifest.product !== 'DroidDeck' || pidManifest.kind !== 'pid-startup-component' ||
      pidManifest.installable !== false || pidManifest.configurationDigest !== generation) throw Error('Invalid PID profile');
  compareRecords(pidManifest.files, PID_FILES.map(p => record(p, bytes.get('pid-compat/' + p))));
  if (profile !== undefined) verifyHeadlessBundle(config, profile, path.join(root, 'headless-runtime'));
  if (owned !== undefined) verifyOwnedHeadlessPolicy(config, owned, path.join(root, 'headless-owned-runtime'));
  return { config, configurationDigest: generation, files: files.map(p => record(p, bytes.get(p))),
    metadataSha256: sha(bytes.get('runtime.json')), serviceManifestSha256: sha(bytes.get('services/' + MANIFEST)),
    pidManifestSha256: sha(bytes.get('pid-compat/' + MANIFEST)) };
}
function matchOwnedReceipt(parts, owned, sourceMetadataSha256, receipt) {
  const expected = createOwnedHeadlessPolicy(parts.config, receipt), actual = validateOwnedHeadlessPolicy(parts.config, owned);
  if (JSON.stringify(expected) !== JSON.stringify(actual) || receipt.metadataSha256 !== sourceMetadataSha256 ||
      ['serviceManifestSha256', 'pidManifestSha256'].some(k => receipt[k] !== parts[k])) throw Error('Owned profile receipt differs');
}
function verifyInstallProfile(directory, expectedProfile, expectedOwnedReceipt) {
  const root = absolute(directory); directories(root);
  const m = JSON.parse(readBoundedRegular(path.join(root, MANIFEST), 32768));
  exactKeys(m, ['version', 'product', 'kind', 'configurationDigest', 'files', ...FALSE_FLAGS,
    ...(m.version === 2 ? ['waydroidConfigurationSha256'] : m.version === 3 ? ['sourceMetadataSha256', 'originalConfigurationSha256', 'profiles'] : [])]);
  if (![1, 2, 3].includes(m.version)) throw Error('Unsupported install profile');
  const profile = m.version === 2 ? m.waydroidConfigurationSha256 : undefined;
  const owned = m.version === 3 ? { version: 2, configurationDigest: m.configurationDigest,
    originalConfigurationSha256: m.originalConfigurationSha256, profiles: m.profiles } : undefined;
  if (owned !== undefined && (typeof m.sourceMetadataSha256 !== 'string' || !/^[a-f0-9]{64}$/.test(m.sourceMetadataSha256))) throw Error('Source metadata digest required');
  if (m.version === 2 && profile === undefined) throw Error('Missing Waydroid profile hash');
  profileFiles(profile, owned);
  if (expectedProfile !== undefined) {
    profileFiles(expectedProfile);
    if (profile !== expectedProfile) throw Error('Waydroid profile differs');
  }
  const parts = inspectParts(root, true, profile, owned);
  if (m.product !== 'DroidDeck' || m.kind !== 'machine-install-profile' ||
      FALSE_FLAGS.some(k => m[k] !== false) || m.configurationDigest !== parts.configurationDigest) throw Error('Invalid install profile');
  compareRecords(m.files, parts.files);
  if (expectedOwnedReceipt !== undefined) {
    if (owned === undefined) throw Error('Owned installation profile required');
    matchOwnedReceipt(parts, owned, m.sourceMetadataSha256, expectedOwnedReceipt);
  }
  return m;
}
function verifyOwnedInstallProfile(directory, receipt) {
  if (receipt === undefined) throw Error('External owned receipt required');
  return verifyInstallProfile(directory, undefined, receipt);
}
function writeSynced(file, bytes) {
  const fd = fs.openSync(file, fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_WRONLY, 0o600);
  try { fs.writeFileSync(fd, bytes); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
}
function buildProfile(configuration, directory, profile, receipt) {
  const config = normalizeRuntimeConfig(configuration), root = absolute(directory);
  const owned = receipt === undefined ? undefined : createOwnedHeadlessPolicy(config, receipt);
  const files = profileFiles(profile, owned);
  directories(path.dirname(root)); fs.mkdirSync(root, { mode: 0o700 });
  writeSynced(path.join(root, 'runtime.json'), encode(config));
  buildServiceBundle({ configuration: config, output: path.join(root, 'services') });
  buildPidBundle(config, path.join(root, 'pid-compat'));
  if (profile !== undefined) buildHeadlessBundle(config, profile, path.join(root, 'headless-runtime'));
  if (owned !== undefined) buildOwnedHeadlessBundle(config, receipt, path.join(root, 'headless-owned-runtime'));
  const parts = inspectParts(root, false, profile, owned);
  if (owned !== undefined) matchOwnedReceipt(parts, owned, receipt.metadataSha256, receipt);
  const m = { version: owned !== undefined ? 3 : profile === undefined ? 1 : 2, product: 'DroidDeck', kind: 'machine-install-profile',
    configurationDigest: parts.configurationDigest,
    ...(profile === undefined ? {} : { waydroidConfigurationSha256: profile }),
    ...(owned === undefined ? {} : { sourceMetadataSha256: receipt.metadataSha256,
      originalConfigurationSha256: owned.originalConfigurationSha256, profiles: owned.profiles }),
    ...Object.fromEntries(FALSE_FLAGS.map(k => [k, false])), files: parts.files };
  for (const p of files) {
    const fd = fs.openSync(path.join(root, p), fs.constants.O_RDWR | (fs.constants.O_NOFOLLOW || 0));
    try { fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
  }
  const pending = path.join(root, MANIFEST + '.partial'); writeSynced(pending, encode(m));
  fs.linkSync(pending, path.join(root, MANIFEST)); fs.unlinkSync(pending);
  return verifyInstallProfile(root, profile, receipt);
}
function buildInstallProfile(configuration, directory, profile) { return buildProfile(configuration, directory, profile); }
function buildOwnedInstallProfile(configuration, receipt, directory) {
  if (receipt === undefined) throw Error('External owned receipt required');
  return buildProfile(configuration, directory, undefined, receipt);
}
async function checkProfileAccount(directory, options) {
  const before = verifyInstallProfile(directory), config = readRuntimeConfig(path.join(directory, 'runtime.json'));
  if (configurationDigest(config) !== before.configurationDigest) throw Error('Profile generation changed');
  await validateRuntimeAccount(config, options);
  const after = verifyInstallProfile(directory);
  if (JSON.stringify(before) !== JSON.stringify(after)) throw Error('Profile changed during account lookup');
  return { kind: 'machine-install-profile-account-check', configurationDigest: before.configurationDigest,
    accountValidated: true, installable: false, readyForActivation: false, servicesActivated: false, androidOperationsExecuted: false };
}
async function main(args) {
  let m, waydroidProfileVerified = false, ownedProfilePolicyMatched = false;
  if ((args.length === 3 && args[0] === 'build') || (args.length === 4 && ['build-headless', 'build-owned-headless'].includes(args[0]))) {
    if (process.platform !== 'linux' || !process.getuid || process.getuid() === 0) throw Error('Unprivileged Linux build required');
    const output = absolute(args[args.length - 1]), home = path.resolve(os.homedir());
    if (!output.startsWith(home + path.sep)) throw Error('Profile output outside home');
    directories(path.dirname(output), home);
    if (args[0] === 'build-owned-headless') {
      const input = absolute(args[1]); directories(path.dirname(input));
      const metadata = readBoundedRegular(input, LIMIT), config = readRuntimeConfig(input);
      const receipt = readOwnedHeadlessReceipt(config, metadata, args[2]);
      if (!readBoundedRegular(input, LIMIT).equals(metadata)) throw Error('Machine metadata changed');
      m = buildOwnedInstallProfile(config, receipt, output);
    } else {
      const profile = args[0] === 'build-headless' ? sha(readBoundedRegular(absolute(args[2]), 65536)) : undefined;
      m = buildInstallProfile(readRuntimeConfig(absolute(args[1])), output, profile);
    }
  } else if (args.length === 2 && args[0] === 'verify') m = verifyInstallProfile(absolute(args[1]));
  else if (args.length === 3 && args[0] === 'verify-headless') {
    const profile = sha(readBoundedRegular(absolute(args[2]), 65536));
    m = verifyInstallProfile(absolute(args[1]), profile); waydroidProfileVerified = true;
  }
  else if (args.length === 3 && args[0] === 'verify-owned-headless') {
    const root = absolute(args[1]); directories(root);
    const config = readRuntimeConfig(path.join(root, 'runtime.json'));
    m = verifyOwnedInstallProfile(root, readOwnedPolicyReceipt(config, args[2])); ownedProfilePolicyMatched = true;
  }
  else if (args.length === 2 && args[0] === 'check-host') {
    if (process.platform !== 'linux') throw Error('Linux account check required');
    m = await checkProfileAccount(absolute(args[1]));
  } else throw Error('Use build CONFIG NEW_OUTPUT, build-headless CONFIG WAYDROID_CONFIG NEW_OUTPUT, build-owned-headless CONFIG RECEIPT NEW_OUTPUT, verify OUTPUT, verify-headless OUTPUT WAYDROID_CONFIG, verify-owned-headless OUTPUT RECEIPT or check-host OUTPUT');
  console.log(JSON.stringify({ kind: m.kind, configurationDigest: m.configurationDigest,
    accountValidated: m.accountValidated, waydroidProfileVerified, installable: false, readyForActivation: false,
    servicesActivated: false, androidOperationsExecuted: false, ...(m.version === 3 ? { ownedProfilePolicyMatched } : {}) }));
}
if (require.main === module) main(process.argv.slice(2)).catch(() => {
  console.error('Install profile rejected; no activation performed'); process.exitCode = 1;
});
module.exports = { buildInstallProfile, verifyInstallProfile, buildOwnedInstallProfile, verifyOwnedInstallProfile, checkProfileAccount, main };
