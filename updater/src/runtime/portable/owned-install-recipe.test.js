// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path'), { createHash } = require('node:crypto');
const cp = require('node:child_process');
const { createOwnedInstallRecipe: recipe, GPU_SERVICE_PLAN_SHA256 } = require('./owned-install-recipe');
const { normalizeRuntimeConfig } = require('./runtime-config'), { configurationDigest } = require('./configuration-generation');
const { verifyInstallProfile } = require('./install-profile');
const { createServicePlan } = require('./service-plan'), { PLAN_SHA256 } = require('./service-bundle');
const { createPidBundlePlan, buildPidBundle } = require('./pid-bundle');
const { buildOwnedHeadlessBundle } = require('./headless-owned-bundle');
const config = { user: 'alice', uid: 1200, gid: 1300, lanAddress: '192.168.10.8' };
const raw = Buffer.from(JSON.stringify(config));
const sha = value => createHash('sha256').update(value).digest('hex');
const encode = value => JSON.stringify(value, null, 2) + '\n';
function policy(c = config) { return { version: 2, configurationDigest: configurationDigest(c),
  originalConfigurationSha256: sha('generated original'), profiles: { software: sha('generated software'), gpu: sha('generated GPU') } }; }
function temporary(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'droiddeck-owned-recipe-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true })); return root;
}
function cli(input, ...args) { return cp.spawnSync(process.execPath, [path.join(__dirname, 'owned-install-recipe.js'), ...args],
  { input, encoding: 'utf8', timeout: 10000 }); }

test('PID and owned headless bytes match existing builders while new GPU recipe cannot masquerade as legacy profile', t => {
  const root = temporary(t), output = path.join(root, 'owned'), pid = path.join(root, 'pid'), headless = path.join(root, 'headless');
  const owned = policy(), result = recipe(raw, owned), receipt = { kind: 'protected-machine-gpu-profile-policy', configurationDigest: owned.configurationDigest,
    metadataSha256: sha(raw), serviceManifestSha256: result.summary.serviceManifestSha256,
    pidManifestSha256: result.summary.pidManifestSha256,
    originalConfigurationSha256: owned.originalConfigurationSha256, softwareConfigurationSha256: owned.profiles.software,
    gpuConfigurationSha256: owned.profiles.gpu, accountValidated: true, servicesActivated: false, installable: false,
    readyForActivation: false, androidOperationsExecuted: false, gpuProfilePolicyRegenerated: true, codecsVerified: true, independentRecoveryArmed: false };
  buildPidBundle(config, pid); buildOwnedHeadlessBundle(config, receipt, headless);
  assert.equal(Object.keys(result.files).length, 21);
  for (const [name, text] of Object.entries(result.files)) {
    if (name.startsWith('pid-compat/')) assert.equal(text, fs.readFileSync(path.join(pid, name.slice(11)), 'utf8'), name);
    if (name.startsWith('headless-owned-runtime/')) assert.equal(text, fs.readFileSync(path.join(headless, name.slice(23)), 'utf8'), name);
    const file = path.join(output, name); fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, text);
  }
  const m = JSON.parse(result.files['bundle-manifest.json']);
  assert.equal(m.kind, 'owned-gpu-install-recipe'); assert.equal(m.version, 1);
  assert.throws(() => verifyInstallProfile(output));
  assert.equal(result.summary.profileManifestSha256, sha(fs.readFileSync(path.join(output, 'bundle-manifest.json'))));
  assert.notEqual(GPU_SERVICE_PLAN_SHA256, PLAN_SHA256);
});

test('every payload and child manifest has independent matching size digest and generation', () => {
  const r = recipe(raw, policy()), top = JSON.parse(r.files['bundle-manifest.json']);
  assert.equal(top.version, 1); assert.equal(top.files.length, 20);
  for (const row of top.files) {
    assert.equal(row.size, Buffer.byteLength(r.files[row.path])); assert.equal(row.sha256, sha(r.files[row.path]));
  }
  for (const dir of ['services', 'pid-compat', 'headless-owned-runtime']) {
    const m = JSON.parse(r.files[dir + '/bundle-manifest.json']);
    assert.equal(m.configurationDigest, top.configurationDigest);
    for (const row of m.files) {
      assert.equal(row.size, Buffer.byteLength(r.files[dir + '/' + row.path])); assert.equal(row.sha256, sha(r.files[dir + '/' + row.path]));
    }
  }
  const services = createServicePlan(config), sm = JSON.parse(r.files['services/bundle-manifest.json']);
  assert.equal(sm.sourcePlanSha256, GPU_SERVICE_PLAN_SHA256);
  for (const [name, text] of Object.entries(services.files)) assert.equal(r.files['services/' + name], text);
  assert.deepEqual(createPidBundlePlan(config).manifest, JSON.parse(r.files['pid-compat/bundle-manifest.json']));
});

