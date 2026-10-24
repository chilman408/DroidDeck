// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path'), { createHash } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const base = require('./install-profile'), { configurationDigest } = require('./configuration-generation');
const config = { user: 'alice', uid: 1200, gid: 1300, lanAddress: '192.168.10.8' };
const sha = b => createHash('sha256').update(b).digest('hex');
function api() {
  assert.equal(typeof base.buildOwnedInstallProfile, 'function', 'Owned installation profile builder missing');
  assert.equal(typeof base.verifyOwnedInstallProfile, 'function', 'Owned installation profile verifier missing');
  return base;
}
function fixture(t, c = config) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'owned-install-profile-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const baseline = path.join(root, 'baseline'); base.buildInstallProfile(c, baseline);
  const receipt = { kind: 'protected-machine-gpu-profile-policy', configurationDigest: configurationDigest(c),
    metadataSha256: sha(fs.readFileSync(path.join(baseline, 'runtime.json'))),
    serviceManifestSha256: sha(fs.readFileSync(path.join(baseline, 'services/bundle-manifest.json'))),
    pidManifestSha256: sha(fs.readFileSync(path.join(baseline, 'pid-compat/bundle-manifest.json'))),
    originalConfigurationSha256: sha('generated original'), softwareConfigurationSha256: sha('generated software'),
    gpuConfigurationSha256: sha('generated GPU'), accountValidated: true, servicesActivated: false, installable: false,
    readyForActivation: false, androidOperationsExecuted: false, gpuProfilePolicyRegenerated: true, codecsVerified: true, independentRecoveryArmed: false };
  return { root, baseline, receipt, output: path.join(root, 'profile') };
}
function rewrite(output, manifest, relative) {
  const file = path.join(output, manifest), m = JSON.parse(fs.readFileSync(file)), bytes = fs.readFileSync(path.join(path.dirname(file), relative));
  Object.assign(m.files.find(r => r.path === relative), { size: bytes.length, sha256: sha(bytes) });
  fs.writeFileSync(file, JSON.stringify(m, null, 2) + '\n');
}
function repair(output, relative) {
  rewrite(output, 'headless-owned-runtime/bundle-manifest.json', relative);
  rewrite(output, 'bundle-manifest.json', 'headless-owned-runtime/' + relative);
  rewrite(output, 'bundle-manifest.json', 'headless-owned-runtime/bundle-manifest.json');
}

test('v3 binds twenty inert payloads and the exact original/software/GPU hashes without raw profiles', t => {
  const a = api(), f = fixture(t), m = a.buildOwnedInstallProfile(config, f.receipt, f.output);
  assert.equal(m.version, 3); assert.equal(m.files.length, 20);
  assert.deepEqual(m.profiles, { software: f.receipt.softwareConfigurationSha256, gpu: f.receipt.gpuConfigurationSha256 });
  assert.equal(m.originalConfigurationSha256, f.receipt.originalConfigurationSha256);
  assert.deepEqual(a.verifyOwnedInstallProfile(f.output, f.receipt), m); assert.deepEqual(a.verifyInstallProfile(f.output), m);
  assert(!fs.existsSync(path.join(f.output, 'headless-runtime')));
  for (const r of m.files) assert(!fs.readFileSync(path.join(f.output, r.path)).includes('generated original'));
  for (const k of ['accountValidated', 'installable', 'readyForActivation', 'servicesActivated', 'androidOperationsExecuted']) assert.equal(m[k], false);
});

test('legacy v1 and v2 manifests remain byte-identical to before this integration', t => {
  const f = fixture(t);
  assert.equal(sha(fs.readFileSync(path.join(f.baseline, 'bundle-manifest.json'))), '3c611a732e16130e71f3f7394710a7ec81aa3e2fee7a1157d1c7193cc4d29fa7');
  const other = path.join(f.root, 'legacy'); base.buildInstallProfile(config, other, 'a'.repeat(64));
  assert.equal(sha(fs.readFileSync(path.join(other, 'bundle-manifest.json'))), 'f32ea58d38d12b5d0d5db0144c22f1e9ba519cdfef11906432d1508a63ca9b71');
});

