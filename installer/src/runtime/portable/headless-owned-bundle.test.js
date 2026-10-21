// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path'), { createHash } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const { normalizeRuntimeConfig } = require('./runtime-config'), { configurationDigest } = require('./configuration-generation');
const config = { user: 'alice', uid: 1200, gid: 1300, lanAddress: '192.168.10.8' };
const sha = b => createHash('sha256').update(b).digest('hex');
const metadata = Buffer.from(JSON.stringify(normalizeRuntimeConfig(config), null, 2) + '\n');
function receipt() { return { kind: 'protected-machine-gpu-profile-policy', configurationDigest: configurationDigest(config),
  serviceManifestSha256: '1'.repeat(64), metadataSha256: sha(metadata), accountValidated: true,
  servicesActivated: false, installable: false, readyForActivation: false, androidOperationsExecuted: false,
  pidManifestSha256: '2'.repeat(64), originalConfigurationSha256: sha('generated original'),
  softwareConfigurationSha256: sha('generated software'), gpuConfigurationSha256: sha('generated GPU'),
  gpuProfilePolicyRegenerated: true, codecsVerified: true, independentRecoveryArmed: false }; }
function api() { return require('./headless-owned-bundle'); }
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'owned-headless-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return { root, output: path.join(root, 'component') };
}
test('owned component has six inert payloads and an exact two-profile policy while keeping accepted sources', t => {
  const f = fixture(t), m = api().buildOwnedHeadlessBundle(config, receipt(), f.output);
  assert.equal(m.kind, 'headless-owned-session-component'); assert.equal(m.files.length, 6);
  assert.deepEqual(api().verifyOwnedHeadlessBundle(config, receipt(), f.output), m);
  const policy = JSON.parse(fs.readFileSync(path.join(f.output, 'policy.json')));
  assert.equal(policy.version, 2); assert.deepEqual(Object.keys(policy.profiles).sort(), ['gpu', 'software']);
  assert.equal(policy.profiles.gpu, receipt().gpuConfigurationSha256);
  assert.equal(policy.profiles.software, receipt().softwareConfigurationSha256);
  assert.equal(sha(fs.readFileSync(path.join(f.output, 'headless-runtime.py'))), '2c0c34bf167411556e5a478669a86ec791a73e257cec117c596a0570af368d7b');
  for (const key of ['accountValidated', 'installable', 'readyForActivation', 'servicesActivated', 'androidOperationsExecuted']) assert.equal(m[key], false);
});
test('different pair or machine cannot be mixed and manifests are path-independent', t => {
  const f = fixture(t), m = api().buildOwnedHeadlessBundle(config, receipt(), f.output);
  assert.deepEqual(m, api().buildOwnedHeadlessBundle(config, receipt(), path.join(f.root, 'second')));
  assert.throws(() => api().verifyOwnedHeadlessBundle({ ...config, uid: 1400 }, receipt(), f.output));
  assert.throws(() => api().verifyOwnedHeadlessBundle(config, { ...receipt(), gpuConfigurationSha256: 'a'.repeat(64) }, f.output));
});
test('unverified, duplicate, credential-bearing or stale receipts fail before creating output', t => {
  const admission = api(), f = fixture(t);
  for (const change of [{ accountValidated: false }, { codecsVerified: false }, { readyForActivation: true },
    { gpuConfigurationSha256: receipt().softwareConfigurationSha256 }, { password: 'generated' },
    { configurationDigest: 'a'.repeat(64) }, { originalConfigurationSha256: null }]) {
    assert.throws(() => admission.buildOwnedHeadlessBundle(config, { ...receipt(), ...change }, f.output));
    assert(!fs.existsSync(f.output));
  }
});
test('rewritten manifest hashes cannot adopt altered runtime, policy or notification dropin', t => {
  for (const relative of ['headless-owned-runtime.py', 'policy.json', 'systemd/60-droiddeck-headless.conf']) {
    const f = fixture(t); api().buildOwnedHeadlessBundle(config, receipt(), f.output);
    const file = path.join(f.output, relative); fs.appendFileSync(file, '\n# generated changed bytes\n');
    const marker = path.join(f.output, 'bundle-manifest.json'), m = JSON.parse(fs.readFileSync(marker));
    const r = m.files.find(r => r.path === relative), bytes = fs.readFileSync(file); r.size = bytes.length; r.sha256 = sha(bytes);
    fs.writeFileSync(marker, JSON.stringify(m, null, 2) + '\n');
    assert.throws(() => api().verifyOwnedHeadlessBundle(config, receipt(), f.output));
  }
});
test('extra private files, hardlinks, linked namespaces and existing output are refused', t => {
  const f = fixture(t); api().buildOwnedHeadlessBundle(config, receipt(), f.output);
  assert.throws(() => api().buildOwnedHeadlessBundle(config, receipt(), f.output));
  const extra = path.join(f.output, 'key.pem'); fs.writeFileSync(extra, 'generated');
  assert.throws(() => api().verifyOwnedHeadlessBundle(config, receipt(), f.output)); fs.unlinkSync(extra);
  fs.linkSync(path.join(f.output, 'policy.json'), path.join(f.root, 'linked-file'));
  assert.throws(() => api().verifyOwnedHeadlessBundle(config, receipt(), f.output));
  const link = path.join(f.root, 'linked-parent'); fs.symlinkSync(f.root, link, process.platform === 'win32' ? 'junction' : 'dir');
  assert.throws(() => api().buildOwnedHeadlessBundle(config, receipt(), path.join(link, 'new')));
});
test('real verify CLI binds exact metadata bytes and supports bounded receipt stdin without leaking it', t => {
  const f = fixture(t); api().buildOwnedHeadlessBundle(config, receipt(), f.output);
  const input = path.join(f.root, 'config.json'); fs.writeFileSync(input, metadata);
  const cli = (...args) => spawnSync(process.execPath, [path.join(__dirname, 'headless-owned-bundle.js'), ...args], {
    input: JSON.stringify(receipt()), encoding: 'utf8', timeout: 10000
  });
  const good = cli('verify', input, '-', f.output); assert.equal(good.status, 0, good.stderr);
  assert.equal(JSON.parse(good.stdout).files, 6); assert(!good.stdout.includes(receipt().originalConfigurationSha256));
  fs.appendFileSync(input, ' '); const stale = cli('verify', input, '-', f.output);
  assert.equal(stale.status, 1); assert.equal(stale.stdout, '');
  assert.equal(cli('--activate').status, 1);
});

