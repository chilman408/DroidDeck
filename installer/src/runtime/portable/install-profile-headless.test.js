// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path'), crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const { buildInstallProfile, verifyInstallProfile, checkProfileAccount } = require('./install-profile');
const config = { user: 'alice', uid: 1200, gid: 1300, lanAddress: '192.168.10.8' };
const sha = b => crypto.createHash('sha256').update(b).digest('hex');
const generatedProfile = '[waydroid]\narch = x86_64\n# generated-private-marker\n', profileHash = sha(generatedProfile);
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'headless-profile-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return { root, output: path.join(root, 'profile') };
}
function rewriteRecord(root, manifestPath, relative) {
  const filename = path.join(root, manifestPath), m = JSON.parse(fs.readFileSync(filename));
  const bytes = fs.readFileSync(path.join(path.dirname(filename), relative)), record = m.files.find(r => r.path === relative);
  record.size = bytes.length; record.sha256 = sha(bytes); fs.writeFileSync(filename, JSON.stringify(m, null, 2) + '\n');
}
function repairChildReceipts(output, relative) {
  rewriteRecord(output, 'headless-runtime/bundle-manifest.json', relative);
  rewriteRecord(output, 'bundle-manifest.json', 'headless-runtime/' + relative);
  rewriteRecord(output, 'bundle-manifest.json', 'headless-runtime/bundle-manifest.json');
}
test('headless profile binds nineteen payloads without copying raw Waydroid configuration', t => {
  const f = fixture(t), m = buildInstallProfile(config, f.output, profileHash);
  assert.equal(m.version, 2); assert.equal(m.files.length, 19); assert.equal(m.waydroidConfigurationSha256, profileHash);
  assert.deepEqual(verifyInstallProfile(f.output, profileHash), m);
  assert.deepEqual(verifyInstallProfile(f.output), m);
  const policy = JSON.parse(fs.readFileSync(path.join(f.output, 'headless-runtime/policy.json')));
  assert.equal(policy.configurationDigest, m.configurationDigest); assert.equal(policy.waydroidConfigurationSha256, profileHash);
  for (const record of m.files) assert(!fs.readFileSync(path.join(f.output, record.path)).includes('generated-private-marker'));
  for (const key of ['installable', 'readyForActivation', 'servicesActivated', 'androidOperationsExecuted', 'accountValidated']) assert.equal(m[key], false);
});
test('headless receipts are deterministic and differ for another Waydroid profile', t => {
  const f = fixture(t), a = buildInstallProfile(config, f.output, profileHash);
  const b = buildInstallProfile(config, path.join(f.root, 'same'), profileHash);
  const c = buildInstallProfile(config, path.join(f.root, 'other'), 'a'.repeat(64));
  assert.deepEqual(a, b); assert.notDeepEqual(a.files, c.files);
  assert.equal(a.configurationDigest, c.configurationDigest);
  assert.deepEqual(verifyInstallProfile(path.join(f.root, 'other'), 'a'.repeat(64)), c);
});
test('malformed profile hashes fail before creating output', t => {
  const f = fixture(t);
  for (const value of [null, '', 'A'.repeat(64), 'a'.repeat(63), 1, { sha256: profileHash }]) {
    assert.throws(() => buildInstallProfile(config, f.output, value)); assert(!fs.existsSync(f.output));
  }
});
test('rewriting headless executable and both file receipts cannot bypass source regeneration', t => {
  const f = fixture(t); buildInstallProfile(config, f.output, profileHash);
  fs.appendFileSync(path.join(f.output, 'headless-runtime/headless-runtime.py'), '\nraise RuntimeError("generated mutation")\n');
  repairChildReceipts(f.output, 'headless-runtime.py'); assert.throws(() => verifyInstallProfile(f.output));
});
test('rewriting nested policy and both file receipts cannot change the bound configuration', t => {
  const f = fixture(t); buildInstallProfile(config, f.output, profileHash);
  const filename = path.join(f.output, 'headless-runtime/policy.json'), policy = JSON.parse(fs.readFileSync(filename));
  policy.waydroidConfigurationSha256 = 'b'.repeat(64); fs.writeFileSync(filename, JSON.stringify(policy, null, 2) + '\n');
  repairChildReceipts(f.output, 'policy.json'); assert.throws(() => verifyInstallProfile(f.output));
});
test('a valid headless child for another machine cannot enter this profile', t => {
  const f = fixture(t); buildInstallProfile(config, f.output, profileHash);
  const other = path.join(f.root, 'other'); buildInstallProfile({ ...config, uid: 1400 }, other, profileHash);
  for (const relative of ['policy.json', 'bundle-manifest.json']) {
    fs.copyFileSync(path.join(other, 'headless-runtime', relative), path.join(f.output, 'headless-runtime', relative));
    rewriteRecord(f.output, 'bundle-manifest.json', 'headless-runtime/' + relative);
  }
  assert.throws(() => verifyInstallProfile(f.output));
});
test('external profile matching refuses stale hashes and base-only downgrade', t => {
  const f = fixture(t); buildInstallProfile(config, f.output, profileHash);
  assert.throws(() => verifyInstallProfile(f.output, 'b'.repeat(64)));
  const base = path.join(f.root, 'base'); buildInstallProfile(config, base);
  assert.throws(() => verifyInstallProfile(base, profileHash)); assert.equal(verifyInstallProfile(base).version, 1);
});
test('headless file links, extra keys and unrecognized top-level versions refuse', t => {
  const f = fixture(t); buildInstallProfile(config, f.output, profileHash);
  const filename = path.join(f.output, 'bundle-manifest.json'), original = fs.readFileSync(filename);
  for (const change of [{ version: 3 }, { version: 1 }, { profileValidated: true }, { waydroidConfigurationSha256: null }]) {
    fs.writeFileSync(filename, JSON.stringify({ ...JSON.parse(original), ...change })); assert.throws(() => verifyInstallProfile(f.output));
  }
  fs.writeFileSync(filename, original);
  const extra = path.join(f.output, 'headless-runtime/private.pem'); fs.writeFileSync(extra, 'generated');
  assert.throws(() => verifyInstallProfile(f.output)); fs.unlinkSync(extra);
  fs.linkSync(path.join(f.output, 'headless-runtime/policy.json'), path.join(f.root, 'hardlink'));
  assert.throws(() => verifyInstallProfile(f.output));
});
test('headless account inspection stays read-only and does not claim Waydroid host admission', async t => {
  const f = fixture(t); buildInstallProfile(config, f.output, profileHash);
  const before = fs.readFileSync(path.join(f.output, 'bundle-manifest.json'));
  const result = await checkProfileAccount(f.output, { lookup: async () => 'alice:x:1200:1300:Generated:/home/alice:/bin/bash\n' });
  assert.equal(result.accountValidated, true); assert.equal(result.readyForActivation, false);
  assert.notEqual(result.waydroidProfileVerified, true);
  assert.deepEqual(fs.readFileSync(path.join(f.output, 'bundle-manifest.json')), before);
});
test('verify-headless CLI matches actual file bytes and fails closed on stale input', t => {
  const f = fixture(t); buildInstallProfile(config, f.output, profileHash);
  const input = path.join(f.root, 'waydroid.cfg'); fs.writeFileSync(input, generatedProfile);
  const run = () => spawnSync(process.execPath, [path.join(__dirname, 'install-profile.js'), 'verify-headless', f.output, input],
    { encoding: 'utf8', timeout: 10000 });
  const good = run(); assert.equal(good.status, 0, good.stderr);
  const receipt = JSON.parse(good.stdout); assert.equal(receipt.waydroidProfileVerified, true);
  assert.equal(receipt.readyForActivation, false); assert(!good.stdout.includes('generated-private-marker'));
  fs.appendFileSync(input, '# stale\n'); const stale = run();
  assert.equal(stale.status, 1); assert.equal(stale.stdout, ''); assert(!stale.stderr.includes('generated-private-marker'));
  fs.writeFileSync(input, Buffer.alloc(65537, 65)); assert.equal(run().status, 1);
});