test('owned manifest is deterministic and changes for another pair or machine', t => {
  const a = api(), f = fixture(t), m = a.buildOwnedInstallProfile(config, f.receipt, f.output);
  assert.deepEqual(a.buildOwnedInstallProfile(config, f.receipt, path.join(f.root, 'same')), m);
  const changed = { ...f.receipt, gpuConfigurationSha256: 'a'.repeat(64) };
  assert.notDeepEqual(a.buildOwnedInstallProfile(config, changed, path.join(f.root, 'other')).files, m.files);
  const second = fixture(t, { ...config, uid: 1500 });
  assert.notEqual(a.buildOwnedInstallProfile({ ...config, uid: 1500 }, second.receipt, second.output).configurationDigest, m.configurationDigest);
});

test('invalid, stale, duplicate or credential-bearing receipts reject before output', t => {
  const a = api(), f = fixture(t);
  for (const change of [{ accountValidated: false }, { codecsVerified: false }, { password: 'generated-secret' },
    { configurationDigest: 'a'.repeat(64) }, { gpuConfigurationSha256: f.receipt.softwareConfigurationSha256 },
    { metadataSha256: null }]) {
    assert.throws(() => a.buildOwnedInstallProfile(config, { ...f.receipt, ...change }, f.output)); assert(!fs.existsSync(f.output));
  }
});

test('noncanonical source metadata hash stays distinct from the canonical package metadata', t => {
  const a = api(), f = fixture(t), raw = Buffer.from(JSON.stringify(JSON.parse(fs.readFileSync(path.join(f.baseline, 'runtime.json')))));
  const r = { ...f.receipt, metadataSha256: sha(raw) }, m = a.buildOwnedInstallProfile(config, r, f.output);
  assert.equal(m.sourceMetadataSha256, sha(raw));
  assert.notEqual(m.sourceMetadataSha256, sha(fs.readFileSync(path.join(f.output, 'runtime.json'))));
  assert.deepEqual(a.verifyOwnedInstallProfile(f.output, r), m);
  assert.throws(() => a.verifyOwnedInstallProfile(f.output, f.receipt));
});

test('external receipt binds all three child policies rather than only the owned pair', t => {
  const a = api(), f = fixture(t); a.buildOwnedInstallProfile(config, f.receipt, f.output);
  for (const k of ['serviceManifestSha256', 'pidManifestSha256', 'metadataSha256', 'gpuConfigurationSha256', 'originalConfigurationSha256']) {
    assert.throws(() => a.verifyOwnedInstallProfile(f.output, { ...f.receipt, [k]: 'a'.repeat(64) }));
  }
  assert.throws(() => a.verifyOwnedInstallProfile(f.output));
});

test('regeneration refuses rewritten executable or notification bytes even with repaired child/parent hashes', t => {
  const a = api();
  for (const relative of ['headless-owned-runtime.py', 'systemd/60-droiddeck-headless.conf']) {
    const f = fixture(t); a.buildOwnedInstallProfile(config, f.receipt, f.output);
    fs.appendFileSync(path.join(f.output, 'headless-owned-runtime', relative), '\n# generated changed bytes\n'); repair(f.output, relative);
    assert.throws(() => a.verifyInstallProfile(f.output));
  }
});

test('changed policy, mixed-machine child and version downgrade cannot hide behind repaired hashes', t => {
  const a = api(), f = fixture(t); a.buildOwnedInstallProfile(config, f.receipt, f.output);
  const filename = path.join(f.output, 'headless-owned-runtime/policy.json'), policy = JSON.parse(fs.readFileSync(filename));
  policy.profiles.gpu = 'a'.repeat(64); fs.writeFileSync(filename, JSON.stringify(policy, null, 2) + '\n'); repair(f.output, 'policy.json');
  assert.throws(() => a.verifyInstallProfile(f.output));
  const second = fixture(t); a.buildOwnedInstallProfile(config, second.receipt, second.output);
  const marker = path.join(second.output, 'bundle-manifest.json'), original = JSON.parse(fs.readFileSync(marker));
  for (const change of [{ version: 2 }, { version: 1 }, { profiles: { ...original.profiles, arbitrary: 'b'.repeat(64) } }, { accountValidated: true }]) {
    fs.writeFileSync(marker, JSON.stringify({ ...original, ...change })); assert.throws(() => a.verifyInstallProfile(second.output));
  }
  fs.writeFileSync(marker, JSON.stringify(original, null, 2) + '\n');
  const other = fixture(t, { ...config, uid: 1500 }); a.buildOwnedInstallProfile({ ...config, uid: 1500 }, other.receipt, other.output);
  for (const relative of ['policy.json', 'bundle-manifest.json']) {
    fs.copyFileSync(path.join(other.output, 'headless-owned-runtime', relative), path.join(second.output, 'headless-owned-runtime', relative));
    rewrite(second.output, 'bundle-manifest.json', 'headless-owned-runtime/' + relative);
  }
  assert.throws(() => a.verifyInstallProfile(second.output));
});