test('recipe API performs no filesystem writes ownership changes or child execution', t => {
  const no = () => { throw Error('unexpected side effect'); };
  const open = fs.openSync;
  t.mock.method(fs, 'openSync', function(name, flags, ...rest) {
    assert.equal(typeof flags, 'number');
    assert.equal(flags & (fs.constants.O_WRONLY | fs.constants.O_RDWR | fs.constants.O_CREAT | fs.constants.O_TRUNC | fs.constants.O_APPEND), 0);
    return open.call(fs, name, flags, ...rest);
  });
  for (const key of ['mkdirSync', 'writeFileSync', 'appendFileSync', 'linkSync', 'symlinkSync', 'renameSync', 'unlinkSync',
    'rmSync', 'chmodSync', 'chownSync', 'fsyncSync']) t.mock.method(fs, key, no);
  for (const key of ['execSync', 'execFileSync', 'spawnSync', 'spawn', 'execFile', 'exec']) t.mock.method(cp, key, no);
  const r = recipe(raw, policy()); assert.equal(Object.keys(r.files).length, 21);
  for (const key of ['filesWritten', 'rootProvenanceVerified', 'profilePolicyVerified', 'accountValidated', 'targetNamespaceVerified',
    'gpuAccelerationVerified', 'codecOverlayVerified', 'independentRecoveryArmed', 'installable', 'readyForActivation',
    'servicesActivated', 'androidOperationsExecuted']) assert.equal(r.summary[key], false);
  assert.equal(r.summary.sourceRecipeCodeExecuted, true); assert.equal(r.summary.recipesDerived, true);
});

test('strings summaries and file inventory are immutable snapshots independent of caller objects', () => {
  const input = policy(), metadata = Buffer.from(raw), r = recipe(metadata, input), snapshot = JSON.stringify(r);
  assert(Object.isFrozen(r)); assert(Object.isFrozen(r.files)); assert(Object.isFrozen(r.summary));
  input.profiles.gpu = sha('different'); metadata.fill(0);
  assert.equal(JSON.stringify(r), snapshot);
  assert.equal(Reflect.set(r.files, 'extra', 'changed'), false);
  assert.equal(Reflect.set(r.summary, 'installable', true), false);
  assert.deepEqual(r, recipe(raw, policy()));
});

test('raw canonical and compact metadata generations remain distinct', () => {
  const normalized = normalizeRuntimeConfig(config), canonical = Buffer.from(encode(normalized));
  const a = recipe(raw, policy()), b = recipe(canonical, policy());
  assert.equal(a.summary.configurationDigest, b.summary.configurationDigest);
  assert.equal(a.summary.canonicalMetadataSha256, b.summary.canonicalMetadataSha256);
  assert.notEqual(a.summary.sourceMetadataSha256, b.summary.sourceMetadataSha256);
  assert.notEqual(a.summary.profileManifestSha256, b.summary.profileManifestSha256);
  assert.equal(b.summary.sourceMetadataSha256, b.summary.canonicalMetadataSha256);
  for (const name of Object.keys(a.files).filter(n => n !== 'bundle-manifest.json')) assert.equal(a.files[name], b.files[name]);
});