test('CLI refuses oversized stdin and root or outside-home build without creating output', t => {
  const f = fixture(t), input = path.join(f.root, 'config.json'); fs.writeFileSync(input, metadata);
  const cli = (mode, bytes) => spawnSync(process.execPath, [path.join(__dirname, 'headless-owned-bundle.js'), mode, input, '-', f.output], {
    input: bytes, encoding: 'utf8', timeout: 10000
  });
  assert.equal(cli('verify', ' '.repeat(16385)).status, 1);
  assert.equal(cli('build', JSON.stringify(receipt())).status, 1); assert(!fs.existsSync(f.output));
});

test('systemd verifies the owned notify target with base services without activation', { skip: process.platform !== 'linux' }, t => {
  const f = fixture(t), units = [], directory = path.join(f.root, 'units'); fs.mkdirSync(directory);
  const { createServicePlan } = require('./service-plan');
  const current = { user: 'hilman', uid: 1000, gid: 1000, lanAddress: '192.168.2.215' };
  for (const [name, text] of Object.entries(createServicePlan(current).files)) {
    if (!name.startsWith('systemd/system/')) continue;
    const file = path.join(directory, name.slice('systemd/system/'.length));
    fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, text);
    if (name.endsWith('.service')) units.push(file);
  }
  api().buildOwnedHeadlessBundle(current, { ...receipt(), configurationDigest: configurationDigest(current) }, f.output);
  const dropin = path.join(directory, 'tesla-waydroid-headless-session.service.d'); fs.mkdirSync(dropin);
  fs.copyFileSync(path.join(f.output, 'systemd/60-droiddeck-headless.conf'), path.join(dropin, '60-droiddeck-headless.conf'));
  const container = path.join(directory, 'waydroid-container.service');
  fs.copyFileSync('/usr/lib/systemd/system/waydroid-container.service', container); units.push(container);
  const result = spawnSync('/usr/bin/systemd-analyze', ['verify', '--man=no', ...units], { encoding: 'utf8', timeout: 10000,
    env: { ...process.env, SYSTEMD_UNIT_PATH: directory + ':/usr/lib/systemd/system:/lib/systemd/system', SYSTEMD_COLORS: '0' } });
  assert.equal(result.error, undefined); assert.equal(result.status, 0, result.stderr); assert.equal(result.stderr.trim(), '');
});