test('legacy-only packages cannot satisfy an externally required owned pair', t => {
  const a = api(), f = fixture(t); assert.throws(() => a.verifyOwnedInstallProfile(f.baseline, f.receipt));
  assert.throws(() => a.verifyInstallProfile(f.baseline, undefined, f.receipt));
  a.buildOwnedInstallProfile(config, f.receipt, f.output);
  assert.throws(() => a.verifyInstallProfile(f.output, f.receipt.gpuConfigurationSha256));
});

test('file consistency is not root provenance and persisted validation flags remain false', t => {
  const a = api(), f = fixture(t), other = { ...f.receipt, gpuConfigurationSha256: 'a'.repeat(64) };
  const m = a.buildOwnedInstallProfile(config, other, f.output);
  assert.deepEqual(a.verifyInstallProfile(f.output), m);
  assert.throws(() => a.verifyOwnedInstallProfile(f.output, f.receipt));
  assert.equal(m.accountValidated, false); assert.equal(m.readyForActivation, false);
});

test('existing outputs, linked namespaces, hardlinks and extra private files refuse', t => {
  const a = api(), f = fixture(t); a.buildOwnedInstallProfile(config, f.receipt, f.output);
  assert.throws(() => a.buildOwnedInstallProfile(config, f.receipt, f.output));
  const extra = path.join(f.output, 'headless-owned-runtime/private.pem'); fs.writeFileSync(extra, 'generated');
  assert.throws(() => a.verifyInstallProfile(f.output)); fs.unlinkSync(extra);
  fs.linkSync(path.join(f.output, 'headless-owned-runtime/policy.json'), path.join(f.root, 'hardlink'));
  assert.throws(() => a.verifyInstallProfile(f.output));
  const link = path.join(f.root, 'linked-parent'); fs.symlinkSync(f.root, link, process.platform === 'win32' ? 'junction' : 'dir');
  assert.throws(() => a.buildOwnedInstallProfile(config, f.receipt, path.join(link, 'new')));
});

test('read-only account check supports v3 but never claims GPU admission or changes stored flags', async t => {
  const a = api(), f = fixture(t); a.buildOwnedInstallProfile(config, f.receipt, f.output);
  const before = fs.readFileSync(path.join(f.output, 'bundle-manifest.json'));
  const r = await a.checkProfileAccount(f.output, { lookup: async () => 'alice:x:1200:1300:Generated:/home/alice:/bin/bash\n' });
  assert.equal(r.accountValidated, true); assert.equal(r.readyForActivation, false); assert.notEqual(r.ownedProfilePolicyMatched, true);
  assert.deepEqual(fs.readFileSync(path.join(f.output, 'bundle-manifest.json')), before);
});

test('verify-owned CLI accepts bounded receipt stdin but refuses stale, extra args and oversized input', t => {
  const a = api(), f = fixture(t); a.buildOwnedInstallProfile(config, f.receipt, f.output);
  const cli = (input, ...args) => spawnSync(process.execPath, [path.join(__dirname, 'install-profile.js'), ...args], { input, encoding: 'utf8', timeout: 10000 });
  const good = cli(JSON.stringify(f.receipt), 'verify-owned-headless', f.output, '-');
  assert.equal(good.status, 0, good.stderr); const r = JSON.parse(good.stdout);
  assert.equal(r.ownedProfilePolicyMatched, true); assert.equal(r.waydroidProfileVerified, false); assert.equal(r.accountValidated, false);
  assert(!good.stdout.includes(f.receipt.gpuConfigurationSha256));
  assert.equal(cli(JSON.stringify({ ...f.receipt, gpuConfigurationSha256: 'a'.repeat(64) }), 'verify-owned-headless', f.output, '-').status, 1);
  assert.equal(cli(' '.repeat(16385), 'verify-owned-headless', f.output, '-').status, 1);
  assert.equal(cli('{}', 'verify-owned-headless', f.output, '-', '--force').status, 1);
  const refused = path.join(f.root, 'refused');
  assert.equal(cli(JSON.stringify(f.receipt), 'build-owned-headless', path.join(f.baseline, 'runtime.json'), '-', refused).status, 1);
  assert(!fs.existsSync(refused));
});