test('owned policy generation distinctness closed keys and secret bearing inputs refuse', () => {
  const good = policy();
  for (const bad of [null, [], { ...good, version: 1 }, { ...good, password: 'generated-secret' },
    { ...good, configurationDigest: 'a'.repeat(64) }, { ...good, originalConfigurationSha256: good.profiles.software },
    { ...good, profiles: { software: good.profiles.gpu, gpu: good.profiles.gpu } },
    { ...good, profiles: { ...good.profiles, extra: 'a'.repeat(64) } }]) assert.throws(() => recipe(raw, bad));
  for (const bad of [null, '', Buffer.alloc(0), Buffer.alloc(16385), Buffer.from([255]), Buffer.from('{invalid'),
    Buffer.from(JSON.stringify({ ...config, password: 'generated-secret' })), Buffer.from(JSON.stringify({ ...config, home: '/root' })),
    Buffer.from(JSON.stringify({ ...config, lanAddress: '8.8.8.8' }))]) assert.throws(() => recipe(bad, good));
});

test('headless notify PID generation and compositor device groups preserve accepted recipes', () => {
  const r = recipe(raw, policy()), generation = configurationDigest(config);
  assert.match(r.files['services/systemd/system/tesla-waydroid-compositor.service'], /SupplementaryGroups=render video\n/);
  assert.match(r.files['services/systemd/system/tesla-waydroid-headless-session.service'], /SupplementaryGroups=render video\n/);
  assert.match(r.files['headless-owned-runtime/systemd/60-droiddeck-headless.conf'], /Type=notify\nNotifyAccess=main\nExecStart=\n/);
  assert.match(r.files['headless-owned-runtime/systemd/60-droiddeck-headless.conf'], /python3 -I -B \/opt\/droiddeck\/headless-owned-runtime\/headless-owned-runtime.py/);
  assert(r.files['pid-compat/systemd/40-droiddeck-pid-compat.conf'].includes(generation));
  assert.deepEqual(JSON.parse(r.files['headless-owned-runtime/policy.json']), policy());
  for (const text of Object.values(r.files)) { assert(!text.includes('generated original')); assert(!text.includes('generated software')); }
});

test('primary config normalizer supplies defaults and Unicode IDNA without a new parser', () => {
  const c = { ...config, hostname: 'B\u00dcCHER.example' }, r = recipe(Buffer.from(JSON.stringify(c)), policy(c));
  const value = JSON.parse(r.files['runtime.json']);
  assert.equal(value.hostname, 'xn--bcher-kva.example'); assert.equal(value.home, '/home/alice'); assert.equal(value.port, 8443);
  assert.equal(r.summary.configurationDigest, configurationDigest(c));
});

test('existing independent service and headless source pins are retained', t => {
  const original = fs.readSync;
  // readBoundedRegular uses the actual descriptor: model corrupted accepted source bytes only in memory.
  t.mock.method(fs, 'readSync', function(fd, buffer, offset, length, position) {
    const n = original.call(fs, fd, buffer, offset, length, position);
    if (n) buffer[offset] = buffer[offset] === 47 ? 32 : 47;
    return n;
  });
  assert.throws(() => recipe(raw, policy()), /GPU service source pin changed/);
});

test('CLI inspects bounded stdin only returns digests and never prints settings or raw profile hashes', () => {
  const input = JSON.stringify({ metadata: raw.toString(), ownedPolicy: policy() });
  const r = cli(input, 'inspect', '-'); assert.equal(r.status, 0, r.stderr);
  assert.deepEqual(JSON.parse(r.stdout), recipe(raw, policy()).summary);
  for (const value of ['alice', config.lanAddress, policy().profiles.gpu, 'ExecStart=', '[Service]']) assert(!r.stdout.includes(value));
  assert.equal(r.stderr, '');
});

test('CLI rejects oversized malformed secret bearing metadata and arbitrary output options with fixed redaction', () => {
  for (const [input, args] of [['x'.repeat(65537), ['inspect', '-']], ['\ufffd', ['inspect', '-']],
    ['{}', ['inspect', '-']], [JSON.stringify({ metadata: raw.toString(), ownedPolicy: policy(), password: 'generated-secret' }), ['inspect', '-']],
    [JSON.stringify({ metadata: JSON.stringify({ ...config, password: 'generated-secret' }), ownedPolicy: policy() }), ['inspect', '-']],
    ['{}', ['inspect', '/private-value']], ['{}', ['build', '-']], ['{}', ['inspect', '-', '--output', '/private-value']]]) {
    const r = cli(input, ...args); assert.equal(r.status, 1); assert.equal(r.stdout, '');
    assert.equal(r.stderr.trim(), 'Owned install recipe refused; no files written');
  }
});
